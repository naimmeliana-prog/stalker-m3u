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

const STALKER_TOKENS = {};

async function resolveStalkerLink(portalUrl, mac, rawCmd, type = "itv") {
  const headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "X-User-Agent": "Model: MAG250; Link: WiFi",
    "Cookie": `mac=${mac}; stb_lang=en; timezone=Europe/London`
  };

  const key = `${portalUrl}|${mac}`;
  let cached = STALKER_TOKENS[key];
  let token = "";
  let activeEntry = "";

  if (cached && (Date.now() - cached.ts < 3600 * 1000)) {
    token = cached.token;
    activeEntry = cached.entry;
  } else {
    const entryPoints = [
      "/server/load.php",
      "/portal.php",
      "/c/server/load.php",
      "/stalker_portal/server/load.php",
    ];

    for (const entry of entryPoints) {
      try {
        const hsUrl = `${portalUrl.replace(/\/$/, "")}${entry}?type=stb&action=handshake&JsHttpRequest=1-xml`;
        const res = await fetch(hsUrl, { headers });
        if (res.ok) {
          const body = await res.json();
          const js = body.js || body;
          if (js && js.token) {
            token = js.token;
            activeEntry = entry;
            STALKER_TOKENS[key] = { token, entry, ts: Date.now() };
            break;
          }
        }
      } catch (e) {}
    }
  }

  if (!token) {
    throw new Error("Handshake failed");
  }

  headers["Cookie"] += `; token=${token}`;
  headers["Authorization"] = `Bearer ${token}`;

  let clCmd = rawCmd;
  if (!clCmd.startsWith("ffmpeg ") && !clCmd.startsWith("ffrt ")) {
    clCmd = "ffmpeg " + clCmd;
  }

  const clParams = new URLSearchParams({
    type: type,
    action: "create_link",
    cmd: clCmd,
    JsHttpRequest: "1-xml"
  });

  const clUrl = `${portalUrl.replace(/\/$/, "")}${activeEntry}?${clParams.toString()}`;
  const res = await fetch(clUrl, { headers });
  if (!res.ok) {
    throw new Error("Create link request failed: " + res.status);
  }
  const body = await res.json();
  const js = body.js || body;
  const rawUrl = js.cmd || body.cmd || "";
  if (!rawUrl) {
    throw new Error("No URL returned from create_link");
  }

  let cleanUrl = rawUrl.replace(/^(ffmpeg|ffrt)\s+/i, "").trim();
  cleanUrl = cleanUrl.replace(/^\d+:\d+\s+/, "").trim();
  return cleanUrl;
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

    let username = url.searchParams.get("username") || "";
    let postParams = null;
    if (request.method === "POST" && path.endsWith("player_api.php")) {
      try {
        const body = await request.clone().text();
        if (body) {
          postParams = new URLSearchParams(body);
          if (!username) {
            username = postParams.get("username") || "";
          }
        }
      } catch (e) {}
    }

    const parts = path.split("/").filter(Boolean);
    if (!username && parts.length >= 3) {
      const type = parts[0];
      if (type === "series" || type === "live" || type === "movie") {
        username = parts[1];
      }
    }

    let dataBase = (env && env.DATA_BASE) || DEFAULT_DATA_BASE;
    if (username && username !== "demo" && username !== "test") {
      const sanitized = username.replace(/[^a-zA-Z0-9_-]/g, "");
      if (sanitized) {
        const root = DEFAULT_DATA_BASE.replace(/\/xtream\/?$/, "/");
        dataBase = root + "portals/" + sanitized + "/xtream/";
      }
    }

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
      if (postParams) {
        params = new URLSearchParams([...params, ...postParams]);
      } else if (request.method === "POST") {
        try {
          const body = await request.text();
          if (body) {
            params = new URLSearchParams([...params, ...new URLSearchParams(body)]);
          }
        } catch (e) {}
      }
      return withCors(await handleApi(params, url.host, dataBase));
    }

    if (path.endsWith("get.php")) {
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
    if (parts.length >= 4 && parts[0] === "series") {
      const ep = decodeURIComponent(parts[3]).replace(/\.\w+$/, "");
      const streams = await fetchData(dataBase + "streams.json", 600);
      const target = streams[ep];
      if (!target) {
        return corsJson({}, 404);
      }
      let finalTarget = target;
      if (target.includes("localhost") || !target.startsWith("http")) {
        try {
          const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
          const configRes = await fetch(configUrl);
          if (configRes.ok) {
            const config = await configRes.json();
            if (config.portal && config.mac) {
              finalTarget = await resolveStalkerLink(config.portal, config.mac, target, "vod");
            }
          }
        } catch (e) {
          console.error("Dynamic series resolution failed:", e);
        }
      }
      if (env && env.PROXY_STREAM === "off") {
        return redirectCors(finalTarget);
      }
      return withCors(await streamProxy(finalTarget));
    }

    if (parts.length >= 4 && (parts[0] === "live" || parts[0] === "movie")) {
      const sid = decodeURIComponent(parts[3]).replace(/\.\w+$/, "");
      let finalTarget = "";
      
      if (parts[0] === "live") {
        try {
          const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
          const configRes = await fetch(configUrl);
          if (configRes.ok) {
            const config = await configRes.json();
            if (config.portal && config.mac) {
              const cmd = `ffmpeg http://localhost/ch/${sid}_`;
              finalTarget = await resolveStalkerLink(config.portal, config.mac, cmd, "itv");
            }
          }
        } catch (e) {
          console.error("Dynamic live stream resolution failed, using fallback:", e);
        }
      }

      if (!finalTarget) {
        const mapFile = parts[0] === "live" ? "live_urls.json" : "vod_urls.json";
        const urls = await fetchData(dataBase + mapFile, 600);
        const target = urls[sid];
        if (!target) {
          return corsJson({}, 404);
        }
        finalTarget = target;
        if (target.includes("localhost") || !target.startsWith("http")) {
          try {
            const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
            const configRes = await fetch(configUrl);
            if (configRes.ok) {
              const config = await configRes.json();
              if (config.portal && config.mac) {
                const type = parts[0] === "live" ? "itv" : "vod";
                finalTarget = await resolveStalkerLink(config.portal, config.mac, target, type);
              }
            }
          } catch (e) {
            console.error("Dynamic fallback stream resolution failed:", e);
          }
        }
      }

      if (!finalTarget) {
        return corsJson({}, 404);
      }
      if (env && env.PROXY_STREAM === "off") {
        return redirectCors(finalTarget);
      }
      return withCors(await streamProxy(finalTarget));
    }

    if (parts.length === 3) {
      const sid = decodeURIComponent(parts[2]).replace(/\.\w+$/, "");
      let finalTarget = "";

      try {
        const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
        const configRes = await fetch(configUrl);
        if (configRes.ok) {
          const config = await configRes.json();
          if (config.portal && config.mac) {
            const cmd = `ffmpeg http://localhost/ch/${sid}_`;
            finalTarget = await resolveStalkerLink(config.portal, config.mac, cmd, "itv");
          }
        }
      } catch (e) {}

      if (finalTarget) {
        return redirectCors(finalTarget);
      }

      const live = await fetchData(dataBase + "live_urls.json", 600);
      if (live[sid]) {
        let fallbackTarget = live[sid];
        if (fallbackTarget.includes("localhost") || !fallbackTarget.startsWith("http")) {
          try {
            const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
            const configRes = await fetch(configUrl);
            if (configRes.ok) {
              const config = await configRes.json();
              if (config.portal && config.mac) {
                fallbackTarget = await resolveStalkerLink(config.portal, config.mac, fallbackTarget, "itv");
              }
            }
          } catch (e) {}
        }
        return redirectCors(fallbackTarget);
      }

      const vod = await fetchData(dataBase + "vod_urls.json", 600);
      if (vod[sid]) {
        let fallbackTarget = vod[sid];
        if (fallbackTarget.includes("localhost") || !fallbackTarget.startsWith("http")) {
          try {
            const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
            const configRes = await fetch(configUrl);
            if (configRes.ok) {
              const config = await configRes.json();
              if (config.portal && config.mac) {
                fallbackTarget = await resolveStalkerLink(config.portal, config.mac, fallbackTarget, "vod");
              }
            }
          } catch (e) {}
        }
        return redirectCors(fallbackTarget);
      }

      const streams = await fetchData(dataBase + "streams.json", 600);
      if (streams[sid]) {
        let fallbackTarget = streams[sid];
        if (fallbackTarget.includes("localhost") || !fallbackTarget.startsWith("http")) {
          try {
            const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
            const configRes = await fetch(configUrl);
            if (configRes.ok) {
              const config = await configRes.json();
              if (config.portal && config.mac) {
                fallbackTarget = await resolveStalkerLink(config.portal, config.mac, fallbackTarget, "vod");
              }
            }
          } catch (e) {}
        }
        return redirectCors(fallbackTarget);
      }

      return corsJson({}, 404);
    }

    return corsJson({}, 404);
  },
};
