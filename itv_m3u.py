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
import unicodedata

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
DEFAULT_REMOVE = [
    "CARIBEAN",
    "REPETICION DE FUTBOL",
    "FRANCE LQ",
    "TOROS",
    "INFANTIL",
    "MUSICA",
    "LOCALES",
    "ENFANTS HD",
    "TIVIFY GOLD",
    "CANAL+ LIVE",
    "LALIGA+",
    "VIX PPV",
    "VIX PPV VIP",
    "RFEF PPV",
    "TV FOOTBALL PPV",
    "RAKUTEN TV",
    "MAX PPV",
    "MAX PPV VIP",
    "DAZN EXCLUSIVE",
    "DAZN PPV",
    "DAZN PPV BK",
    "MLS PPV",
    "CABLE TV SPORTS",
    "SOCCER PPV",
    "CDM 2026 REPLAY",
    "FRANCE SPORT VIP",
    "LIGUE 1+",
    "LIGUE 1+VIP",
    "L'EQUIPE LIVE",
]
DISPLAY_LANG = {"UK": "EN"}


def _norm(text):
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = t.encode("ascii", "ignore").decode("utf-8", "replace")
    return re.sub(r"\s+", " ", t.upper()).strip()


def load_config():
    path = os.environ.get("PORTAL_CONFIG_PATH") or "config.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def lang_prefix(title):
    t = str(title or "").strip()
    t_upper = t.upper()
    if re.search(r"\[(ES|SPAIN|ESP)\]|\bES\b|\bSPAIN\b|\bESPAÑA\b|\bESPANA\b|\bSPANISH\b|\bCASTELLANO\b", t_upper):
        return "ES"
    if re.search(r"\[(FR|FRENCH|FRA)\]|\bFR\b|\bFRANCE\b|\bFRENCH\b|\bFRANCAIS\b|\bFRANÇAIS\b", t_upper):
        return "FR"
    if re.search(r"\[(UK|EN|ENG|ENGLISH)\]|\bUK\b|\bEN\b|\bENGLISH\b|\bENGLAND\b|\bBRITISH\b|\bGB\b", t_upper):
        return "UK"
    match = re.match(r"^(ES|FR|UK|EN)\b", t_upper)
    if match:
        val = match.group(1)
        return "UK" if val == "EN" else val
    if t.startswith("|"):
        parts = t.split("|")
        val = parts[1].strip() if len(parts) > 1 else ""
        if val in ["ES", "FR", "UK", "EN"]:
            return "UK" if val == "EN" else val
    if "|" in t:
        val = t.split("|", 1)[0].strip()
        if val in ["ES", "FR", "UK", "EN"]:
            return "UK" if val == "EN" else val
    return ""


def clean_name(title):
    return NAME_CLEAN_RE.sub("", str(title or "").strip()).strip()


