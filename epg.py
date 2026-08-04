#!/usr/bin/env python3
"""Genera un XMLTV (epg.xml.gz) a partir de la EPG corta del portal Stalker.

Lee los canales desde itv.m3u (tvg-id = id de canal del portal), pide
get_short_epg por canal y escribe un fichero XMLTV comprimido. Es reanudable
con epg_checkpoint.json.gz.

Configuracion en config.json (seccion "epg").
"""

import concurrent.futures
import datetime
import gzip
import json
import os
import re
import sys
import time
import xml.sax.saxutils

from stalker_series_m3u import PortalError, StalkerPortal, _git_push

EXTINF_RE = re.compile(r"#EXTINF:.*?\stvg-id=\"([^\"]*)\".*?(?:tvg-name=\"([^\"]*)\")?.*?(?:tvg-logo=\"([^\"]*)\")?")
TZ = "+0000"


def load_config():
    with open("config.json", encoding="utf-8") as fh:
        return json.load(fh)


def parse_itv(path):
    """Devuelve {ch_id: {"name":..., "logo":...}} a partir de itv.m3u."""
    channels = {}
    if not os.path.exists(path):
        return channels
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("#EXTINF:"):
                continue
            m = EXTINF_RE.search(line)
            if not m or not m.group(1):
                continue
            cid = m.group(1)
            name = (m.group(2) or "").strip()
            if not name:
                am = re.search(r",\s*([^,]+)\s*$", line)
                name = am.group(1).strip() if am else cid
            logo = (m.group(3) or "").strip()
            channels[cid] = {"name": name, "logo": logo}
    return channels


def _request_epg(portal, ch_id):
    last = None
    for attempt in range(4):
        try:
            out = portal._request(
                {
                    "type": "itv",
                    "action": "get_short_epg",
                    "ch_id": ch_id,
                    "JsHttpRequest": "1-xml",
                }
            )
            js = out.get("js", out) if isinstance(out, dict) else out
            return js if isinstance(js, list) else []
        except PortalError as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise last


def _ts_fmt(ts):
    try:
        return (
            datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime(
                "%Y%m%d%H%M%S"
            )
            + " " + TZ
        )
    except (TypeError, ValueError, OSError):
        return None


def _fmt_channel(cid, meta):
    out = ['<channel id="%s">' % xml.sax.saxutils.escape(cid)]
    out.append(
        "<display-name>%s</display-name>"
        % xml.sax.saxutils.escape(meta.get("name") or cid)
    )
    if meta.get("logo"):
        out.append('<icon src="%s"/>' % xml.sax.saxutils.quoteattr(meta["logo"])[1:-1])
    out.append("</channel>")
    return "".join(out)


def _fmt_programme(p):
    start = _ts_fmt(p.get("start_timestamp"))
    stop = _ts_fmt(p.get("stop_timestamp"))
    if not start:
        return None
    if not stop:
        try:
            stop = _ts_fmt(p.get("start_timestamp", 0) + int(p.get("duration") or 0))
        except (TypeError, ValueError):
            stop = None
    if not stop:
        return None
    name = xml.sax.saxutils.escape(str(p.get("name") or ""))
    if not name:
        return None
    descr = xml.sax.saxutils.escape(str(p.get("descr") or ""))
    out = [
        '<programme start="%s" stop="%s" channel="%s">' % (start, stop, xml.sax.saxutils.escape(str(p.get("ch_id") or ""))),
        "<title>%s</title>" % name,
    ]
    if descr:
        out.append("<desc>%s</desc>" % descr)
    out.append("</programme>")
    return "".join(out)


def _checkpoint_path(path):
    return path


def main(argv=None):
    cfg_all = load_config()
    cfg = cfg_all.get("epg") or {}
    mac = os.environ.get("MAG_MAC") or cfg_all.get("mac")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return 1

    itv_path = cfg.get("itv", "itv.m3u")
    channels = parse_itv(itv_path)
    if not channels:
        print("[!] %s: sin canales para EPG" % itv_path, file=sys.stderr)
        return 1

    out_path = cfg.get("out", "epg.xml.gz")
    ck_path = cfg.get("checkpoint")
    progress = cfg.get("progress", "progress_epg.log")
    push_interval = cfg.get("push_interval", 300)
    threads = cfg.get("threads", 8)
    timeout = cfg.get("timeout", 15)

    portal = StalkerPortal(
        cfg_all["portal"], mac, timeout, not cfg_all.get("no_verify", False)
    )
    portal.handshake()
    print("[+] Token EPG OK: %s..." % portal.token[:12])

    done = set()
    print("[+] Canales EPG: %d (ventana temporal: refetch completo cada run)" % len(channels))

    pending = list(channels)
    progs = {}  # ch_id -> list of xml strings

    def fetch_one(cid):
        try:
            items = _request_epg(portal, cid)
        except PortalError as exc:
            return cid, None, str(exc)
        lines = []
        for p in items:
            p = dict(p)
            p.setdefault("ch_id", cid)
            line = _fmt_programme(p)
            if line:
                lines.append(line)
        return cid, lines, None

    last_push = [time.time()]

    def save_and_push(force=False):
        if not (ck_path and push_interval):
            return
        if not force and time.time() - last_push[0] < push_interval:
            return
        last_push[0] = time.time()
        try:
            tmp = ck_path + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump({"done": sorted(done)}, fh)
            os.replace(tmp, ck_path)
            with open(progress, "a", encoding="utf-8") as fh:
                fh.write(
                    "%s epg=%d/%d\n"
                    % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), len(done), len(channels))
                )
        except Exception as exc:
            print("[!] Error guardando checkpoint EPG: %s" % exc)
        _git_push(ck_path, progress)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(fetch_one, cid): cid for cid in pending}
        for fut in concurrent.futures.as_completed(futures):
            cid, lines, err = fut.result()
            if err is not None:
                print("[!] Canal %s EPG fallo: %s" % (cid, err))
                continue
            if lines:
                progs[cid] = lines
            done.add(cid)
            save_and_push()
            print("[+] EPG: %d/%d canales" % (len(done), len(channels)))

    body = ['<?xml version="1.0" encoding="UTF-8"?>', "<tv>"]
    for cid, meta in channels.items():
        body.append(_fmt_channel(cid, meta))
        for line in progs.get(cid, []):
            body.append(line)
    body.append("</tv>")
    xml_text = "\n".join(body)

    tmp = out_path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.write(xml_text)
    os.replace(tmp, out_path)
    print("[+] EPG guardado en %s (%d canales, %d programas)" % (out_path, len(channels), sum(len(v) for v in progs.values())))

    if ck_path:
        try:
            tmp = ck_path + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump({"done": sorted(done)}, fh)
            os.replace(tmp, ck_path)
        except Exception as exc:
            print("[!] Error guardando checkpoint EPG final: %s" % exc)
    _git_push(out_path, ck_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
