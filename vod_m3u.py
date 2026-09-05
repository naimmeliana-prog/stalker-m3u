#!/usr/bin/env python3
"""Extrae una lista M3U de peliculas VOD (categorias ES/FR) de un portal Stalker/MAG.

Configuracion en config.json (seccion "vod"); la MAC se toma del secret
MAG_MAC o de la clave "mac". Escribe vod.m3u y puede reanudarse con
vod_checkpoint.json.gz.
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

DEFAULT_EXCLUDE = [
    "LATINO", "LATAM", "MEXICO", "ARGENTINA", "COLOMBIA", "CHILE", "PERU", "VENEZUELA",
    "QUEBEC", "SUISSE", "SUIZA", "SWITZERLAND", "BELGIQUE", "BELGICA", "BELGIUM",
    "CANADA", "CANADIAN", "AFRICA", "AFRIQUE", "AFRICAN",
    "IRELAND", "IRLANDA", "IRISH", "SCOTLAND", "ESCOCIA", "SCOTTISH",
    "CARIBE", "CARIBEAN", "CARIBBEAN",
    "SPORT", "TELENOVELA", "DOCUMENTAL", "DOCUMENTAIRE"
]
DEFAULT_REMOVE_FR = ["PRIME +", "NETFLIX", "DE NOËL", "FILMOGRAPHIE LOUIS DE FUNES", "CHUCK NORRIS"]
NAME_CLEAN_RE = re.compile(r"^[|]?\s*[A-Z]{2}[|]\s*")


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
                repr(sorted(cfg.get("languages") or ["ES", "FR", "UK"])),
                repr(sorted(cfg.get("exclude") or DEFAULT_EXCLUDE)),
                repr(sorted(cfg.get("remove_fr") or DEFAULT_REMOVE_FR)),
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
        if not isinstance(ck, dict) or not isinstance(ck.get("done"), dict):
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


def get_categories(portal):
    out = _request(
        portal, {"type": "vod", "action": "get_categories", "JsHttpRequest": "1-xml"}
    )
    js = out.get("js", out) if isinstance(out, dict) else out
    if isinstance(js, dict):
        cats = js.get("data", [])
        if isinstance(cats, dict):
            cats = [i for g in cats.values() for i in (g if isinstance(g, list) else [g])]
        return cats if isinstance(cats, list) else []
    return js if isinstance(js, list) else []


def select_categories(cats, languages, exclude, remove_fr=None):
    needles = [_norm(k) for k in exclude]
    out = []
    for c in cats:
        cid = str(c.get("id") or "").strip()
        title = str(c.get("title") or "")
        title_lower = title.lower().strip()
        if cid in ["*", "all", "0"] or title_lower in ["all", "todos", "all movies", "todas las peliculas", "tous"]:
            continue
        if any(r in _norm(title) for r in needles):
            continue
        lp = lang_prefix(title)
        if lp not in ["ES", "FR"]:
            continue
        out.append(c)
    return out


def list_movies(portal, cid):
    items = []
    page = 1
    total = 0
    empty_tries = 0
    while page <= 500:
        out = _request(
            portal,
            {
                "type": "vod",
                "action": "get_ordered_list",
                "p": page,
                "category": cid,
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
                    raise PortalError("VOD %s: pagina 1 vacia (total=%d)" % (cid, total))
                empty_tries += 1
                if empty_tries >= 3:
                    break
                time.sleep(3 * empty_tries)
                continue
            break
        items.extend(data)
        if total and len(items) >= total:
            break
        page += 1
    if total and len(items) < total:
        print("[+] VOD %s: %d/%d items recibidos (parcial)" % (cid, len(items), total))
    if page > 500:
        print("[!] VOD %s: limite de paginas (500) alcanzado (%d items)" % (cid, len(items)))
    return items


def resolve_movie(portal, cmd):
    data = _request(
        portal,
        {"type": "vod", "action": "create_link", "cmd": cmd, "JsHttpRequest": "1-xml"},
    )
    raw = portal._js(data).get("cmd") or ""
    return StalkerPortal._clean_cmd(raw) or None


def _resolve_or_none(portal, movie):
    cmd = movie.get("cmd")
    if not cmd:
        return None
    return resolve_movie(portal, cmd)


def make_entry(movie, url, group):
    mid = str(movie.get("id") or "")
    name = clean_name(movie.get("name")) or mid
    banned = [
        "LATINO", "LATAM", "MEXICO", "ARGENTINA", "COLOMBIA", "CHILE", "PERU", "VENEZUELA",
        "QUEBEC", "SUISSE", "SUIZA", "SWITZERLAND", "BELGIQUE", "BELGICA", "BELGIUM",
        "CANADA", "CANADIAN", "AFRICA", "AFRIQUE", "AFRICAN",
        "IRELAND", "IRLANDA", "IRISH", "SCOTLAND", "ESCOCIA", "SCOTTISH",
        "CARIBE", "CARIBEAN", "CARIBBEAN"
    ]
    if any(r in _norm(name) for r in banned) or any(r in _norm(group) for r in banned):
        return None
    lp = lang_prefix(name)
    if lp and lp not in ["ES", "FR"]:
        return None
    logo = movie.get("pic") or movie.get("screenshot_uri") or ""
    extinf = (
        '#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" group-title="%s",%s\n'
        % (
            _escape_attr(mid),
            _escape_attr(name),
            _escape_attr(str(logo)),
            _escape_attr(group),
            name,
        )
    )
    return extinf + url + "\n"


def main(argv=None):
    cfg_all = load_config()
    cfg = cfg_all.get("vod") or {}
    mac = cfg_all.get("mac") or os.environ.get("MAG_MAC")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return 1

    try:
        portal = StalkerPortal(
            cfg_all["portal"], mac, cfg.get("timeout", 15), not cfg_all.get("no_verify", False)
        )
        portal.handshake()
        print("[+] Token VOD OK: %s... endpoint %s" % (portal.token[:12], portal.entry))
    except PortalError as exc:
        print("[!] Error de conexion/handshake VOD (%s): %s" % (cfg_all.get("portal"), exc), file=sys.stderr)
        return 0

    languages = cfg.get("languages") or ["ES", "FR"]
    exclude = cfg.get("exclude") or DEFAULT_EXCLUDE
    remove_fr = cfg.get("remove_fr") or DEFAULT_REMOVE_FR

    config_dir = os.path.dirname(os.path.abspath(os.environ.get("PORTAL_CONFIG_PATH") or "config.json"))
    def resolve_path(p):
        if p and not os.path.isabs(p):
            return os.path.join(config_dir, p)
        return p

    out_path = resolve_path(cfg.get("out", "vod.m3u"))
    ck_path = resolve_path(cfg.get("checkpoint")) if cfg.get("checkpoint") else None
    progress = resolve_path(cfg.get("progress", "progress_vod.log"))
    push_interval = cfg.get("push_interval", 0)
    threads = cfg.get("threads", 8)

    selected = select_categories(get_categories(portal), languages, exclude, remove_fr)
    print("[+] Categorias VOD seleccionadas: %d" % len(selected))
    for c in selected:
        print("  %s\t%s" % (c.get("id"), clean_name(c.get("title"))))

    ck = load_checkpoint(ck_path, portal, cfg) if ck_path else None
    if ck is None:
        ck = {"sig": _sig(portal, cfg), "done": {}, "cats_done": []}
    done = ck["done"]
    cats_done = set(ck.get("cats_done") or [])
    entries = [v for v in done.values() if v is not None]
    known_ids = set(done.keys())
    print("[+] Checkpoint VOD: %d peliculas ya resueltas" % len(done))

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
                    "%s vod=%d/%d cats=%d/%d\n"
                    % (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        len(done),
                        len(known_ids),
                        len(cats_done),
                        len(selected),
                    )
                )
        except Exception as exc:
            print("[!] Error guardando checkpoint VOD: %s" % exc)
        _git_push(ck_path, progress)

    def _on_emergency_signal(signum, frame):
        print("[!] Senal %d recibida (cancelacion/timeout). Guardando checkpoint VOD de emergencia..." % signum)
        if ck_path and ck:
            try:
                save_checkpoint(ck_path, ck)
                with open(progress, "a", encoding="utf-8") as fh:
                    fh.write("%s vod=%d/%d (EMERGENCIA)\n" % (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        len(done), len(known_ids)))
            except Exception as exc:
                print("[!] Error guardando checkpoint VOD de emergencia: %s" % exc)
        sys.exit(128 + signum)

    import signal
    signal.signal(signal.SIGTERM, _on_emergency_signal)
    signal.signal(signal.SIGINT, _on_emergency_signal)

    for c in selected:
        cid = str(c.get("id"))
        if cid in cats_done:
            continue
        lp = lang_prefix(c.get("title"))
        group = ("%s| %s" % (lp, clean_name(c.get("title")))).strip() or cid
        try:
            movies = list_movies(portal, cid)
        except PortalError:
            print("[!] Categoria %s: error de lista, se omite" % cid)
            continue
        for m in movies:
            known_ids.add(str(m.get("id")))
        pending = [m for m in movies if str(m.get("id")) not in done]
        print(
            "[+] Categoria %s '%s': %d peliculas (%d nuevas)"
            % (cid, group, len(movies), len(pending))
        )
        if pending:
            with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
                futures = {pool.submit(_resolve_or_none, portal, m): m for m in pending}
                for fut in concurrent.futures.as_completed(futures):
                    movie = futures[fut]
                    try:
                        url = fut.result()
                    except PortalError:
                        url = None
                    if not url:
                        continue
                    mid = str(movie.get("id"))
                    e = make_entry(movie, url, group)
                    if e:
                        done[mid] = e
                        entries.append(e)
        cats_done.add(cid)
        ck["cats_done"] = sorted(x for x in cats_done if x is not None)
        save_and_push(force=True)
        print("[+] VOD: %d peliculas unicas" % len(done))

    _write_m3u(out_path, entries)
    if ck_path:
        try:
            save_checkpoint(ck_path, ck)
        except Exception as exc:
            print("[!] Error guardando checkpoint VOD final: %s" % exc)
    if ck_path and push_interval:
        _git_push(out_path, ck_path)
    print("[+] VOD guardado en %s (%d peliculas)" % (out_path, len(entries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