def _gzip_open(path, mode):
    import gzip

    base = path[:-4] if path.endswith(".tmp") else path
    return gzip.open(path, mode, encoding="utf-8") if base.endswith(".gz") else open(
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
                repr(sorted(cfg.get("remove") or DEFAULT_REMOVE)),
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
        if not isinstance(ck, dict) or not isinstance(ck.get("done"), (dict, list)):
            return None
        ck["sig"] = _sig(portal, cfg)
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
    remove = [_norm(k) for k in (cfg.get("remove") or DEFAULT_REMOVE)]
    out = []
    for g in genres:
        gid = str(g.get("id") or "").strip()
        title = str(g.get("title") or "")
        title_lower = title.lower().strip()
        if gid in ["*", "all", "0"] or title_lower in ["all", "todos", "all channels", "todos los canales", "tous"]:
            continue
        if any(r in _norm(title) for r in EXCLUDED_REGIONS):
            continue
        lp = lang_prefix(title)
        if lp not in ["ES", "FR", "UK"]:
            continue
        if lp == "UK":
            norm_t = _norm(title)
            if "DOCUMENTARY" not in norm_t and "DOCUMENTAL" not in norm_t:
                continue
        base = clean_name(title)
        if remove and any(n in _norm(base) for n in remove):
            continue
        out.append(g)
    return out


def list_channels(portal, genre_id):
    items = []
    raw_items = 0
    page = 1
    total = 0
    empty_tries = 0
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
        t = int(js.get("total_items") or 0) if isinstance(js, dict) else 0
        if t:
            total = t
        if not data:
            if page == 1:
                if total > 0:
                    raise PortalError("ITV %s: pagina 1 vacia (total=%d)" % (genre_id, total))
                empty_tries += 1
                if empty_tries >= 3:
                    break
                time.sleep(3 * empty_tries)
                continue
            break
        raw_items += len(data)
        data = [c for c in data if not HEADER_RE.match(str(c.get("name") or ""))]
        if not data:
            break
        items.extend(data)
        if total and raw_items >= total:
            break
        page += 1
    if total and raw_items < total:
        print("[+] ITV %s: %d/%d items recibidos (parcial)" % (genre_id, raw_items, total))
    if page > 200:
        print("[!] ITV %s: limite de paginas (200) alcanzado (%d items)" % (genre_id, raw_items))
    return items


EXCLUDED_REGIONS = [
    "LATINO", "LATAM", "MEXICO", "ARGENTINA", "COLOMBIA", "CHILE", "PERU", "VENEZUELA",
    "QUEBEC", "SUISSE", "SUIZA", "SWITZERLAND", "BELGIQUE", "BELGICA", "BELGIUM",
    "CANADA", "CANADIAN", "AFRICA", "AFRIQUE", "AFRICAN",
    "IRELAND", "IRLANDA", "IRISH", "SCOTLAND", "ESCOCIA", "SCOTTISH",
    "CARIBE", "CARIBEAN", "CARIBBEAN"
]


def resolve_channel(portal, cmd):
    params = {
        "type": "itv",
        "action": "create_link",
        "cmd": cmd,
        "JsHttpRequest": "1-xml",
    }
    try:
        out = _request(portal, params, attempts=3)
        js = portal._js(out)
        url = js.get("cmd") or out.get("cmd") or ""
        return StalkerPortal._clean_cmd(url)
    except Exception:
        return None


def make_entry(ch, group):
    cid = str(ch.get("id") or "")
    name = clean_name(ch.get("name")) or cid
    if any(r in _norm(name) for r in EXCLUDED_REGIONS) or any(r in _norm(group) for r in EXCLUDED_REGIONS):
        return None
    lp = lang_prefix(name)
    if lp == "UK":
        norm_n = _norm(name)
        norm_g = _norm(group)
        if "DOCUMENTARY" not in norm_n and "DOCUMENTAL" not in norm_n and "DOCUMENTARY" not in norm_g and "DOCUMENTAL" not in norm_g:
            return None
    elif lp and lp not in ["ES", "FR", "UK"]:
        return None
    logo = ch.get("logo") or ""
    url = ch.get("resolved_url") or (ch.get("cmd") or "").strip()
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
    if cfg_all.get("paused"):
        print("[+] Portal pausado. Omitiendo ITV...")
        return 0
    cfg = cfg_all.get("itv") or {}
    mac = cfg_all.get("mac") or os.environ.get("MAG_MAC")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return 1

    try:
        portal = StalkerPortal(
            cfg_all["portal"], mac, cfg.get("timeout", 15), not cfg_all.get("no_verify", False)
        )
        portal.handshake()
        print("[+] Token ITV OK: %s... endpoint %s" % (portal.token[:12], portal.entry))
    except PortalError as exc:
        print("[!] Error de conexion/handshake ITV (%s): %s" % (cfg_all.get("portal"), exc), file=sys.stderr)
        return 0

    config_dir = os.path.dirname(os.path.abspath(os.environ.get("PORTAL_CONFIG_PATH") or "config.json"))
    def resolve_path(p):
        if p and not os.path.isabs(p):
            return os.path.join(config_dir, p)
        return p

    out_path = resolve_path(cfg.get("out", "itv.m3u"))
    ck_path = resolve_path(cfg.get("checkpoint")) if cfg.get("checkpoint") else None
    progress = resolve_path(cfg.get("progress", "progress_itv.log"))
    push_interval = cfg.get("push_interval", 0)
    threads = cfg.get("threads", 8)
    resolve_live = cfg.get("resolve", False)

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

    def _on_emergency_signal(signum, frame):
        print("[!] Senal %d recibida (cancelacion/timeout). Guardando checkpoint ITV de emergencia..." % signum)
        if ck_path and ck:
            try:
                save_checkpoint(ck_path, ck)
                with open(progress, "a", encoding="utf-8") as fh:
                    fh.write("%s itv=%d/%d genres=%d/%d (EMERGENCIA)\n" % (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        len(done), len(known_ids), len(genres_done), len(genres)))
            except Exception as exc:
                print("[!] Error guardando checkpoint ITV de emergencia: %s" % exc)
        sys.exit(128 + signum)

    import signal
    signal.signal(signal.SIGTERM, _on_emergency_signal)
    signal.signal(signal.SIGINT, _on_emergency_signal)

    def _process(genre):
        gid = str(genre.get("id"))
        lp = lang_prefix(genre.get("title"))
        group = ("%s| %s" % (DISPLAY_LANG.get(lp, lp), clean_name(genre.get("title")))).strip() or gid
        try:
            channels = list_channels(portal, gid)
        except PortalError:
            return gid, group, None
        if resolve_live and channels:
            for ch in channels:
                cmd = (ch.get("cmd") or "").strip()
                if cmd:
                    resolved = resolve_channel(portal, cmd)
                    if resolved:
                        ch["resolved_url"] = resolved
        return gid, group, channels

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(_process, g): g for g in pending}
        for fut in concurrent.futures.as_completed(futures):
            gid, group, channels = fut.result()
            if channels is None:
                print("[!] Genero %s '%s': error de lista, se reintenta el proximo run" % (gid, group))
                continue
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
            ck["genres_done"] = sorted(x for x in genres_done if x is not None)
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
