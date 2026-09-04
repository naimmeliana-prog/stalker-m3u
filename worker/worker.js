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

async function streamProxy(target, clientRequest, portalUrl = "", mac = "") {
  const reqHeaders = new Headers();
  
  if (clientRequest) {
    const range = clientRequest.headers.get("Range");
    if (range) {
      reqHeaders.set("Range", range);
    }
  }
  
  reqHeaders.set("User-Agent", "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 sb2ed89f0 Safari/533.3");
  if (mac) {
    reqHeaders.set("Cookie", `mac=${mac}; stb_lang=en`);
  }

  let currUrl = target;
  let upstream = null;
  for (let i = 0; i < 5; i++) {
    upstream = await fetch(currUrl, { 
      headers: reqHeaders,
      redirect: "manual" 
    });
    if (upstream.status >= 300 && upstream.status < 400) {
      const loc = upstream.headers.get("Location");
      if (loc) {
        currUrl = loc.startsWith("http") ? loc : new URL(loc, currUrl).href;
        continue;
      }
    }
    break;
  }

  const responseHeaders = new Headers();
  const copyHeaders = [
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Cache-Control"
  ];
  
  for (const h of copyHeaders) {
    const val = upstream.headers.get(h);
    if (val) {
      responseHeaders.set(h, val);
    }
  }

  return new Response(upstream.body, { 
    status: upstream.status, 
    headers: responseHeaders 
  });
}

const STALKER_TOKENS = {};

