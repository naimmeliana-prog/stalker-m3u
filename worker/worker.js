const DEFAULT_DATA_BASE = "https://naimmeliana-prog.github.io/stalker-m3u/xtream/";

const AUTH_USER = {
  auth: 1,
  auth_type: "Xtream Codes",
  status: "Active",
  exp_date: "2030-01-01 00:00:00",
  is_trial: "0",
  max_connections: "1",
};

const EMPTY_INFO = { seasons: [], episodes: {}, info: {} };

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
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
      return handleApi(params, url.host, dataBase);
    }

    if (path.endsWith("get.php")) {
      const mtype = url.searchParams.get("type") || "";
      if (mtype.includes("m3u")) {
        const root = dataBase.replace(/\/xtream\/?$/, "/");
        return Response.redirect(root + "global.m3u", 302);
      }
      return json({ user_info: AUTH_USER, server_info: serverInfo(url.host) });
    }

    if (path.endsWith("xmltv.php")) {
      return new Response('<?xml version="1.0" encoding="UTF-8"?><tv></tv>', {
        headers: { "Content-Type": "application/xml; charset=utf-8" },
      });
    }

    const parts = path.split("/").filter(Boolean);
    if (parts.length >= 4 && parts[0] === "series") {
      const ep = parts[3].replace(/\.\w+$/, "");
      const streams = await fetchData(dataBase + "streams.json", 600);
      const target = streams[ep];
      if (!target) {
        return json({}, 404);
      }
      return Response.redirect(target, 302);
    }

    if (parts.length >= 4 && (parts[0] === "live" || parts[0] === "movie")) {
      const sid = parts[3].replace(/\.\w+$/, "");
      const mapFile = parts[0] === "live" ? "live_urls.json" : "vod_urls.json";
      const urls = await fetchData(dataBase + mapFile, 600);
      const target = urls[sid];
      if (!target) {
        return json({}, 404);
      }
      return Response.redirect(target, 302);
    }

    return json({}, 404);
  },
};
