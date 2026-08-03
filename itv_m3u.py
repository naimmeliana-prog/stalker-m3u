#!/usr/bin/env python3
"""Extrae una lista M3U de TV en directo (ITV) de un portal Stalker/MAG.

Configuracion en config.json (seccion "itv"):
  - es:  "all" (todos los generos ES) o "none"
  - fr:  "all" o "no_sport" (descarta deporte/PPV) o "none"
  - uk:  lista de palabras a conservar en generos UK (p.ej. GENERAL/DOCUMENTARY/NEWS)
  - ir:  "all" o "none"
La MAC se toma del secret MAG_MAC o de la clave "mac".
"""

import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time

from stalker_series_m3u import (
    PortalError,
    StalkerPortal,
    _escape_attr,
    _git_push,
    _write_m3u,
)

NAME_CLEAN_RE = re.compile(r"^[|]?\s*[A-Z]{2}[|]\s*")
FR_SPORT_RE = re.compile(
    r"SPORT|PPV|DAZN|LIGUE|L.EQUIPE|EURO|CDM|REPLAY|SOCCER|FOOT|V[ÉE]LOC|MOTO|RADIO|BOXE",
    re.IGNORECASE,
)
HEADER_RE = re.compile(r"^#+")
DEFAULT_UK = ["GENERAL", "DOCUMENTARY", "NEWS"]


def load_config():
    with open("config.json", encoding="utf-8") as fh:
        return json.load(fh)


def lang_prefix(title):
    t = str(title or "").strip()
    if t.startswith("|"):
        parts = t.split("|")
        return parts[1].strip() if len(parts) > 1 else ""
    if "|" in t:
        return t.split("|", 1)[0].strip()
    return ""


def clean_name(title):
    return NAME_CLEAN_RE.sub("", str(title or "").strip()).strip()


def _gzip_open(path, mode):
    import gzip

    return gzip.open(path, mode, encoding="utf-8") if path.endswith(".gz") else open(
        path, mode, encoding="utf-8"
    )


def _sig(portal, cfg):
    h = hashlib.sha256()
    h.update(
        "|".join(
            [
                portal.base_url,
                portal.mac,
                str(cfg.get("es") or "all"),
                str(cfg.get("fr") or "no_sport"),
                repr(sorted(cfg.get("uk") or DEFAULT_UK)),
                str(cfg.get("ir") or "none"),
            ]
        ).encode("utf-8")
    )
    return h.hexdigest()


def load_checkpoint(path, portal, cfg):
    if not path or not os.path.exists(path):
        return None
    try:
        with _gzip_open(path, "rt") as fh:
            ck = json.load(fh)
        if ck.get("sig") != _sig(portal, cfg):
            print("[!] Configuracion distinta: se descarta el checkpoint ITV")
            return None
        return ck
    except Exception:
        return None