async function resolveStalkerLink(portalUrl, mac, rawCmd, type = "itv") {
  if (!rawCmd) return "";
  const trimmed = rawCmd.trim();
  const isApiLink = trimmed.includes("action=create_link") || trimmed.includes("load.php") || trimmed.includes("portal.php") || trimmed.includes("play_token=") || trimmed.includes("token=");
  if (!isApiLink && (trimmed.startsWith("http://") || trimmed.startsWith("https://")) && !trimmed.includes("localhost") && !trimmed.includes("127.0.0.1")) {
    return trimmed;
  }

  let clCmd = trimmed;
  let epSeries = "";
  if (isApiLink) {
    try {
      const parsedUrl = new URL(trimmed);
      const pCmd = parsedUrl.searchParams.get("cmd");
      const pSeries = parsedUrl.searchParams.get("series");
      const pType = parsedUrl.searchParams.get("type");
      if (pCmd) clCmd = pCmd;
      if (pSeries) epSeries = pSeries;
      if (pType && type !== "series") type = pType;
    } catch (e) {}
  }

  const headers = {
    "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 sb2ed89f0 Safari/533.3",
    "X-User-Agent": "Model: MAG250; Link: WiFi",
    "Cookie": `mac=${mac}; stb_lang=en; timezone=Europe/London`
  };

  const key = `${portalUrl}|${mac}`;
  let cached = STALKER_TOKENS[key];
  let token = "";
  let activeEntry = "";

  const cleanBase = portalUrl.replace(/\/(c|server|portal\.php|stalker_portal).*$/i, "").replace(/\/$/, "");

  if (cached && (Date.now() - cached.ts < 3600 * 1000)) {
    token = cached.token;
    activeEntry = cached.entry;
  } else {
    const entryPoints = [
      portalUrl.endsWith("/c") ? "/c/server/load.php" : "/server/load.php",
      "/server/load.php",
      "/c/server/load.php",
      "/portal.php",
      "/c/portal.php",
      "/stalker_portal/server/load.php",
    ];

    for (const entry of entryPoints) {
      try {
        const hsUrl = `${cleanBase}${entry}?type=stb&action=handshake&JsHttpRequest=1-xml`;
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
    throw new Error("Handshake failed for " + portalUrl);
  }

  headers["Cookie"] += `; token=${token}`;
  headers["Authorization"] = `Bearer ${token}`;

  if (!clCmd.startsWith("ffmpeg ") && !clCmd.startsWith("ffrt ") && !clCmd.startsWith("http://") && !clCmd.startsWith("https://")) {
    clCmd = "ffmpeg " + clCmd;
  }

  const typesToTry = type === "series" ? ["series", "vod"] : [type, "vod", "itv"];
  let rawUrl = "";
  for (const t of typesToTry) {
    const p = {
      type: t,
      action: "create_link",
      cmd: clCmd,
      JsHttpRequest: "1-xml"
    };
    if (epSeries) {
      p.series = epSeries;
    }
    const clParams = new URLSearchParams(p);
    const clUrl = `${cleanBase}${activeEntry}?${clParams.toString()}`;
    try {
      const res = await fetch(clUrl, { headers });
      if (res.ok) {
        const body = await res.json();
        const js = body.js || body;
        const u = js.cmd || body.cmd || "";
        if (u) {
          rawUrl = u;
          break;
        }
      }
    } catch (e) {}
  }

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
    const list = await fetchData(dataBase + "series.json");
    const catId = params.get("category_id");
    if (catId && Array.isArray(list)) {
      return json(list.filter((s) => String(s.category_id) === String(catId)));
    }
    return json(list);
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
    const list = await fetchData(dataBase + "live_streams.json");
    const catId = params.get("category_id");
    if (catId && Array.isArray(list)) {
      return json(list.filter((l) => String(l.category_id) === String(catId)));
    }
    return json(list);
  }
  if (action === "get_vod_categories") {
    return json(await fetchData(dataBase + "vod_categories.json"));
  }
  if (action === "get_vod_streams") {
    const list = await fetchData(dataBase + "vod_streams.json");
    const catId = params.get("category_id");
    if (catId && Array.isArray(list)) {
      return json(list.filter((v) => String(v.category_id) === String(catId)));
    }
    return json(list);
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


    if (parts.length >= 4 && (parts[0] === "live" || parts[0] === "movie" || parts[0] === "series")) {
      const sid = decodeURIComponent(parts[3]).replace(/\.\w+$/, "");
      let mapFile = "live_urls.json";
      if (parts[0] === "movie") mapFile = "vod_urls.json";
      if (parts[0] === "series") mapFile = "streams.json";

      const urls = await fetchData(dataBase + mapFile, 600);
      const target = urls[sid];
      if (!target) {
        return corsJson({}, 404);
      }

      let finalTarget = target;
      let portal = "";
      let mac = "";
      try {
        const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
        const configRes = await fetch(configUrl);
        if (configRes.ok) {
          const config = await configRes.json();
          portal = config.portal || "";
          mac = config.mac || "";
        }
      } catch (e) {}

      if ((!portal || !mac) && target && target.startsWith("http")) {
        try {
          const targetHost = new URL(target).host;
          const portalList = ["backup", "devtv", "greatott", "ipfr", "oktay", "sny57", "tfx1", "tvmoderne", "wave"];
          const root = dataBase.replace(/\/xtream\/?$/, "/");
          for (const pName of portalList) {
            try {
              const pCfgRes = await fetch(root + "portals/" + pName + "/config.json");
              if (pCfgRes.ok) {
                const pCfg = await pCfgRes.json();
                if (pCfg.portal && pCfg.portal.includes(targetHost)) {
                  portal = pCfg.portal;
                  mac = pCfg.mac || "";
                  break;
                }
              }
            } catch (e) {}
          }
        } catch (e) {}
      }

      if (portal && mac) {
        try {
          const type = parts[0] === "live" ? "itv" : (parts[0] === "series" ? "series" : "vod");
          const freshTarget = await resolveStalkerLink(portal, mac, target, type);
          if (freshTarget) {
            finalTarget = freshTarget;
          }
        } catch (e) {
          console.error("Dynamic stream resolution error, using target fallback:", e);
        }
      }

      if (!finalTarget) {
        return corsJson({}, 404);
      }

      if (parts[0] === "live") {
        return withCors(await streamProxy(finalTarget, request, portal, mac));
      }

      if (env && env.PROXY_STREAM === "on") {
        return withCors(await streamProxy(finalTarget, request, portal, mac));
      }
      return redirectCors(finalTarget);
    }

    if (parts.length === 3) {
      const sid = decodeURIComponent(parts[2]).replace(/\.\w+$/, "");

      // 1. Check live_urls.json
      const live = await fetchData(dataBase + "live_urls.json", 600);
      if (live[sid]) {
        let fallbackTarget = live[sid];
        let portal = "";
        let mac = "";
        try {
          const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
          const configRes = await fetch(configUrl);
          if (configRes.ok) {
            const config = await configRes.json();
            portal = config.portal || "";
            mac = config.mac || "";
            if (config.portal && config.mac) {
              const fresh = await resolveStalkerLink(config.portal, config.mac, fallbackTarget, "itv");
              if (fresh) fallbackTarget = fresh;
            }
          }
        } catch (e) {}
        if (env && env.PROXY_STREAM === "on") {
          return withCors(await streamProxy(fallbackTarget, request, portal, mac));
        }
        return redirectCors(fallbackTarget);
      }

      // 2. Check vod_urls.json
      const vod = await fetchData(dataBase + "vod_urls.json", 600);
      if (vod[sid]) {
        let fallbackTarget = vod[sid];
        let portal = "";
        let mac = "";
        try {
          const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
          const configRes = await fetch(configUrl);
          if (configRes.ok) {
            const config = await configRes.json();
            portal = config.portal || "";
            mac = config.mac || "";
            if (config.portal && config.mac) {
              const fresh = await resolveStalkerLink(config.portal, config.mac, fallbackTarget, "vod");
              if (fresh) fallbackTarget = fresh;
            }
          }
        } catch (e) {}
        if (env && env.PROXY_STREAM === "on") {
          return withCors(await streamProxy(fallbackTarget, request, portal, mac));
        }
        return redirectCors(fallbackTarget);
      }

      // 3. Check streams.json (Xtream Series)
      const streams = await fetchData(dataBase + "streams.json", 600);
      if (streams[sid]) {
        let fallbackTarget = streams[sid];
        let portal = "";
        let mac = "";
        try {
          const configUrl = dataBase.replace(/\/xtream\/?$/, "/config.json");
          const configRes = await fetch(configUrl);
          if (configRes.ok) {
            const config = await configRes.json();
            portal = config.portal || "";
            mac = config.mac || "";
            if (config.portal && config.mac) {
              const fresh = await resolveStalkerLink(config.portal, config.mac, fallbackTarget, "series");
              if (fresh) fallbackTarget = fresh;
            }
          }
        } catch (e) {}
        if (env && env.PROXY_STREAM === "on") {
          return withCors(await streamProxy(fallbackTarget, request, portal, mac));
        }
        return redirectCors(fallbackTarget);
      }

      return corsJson({}, 404);
    }

    return corsJson({}, 404);
  },
};
