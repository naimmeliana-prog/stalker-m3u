const DEFAULT_DATA_BASE = "https://naimmeliana-prog.github.io/stalker-m3u/xtream/";

const AUTH_USER = {
  auth: 1,
  auth_type: "Xtream Codes",
  status: "Active",
  exp_date: "2030-01-01 00:00:00",
  is_trial: "0",
  max_connections: "6",
};

const EMPTY_INFO = { seasons: [], episodes: {}, info: {} };

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function withCors(res) {
  res.headers.set("Access-Control-Allow-Origin", "*");
  res.headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.headers.set("Access-Control-Allow-Headers", "*");
  res.headers.set("Access-Control-Max-Age", "86400");
  return res;
}

function corsJson(data, status = 200) {
  return withCors(json(data, status));
}

function redirectCors(target, status = 302) {
  return new Response(null, {
    status,
    headers: {
      Location: target,
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "*",
      "Access-Control-Max-Age": "86400",
    },
  });
}

function parseUsers(raw) {
  if (!raw) {
    return null;
  }
  const map = {};
  String(raw)
    .split(/[\n,;]/)
    .forEach(function (line) {
      line = line.trim();
      if (!line) {
        return;
      }
      const idx = line.indexOf(":");
      if (idx > 0) {
        map[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
      }
    });
  return map;
}

function checkAuth(users, user, pass) {
  if (!users) {
    return true;
  }
  return Boolean(user) && users[user] === pass;
}

function streamAuth(parts, users) {
  if (!users) {
    return true;
  }
  if (parts.length >= 4) {
    return checkAuth(users, parts[1], parts[2]);
  }
  if (parts.length === 3) {
    return checkAuth(users, parts[0], parts[1]);
  }
  return true;
}

async function fetchData(url, cacheTtl = 600) {
  const cache = caches.default;
  const cacheKey = new Request(url);
  const cached = await cache.match(cacheKey);
  if (cached) {
    const ts = Number(cached.headers.get("x-xtream-ts") || 0);
    if (Date.now() - ts < cacheTtl * 1000) {
      return cached.json();
    }
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error("data fetch " + res.status);
  }
  const data = await res.json();
  const store = new Response(JSON.stringify(data), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "x-xtream-ts": String(Date.now()),
    },
  });
  await cache.put(cacheKey, store);
  return data;
}

function serverInfo(host) {
  return {
    url: host,
    port: "",
    https_port: "",
    server_protocol: "https",
    timezone: "Europe/Madrid",
  };
}

async function streamProxy(target) {
  const upstream = await fetch(target, { redirect: "follow" });
  const headers = new Headers();
  headers.set(
    "Content-Type",
    upstream.headers.get("Content-Type") || "application/octet-stream"
  );
  const cl = upstream.headers.get("Content-Length");
  if (cl) {
    headers.set("Content-Length", cl);
  }
  headers.set("Cache-Control", "no-store");
  return new Response(upstream.body, { status: 200, headers });
}