def save_checkpoint(path, ck):
    tmp = path + ".tmp"
    with _gzip_open(tmp, "wt") as fh:
        json.dump(ck, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _request(portal, params, attempts=5):
    last = None
    for a in range(attempts):
        try:
            return portal._request(params)
        except PortalError as exc:
            last = exc
            time.sleep(1.0 * (a + 1))
    raise last


def get_genres(portal):
    out = _request(portal, {"type": "itv", "action": "get_genres", "JsHttpRequest": "1-xml"})
    js = out.get("js", out) if isinstance(out, dict) else out
    return js if isinstance(js, list) else []


def select_genres(genres, cfg):
    es_mode = cfg.get("es") or "all"
    fr_mode = cfg.get("fr") or "no_sport"
    uk_words = cfg.get("uk") or DEFAULT_UK
    ir_mode = cfg.get("ir") or "none"
    out = []
    for g in genres:
        title = str(g.get("title") or "")
        lp = lang_prefix(title)
        base = clean_name(title)
        if lp == "ES":
            if es_mode != "none":
                out.append(g)
        elif lp == "FR":
            if fr_mode == "none":
                continue
            if fr_mode == "no_sport" and FR_SPORT_RE.search(base):
                continue
            out.append(g)
        elif lp == "UK":
            if any(k.upper() in base.upper() for k in uk_words):
                out.append(g)
        elif lp == "IR":
            if ir_mode != "none":
                out.append(g)
    return out


def list_channels(portal, genre_id):
    items = []
    page = 1
    while page <= 200:
        out = _request(
            portal,
            {
                "type": "itv",
                "action": "get_ordered_list",
                "p": page,
                "genre": genre_id,
                "JsHttpRequest": "1-xml",
            },
        )
        js = portal._js(out)
        data = js.get("data", []) if isinstance(js, dict) else []
        if isinstance(data, dict):
            data = [i for g in data.values() for i in (g if isinstance(g, list) else [g])]
        data = [c for c in data if not HEADER_RE.match(str(c.get("name") or ""))]
        if not data:
            break
        items.extend(data)
        page += 1
    return items


def make_entry(ch, group):
    cid = str(ch.get("id") or "")
    name = clean_name(ch.get("name")) or cid
    logo = ch.get("logo") or ""
    raw = ch.get("cmd") or ""
    url = StalkerPortal._clean_cmd(raw)
    if not url:
        return None
    extinf = (
        '#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" group-title="%s",%s\n'
        % (
            _escape_attr(cid),
            _escape_attr(name),
            _escape_attr(str(logo)),
            _escape_attr(group),
            name,
        )
    )
    return extinf + url + "\n"


def main(argv=None):
    cfg_all = load_config()
    cfg = cfg_all.get("itv") or {}
    mac = os.environ.get("MAG_MAC") or cfg_all.get("mac")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return 1

    portal = StalkerPortal(
        cfg_all["portal"], mac, cfg.get("timeout", 15), not cfg_all.get("no_verify", False)
    )
    portal.handshake()
    print("[+] Token ITV OK: %s... endpoint %s" % (portal.token[:12], portal.entry))

    out_path = cfg.get("out", "itv.m3u")
    ck_path = cfg.get("checkpoint")
    progress = cfg.get("progress", "progress_itv.log")
    push_interval = cfg.get("push_interval", 0)
    threads = cfg.get("threads", 8)

    genres = select_genres(get_genres(portal), cfg)
    print("[+] Generos ITV seleccionados: %d" % len(genres))
    for g in genres:
        print("  %s\t%s" % (g.get("id"), clean_name(g.get("title"))))

    ck = load_checkpoint(ck_path, portal, cfg) if ck_path else None
    if ck is None:
        ck = {"sig": _sig(portal, cfg), "done": {}, "genres_done": []}
    done = ck["done"]
    genres_done = set(ck.get("genres_done") or [])
    entries = [v for v in done.values()]
    known_ids = set(done.keys())
    print("[+] Checkpoint ITV: %d canales ya listados" % len(done))

    pending = [g for g in genres if str(g.get("id")) not in genres_done]
    last_push = [time.time()]

    def save_and_push(force=False):
        if not (ck_path and push_interval):
            return
        if not force and time.time() - last_push[0] < push_interval:
            return
        last_push[0] = time.time()
        try:
            save_checkpoint(ck_path, ck)
            with open(progress, "a", encoding="utf-8") as fh:
                fh.write(
                    "%s itv=%d/%d genres=%d/%d\n"
                    % (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        len(done),
                        len(known_ids),
                        len(genres_done),
                        len(genres),
                    )
                )
        except Exception as exc:
            print("[!] Error guardando checkpoint ITV: %s" % exc)
        _git_push(ck_path, progress)

    def _process(genre):
        gid = str(genre.get("id"))
        group = clean_name(genre.get("title")) or gid
        try:
            channels = list_channels(portal, gid)
        except PortalError:
            return gid, group, []
        return gid, group, channels

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(_process, g): g for g in pending}
        for fut in concurrent.futures.as_completed(futures):
            gid, group, channels = fut.result()
            new = 0
            for ch in channels:
                mid = str(ch.get("id"))
                if mid in done:
                    continue
                e = make_entry(ch, group)
                if not e:
                    continue
                done[mid] = e
                entries.append(e)
                known_ids.add(mid)
                new += 1
            genres_done.add(gid)
            ck["genres_done"] = sorted(genres_done)
            print("[+] Genero %s '%s': %d canales (%d nuevos)" % (gid, group, len(channels), new))
            save_and_push()

    _write_m3u(out_path, entries)
    if ck_path:
        try:
            save_checkpoint(ck_path, ck)
        except Exception as exc:
            print("[!] Error guardando checkpoint ITV final: %s" % exc)
    if ck_path and push_interval:
        _git_push(out_path, ck_path)
    print("[+] ITV guardado en %s (%d canales)" % (out_path, len(entries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
