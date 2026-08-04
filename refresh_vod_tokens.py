#!/usr/bin/env python3
"""Refresca los play_token de las peliculas VOD en xtream/vod_urls.json.

Reconstruye el cmd (base64 JSON con stream_id) de cada pelicula y pide un
create_link nuevo al portal. Los tokens VOD tienen validez limitada; este
script los mantiene frescos sin re-fetchar los metadatos. Escribe
xtream/vod_urls.json y lo sube a git (necesita GITHUB_TOKEN para el push).
"""

import base64
import concurrent.futures
import json
import os
import re
import sys
import time

from stalker_series_m3u import PortalError, StalkerPortal, _git_push

STREAM_RE = re.compile(r"[?&]stream=([\w.]+)")


def load_config():
    with open("config.json", encoding="utf-8") as fh:
        return json.load(fh)


def ext_from_url(url):
    m = STREAM_RE.search(url or "")
    if m:
        base = m.group(1)
        dot = base.rfind(".")
        if dot > 0:
            return base[dot + 1:]
    return "mkv"


def make_cmd(stream_id, ext):
    payload = {
        "type": "movie",
        "stream_id": str(stream_id),
        "stream_source": None,
        "target_container": '["%s"]' % ext,
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def refresh_one(portal, stream_id, url, attempts=4):
    cmd = make_cmd(stream_id, ext_from_url(url))
    last = None
    for a in range(attempts):
        try:
            data = portal._request(
                {"type": "vod", "action": "create_link", "cmd": cmd, "JsHttpRequest": "1-xml"}
            )
            raw = portal._js(data).get("cmd") or ""
            fresh = StalkerPortal._clean_cmd(raw) or ""
            if fresh and "play_token=" in fresh:
                return fresh
            return None
        except PortalError as exc:
            last = exc
            time.sleep(0.6 * (a + 1))
    raise last


def main(argv=None):
    cfg_all = load_config()
    cfg = cfg_all.get("vod") or {}
    mac = os.environ.get("MAG_MAC") or cfg_all.get("mac")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return 1

    urls_path = "xtream/vod_urls.json"
    if not os.path.exists(urls_path):
        print("[!] %s no existe" % urls_path, file=sys.stderr)
        return 1

    portal = StalkerPortal(
        cfg_all["portal"], mac, cfg.get("timeout", 15), not cfg_all.get("no_verify", False)
    )
    portal.handshake()
    print("[+] Token refresh VOD OK: %s..." % portal.token[:12])

    with open(urls_path, "r", encoding="utf-8") as fh:
        urls = json.load(fh)
    print("[+] %d peliculas en %s" % (len(urls), urls_path))

    items = sorted(urls.items())
    threads = cfg.get("threads", 16)
    updated = 0
    failed = 0

    def _do(item):
        sid, url = item
        try:
            fresh = refresh_one(portal, sid, url)
        except PortalError:
            return sid, None
        if not fresh:
            return sid, None
        return sid, fresh

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(_do, it): it for it in items}
        for fut in concurrent.futures.as_completed(futures):
            sid, fresh = fut.result()
            if fresh and fresh != urls.get(sid):
                urls[sid] = fresh
                updated += 1
            else:
                failed += 1
            if (updated + failed) % 500 == 0:
                print("[+] %d/%d (ok=%d)" % (updated + failed, len(items), updated))

    tmp = urls_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(urls, fh, ensure_ascii=False)
    os.replace(tmp, urls_path)
    print("[+] vod_urls.json actualizado: %d refrescadas, %d sin cambio" % (updated, failed))

    if updated:
        _git_push(urls_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