async function handleApi(params, host, dataBase) {
  const action = params.get("action") || "";
  if (!action) {
    return json({ user_info: AUTH_USER, server_info: serverInfo(host) });
  }
  if (action === "get_series_categories") {
    return json(await fetchData(dataBase + "series_categories.json"));
  }
  if (action === "get_series") {
    return json(await fetchData(dataBase + "series.json"));
  }
  if (action === "get_series_info") {
    const sid = params.get("series_id") || "";
    if (!sid) {
      return json(EMPTY_INFO);
    }
    try {
      return json(await fetchData(dataBase + "series/" + sid + ".json"));
    } catch (e) {
      return json(EMPTY_INFO);
    }
  }
  if (action === "get_live_categories") {
    return json(await fetchData(dataBase + "live_categories.json"));
  }
  if (action === "get_live_streams") {
    return json(await fetchData(dataBase + "live_streams.json"));
  }
  if (action === "get_vod_categories") {
    return json(await fetchData(dataBase + "vod_categories.json"));
  }
  if (action === "get_vod_streams") {
    return json(await fetchData(dataBase + "vod_streams.json"));
  }
  if (
    action === "get_short_epg" ||
    action === "get_simple_data_table" ||
    action === "get_all_epg" ||
    action === "get_epg_info"
  ) {
    return json([]);
  }
  return json({});
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const dataBase = (env && env.DATA_BASE) || DEFAULT_DATA_BASE;
    const users = parseUsers((env && env.XTRAM_USERS) || "");

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "*",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    if (path.endsWith("player_api.php")) {
      let params = new URLSearchParams(url.search);
      if (request.method === "POST") {
        try {
          const body = await request.text();
          if (body) {
            params = new URLSearchParams([...params, ...new URLSearchParams(body)]);
          }
        } catch (e) {
          /* cuerpo ilegible, se ignora */
        }
      }
      const user = params.get("username") || "";
      const pass = params.get("password") || "";
      if (!checkAuth(users, user, pass)) {
        return corsJson(
          { user_info: { auth: 0, status: "Disabled", exp_date: "1970-01-01 00:00:00" } },
          401
        );
      }
      return withCors(await handleApi(params, url.host, dataBase));
    }

    if (path.endsWith("get.php")) {
      const user = url.searchParams.get("username") || "";
      const pass = url.searchParams.get("password") || "";
      if (!checkAuth(users, user, pass)) {
        return corsJson({}, 401);
      }
      const mtype = url.searchParams.get("type") || "";
      if (mtype.includes("m3u")) {
        const root = dataBase.replace(/\/xtream\/?$/, "/");
        return redirectCors(root + "global.m3u");
      }
      return corsJson({ user_info: AUTH_USER, server_info: serverInfo(url.host) });
    }

    if (path.endsWith("xmltv.php")) {
      const root = dataBase.replace(/\/xtream\/?$/, "/");
      const res = await fetch(root + "epg.xml.gz");
      if (!res.ok) {
        return corsJson({}, 502);
      }
      try {
        const body = res.body.pipeThrough(new DecompressionStream("gzip"));
        return withCors(
          new Response(body, {
            headers: { "Content-Type": "application/xml; charset=utf-8" },
          })
        );
      } catch (e) {
        return withCors(
          new Response(res.body, {
            headers: { "Content-Type": "application/xml; charset=utf-8" },
          })
        );
      }
    }

    const parts = path.split("/").filter(Boolean);
    if (!streamAuth(parts, users)) {
      return corsJson({}, 401);
    }
    if (parts.length >= 4 && parts[0] === "series") {
      const ep = decodeURIComponent(parts[3]).replace(/\.\w+$/, "");
      const streams = await fetchData(dataBase + "streams.json", 600);
      const target = streams[ep];
      if (!target) {
        return corsJson({}, 404);
      }
      if (env && env.PROXY_STREAM === "off") {
        return redirectCors(target);
      }
      return withCors(await streamProxy(target));
    }

    if (parts.length >= 4 && (parts[0] === "live" || parts[0] === "movie")) {
      const sid = decodeURIComponent(parts[3]).replace(/\.\w+$/, "");
      const mapFile = parts[0] === "live" ? "live_urls.json" : "vod_urls.json";
      const urls = await fetchData(dataBase + mapFile, 600);
      const target = urls[sid];
      if (!target) {
        return corsJson({}, 404);
      }
      if (env && env.PROXY_STREAM === "off") {
        return redirectCors(target);
      }
      return withCors(await streamProxy(target));
    }

    if (parts.length === 3) {
      const sid = decodeURIComponent(parts[2]).replace(/\.\w+$/, "");
      const live = await fetchData(dataBase + "live_urls.json", 600);
      if (live[sid]) {
        return redirectCors(live[sid]);
      }
      const vod = await fetchData(dataBase + "vod_urls.json", 600);
      if (vod[sid]) {
        return redirectCors(vod[sid]);
      }
      const streams = await fetchData(dataBase + "streams.json", 600);
      if (streams[sid]) {
        return redirectCors(streams[sid]);
      }
      return corsJson({}, 404);
    }

    return corsJson({}, 404);
  },
};
