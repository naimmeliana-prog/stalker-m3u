#!/usr/bin/env python3
"""Extrae una lista M3U de series desde un portal IPTV Stalker/MAG mediante autenticacion por MAC.

Uso:
    python stalker_series_m3u.py --mac 00:1A:79:AB:CD:EF http://portal.example.com
    python stalker_series_m3u.py --mac 00:1A:79:AB:CD:EF http://portal.example.com --search "breaking"
    python stalker_series_m3u.py --mac 00:1A:79:AB:CD:EF http://portal.example.com --no-resolve
"""

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENTRY_POINTS = (
    "/server/load.php",
    "/portal.php",
    "/c/server/load.php",
    "/stalker_portal/server/load.php",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
X_USER_AGENT = "Model: MAG250; Link: WiFi"

SERIES_GROUP = "SERIES"

CMD_PREFIX_RE = re.compile(r"^(?:ffmpeg|ffrt)\s+", re.IGNORECASE)
FFMPEG_ARGS_RE = re.compile(r"^\d+:\d+\s+")
SEASON_NUM_RE = re.compile(r"(\d+)")
SERIES_NAME_CLEAN_RE = re.compile(r"^[|]?\s*[A-Z]{2}[|]\s*")


def _norm(text):
    import unicodedata

    t = unicodedata.normalize("NFKD", str(text or ""))
    t = t.encode("ascii", "ignore").decode("utf-8", "replace")
    return re.sub(r"\s+", " ", t.upper()).strip()


def _clean_series_name(title):
    return SERIES_NAME_CLEAN_RE.sub("", str(title or "").strip()).strip()


def _title_lang(title):
    raw = str(title or "").strip()
    t_upper = raw.upper()
    match = re.match(r"^(ES|FR|UK|EN)\b", t_upper)
    if match:
        val = match.group(1)
        return "UK" if val == "EN" else val
    if raw.startswith("|"):
        parts = raw.split("|")
        val = parts[1].strip() if len(parts) > 1 else ""
        if val in ["ES", "FR", "UK", "EN"]:
            return "UK" if val == "EN" else val
    if "|" in raw:
        val = raw.split("|", 1)[0].strip()
        if val in ["ES", "FR", "UK", "EN"]:
            return "UK" if val == "EN" else val
    if any(k in t_upper for k in ["ESPAÑA", "ESPANA", "SPAIN", "SPANISH", "ESPAÑOL", "ESPANOL", "CASTELLANO", "ES |", "| ES", "ES -", "SERIES ES", "[ES]"]):
        return "ES"
    if any(k in t_upper for k in ["FRANCE", "FRENCH", "FRANCAIS", "FRANÇAIS", "FR |", "| FR", "FR -", "SERIES FR", "[FR]"]):
        return "FR"
    if any(k in t_upper for k in ["UK |", "| UK", "UNITED KINGDOM", "ENGLAND", "ENGLISH", "ANGLAIS", "BRITISH", "GB"]):
        return "UK"
    return ""


def _fmt_cat_name(title):
    name = _clean_series_name(title)
    if not name:
        return str(title or "")
    name = re.sub(r"\s+", " ", name).strip()
    lp = _title_lang(title)
    return ("%s| %s" % (lp, name)) if lp else name


class PortalError(Exception):
    pass


class StalkerPortal:
    def __init__(self, base_url, mac, timeout=15, verify_ssl=True):
        self.base_url = base_url.rstrip("/")
        self.mac = mac
        self.timeout = timeout
        self.token = None
        self.entry = None
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        )

    def _headers(self, include_token=True):
        headers = {
            "User-Agent": USER_AGENT,
            "X-User-Agent": X_USER_AGENT,
            "Cookie": "mac=%s; stb_lang=en; timezone=Europe/London" % self.mac,
        }
        if include_token and self.token:
            headers["Cookie"] += "; token=%s" % self.token
            headers["Authorization"] = "Bearer %s" % self.token
        return headers

    def _js(self, data):
        if isinstance(data, dict):
            js = data.get("js", data)
        else:
            js = data
        return js if isinstance(js, dict) else {}

    def _request(self, params, include_token=True, allow_retry=True, _attempt=1):
        query = urllib.parse.urlencode(params)
        url = self.base_url + self.entry + "?" + query
        req = urllib.request.Request(url, headers=self._headers(include_token))
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and include_token and allow_retry:
                self.handshake()
                return self._request(params, include_token, allow_retry=False)
            if exc.code in (500, 502, 503, 504) and _attempt < 4:
                time.sleep(0.5 * _attempt)
                return self._request(params, include_token, allow_retry, _attempt + 1)
            raise PortalError("HTTP %s al pedir %s" % (exc.code, url))
        except Exception as exc:
            raise PortalError("Error pidiendo %s: %s" % (url, exc))

    def handshake(self):
        for entry in ENTRY_POINTS:
            self.entry = entry
            query = urllib.parse.urlencode(
                {"type": "stb", "action": "handshake", "JsHttpRequest": "1-xml"}
            )
            url = self.base_url + entry + "?" + query
            req = urllib.request.Request(url, headers=self._headers(False))
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", "replace")
                token = self._js(json.loads(body)).get("token")
                if token:
                    self.token = token
                    return token
            except Exception:
                continue
        raise PortalError(
            "No se pudo autenticar en %s (handshake fallido en todos los endpoints)"
            % self.base_url
        )

    def get_categories(self):
        try:
            data = self._request(
                {"type": "series", "action": "get_categories", "JsHttpRequest": "1-xml"}
            )
            if isinstance(data, dict):
                js = data.get("js", data)
            else:
                js = data
            if isinstance(js, list):
                return js
            if isinstance(js, dict):
                cats = js.get("data", [])
                if isinstance(cats, dict):
                    cats = [item for group in cats.values() for item in (group if isinstance(group, list) else [group])]
                return cats if isinstance(cats, list) else []
            return []
        except PortalError:
            return []

    def _normalize_data(self, data):
        if isinstance(data, dict):
            return [
                item
                for group in data.values()
                for item in (group if isinstance(group, list) else [group])
            ]
        return data if isinstance(data, list) else []

    def _paged_list(self, extra, max_pages=1000):
        items = []
        page = 1
        total = 0
        empty_tries = 0
        while page <= max_pages:
            params = {"type": "series", "action": "get_ordered_list", "p": page, "JsHttpRequest": "1-xml"}
            params.update(extra)
            js = self._js(self._request(params))
            data = self._normalize_data(js.get("data", []))
            t = int(js.get("total_items") or 0)
            if t:
                total = t
            if not data:
                if page == 1:
                    if total > 0:
                        raise PortalError("Lista vacia en pagina 1 (total=%d)" % total)
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
            raise PortalError("Lista incompleta %d/%d en pagina %d" % (len(items), total, page))
        if page > max_pages:
            raise PortalError("Limite de paginas (%d) alcanzado" % max_pages)
        return items

    def get_series(self, category=None):
        extra = {}
        if category:
            extra["category"] = category
        return self._paged_list(extra)

    def get_seasons(self, series_id):
        return self._paged_list({"movie_id": str(series_id)}, max_pages=100)

    @staticmethod
    def iter_episodes(season):
        raw = season.get("series") or season.get("episodes") or season.get("list") or []
        if isinstance(raw, dict):
            raw = list(raw.values())
        if not isinstance(raw, list):
            raw = [raw]
        for idx, ep in enumerate(raw, 1):
            if isinstance(ep, dict):
                num = ep.get("series_number") or ep.get("num") or ep.get("id") or idx
                name = ep.get("name")
            else:
                num = ep or idx
                name = None
            yield num, name

    def resolve_stream(self, season_cmd, episode):
        if not season_cmd:
            return None
        if re.match(r"^(?:https?|rtmp)://", season_cmd, re.IGNORECASE):
            return season_cmd
        for stype in ("series", "vod"):
            params = {
                "type": stype,
                "action": "create_link",
                "cmd": season_cmd,
                "series": str(episode),
                "JsHttpRequest": "1-xml",
            }
            try:
                data = self._request(params)
                raw = self._js(data).get("cmd") or data.get("cmd") or ""
                cleaned = self._clean_cmd(raw)
                if cleaned:
                    return cleaned
            except PortalError:
                continue
        return None

    @staticmethod
    def _clean_cmd(raw):
        raw = CMD_PREFIX_RE.sub("", raw).strip()
        raw = FFMPEG_ARGS_RE.sub("", raw).strip()
        return raw or None


def _fmt_num(num):
    try:
        return "%02d" % int(num)
    except (TypeError, ValueError):
        return str(num)


def build_title(series_name, season_num, episode_num, episode_name):
    tag = "S%sE%s" % (_fmt_num(season_num), _fmt_num(episode_num))
    if episode_name:
        return "%s - %s - %s" % (series_name, tag, episode_name)
    return "%s - %s" % (series_name, tag)


def _escape_attr(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace(",", " ")


CONTAINER_EXT = "mkv"


def _clean_title(name):
    title = re.sub(r"^[^\w]+", "", str(name)).strip()
    title = re.sub(r"\s+", " ", title)
    return title


def process_series(portal, item, args):
    block = []
    xinfo = None
    sid = str(item.get("id") or "").split(":")[0]
    name = str(item.get("name") or "Sin nombre")
    banned = ["LATINO", "QUEBEC", "SUISSE", "SUIZA", "BELGIQUE", "BELGICA", "CANADA", "CANADIAN"]
    if any(r in _norm(name) for r in banned):
        return block, xinfo
    logo = item.get("screenshot_uri") or item.get("cover")
    if logo and str(logo).startswith("/"):
        logo = portal.base_url + str(logo)
    try:
        seasons = portal.get_seasons(sid)
    except PortalError:
        seasons = []
    if not seasons:
        raw_s = item.get("seasons") or item.get("series")
        seasons = [s for s in raw_s] if isinstance(raw_s, list) else []
    x_seasons = []
    for idx, season in enumerate(seasons, 1):
        match = SEASON_NUM_RE.search(str(season.get("name") or ""))
        season_num = int(match.group(1)) if match else idx
        cmd = season.get("cmd")
        group = name if args.group == "series" else SERIES_GROUP
        x_eps = []
        for ep_num, ep_name in StalkerPortal.iter_episodes(season):
            title = build_title(name, season_num, ep_num, ep_name)
            if args.no_resolve:
                if not cmd:
                    continue
                query = urllib.parse.urlencode(
                    {
                        "type": "vod",
                        "action": "create_link",
                        "cmd": cmd,
                        "series": str(ep_num),
                        "JsHttpRequest": "1-xml",
                    }
                )
                url = portal.base_url + portal.entry + "?" + query
            else:
                url = portal.resolve_stream(cmd, ep_num)
                if not url:
                    continue
            extinf = (
                '#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" '
                'group-title="%s",%s\n'
            ) % (
                _escape_attr(sid),
                _escape_attr(name),
                _escape_attr(logo or ""),
                _escape_attr(group),
                title,
            )
            if group != SERIES_GROUP:
                extinf += "#EXTGRP:%s\n" % _escape_attr(group)
            block.append(extinf + url + "\n")
            if args.xtream_dir:
                xurl = url if not args.no_resolve else portal.resolve_stream(cmd, ep_num)
                if not xurl:
                    continue
                x_eps.append(
                    {
                        "id": "%s:%s:%s" % (sid, season_num, ep_num),
                        "episode_num": str(ep_num),
                        "season": str(season_num),
                        "title": title,
                        "container_extension": CONTAINER_EXT,
                        "info": {
                            "season": str(season_num),
                            "episode": str(ep_num),
                        },
                        "stream_url": xurl,
                    }
                )
        if x_eps:
            x_seasons.append(
                {
                    "season_number": str(season_num),
                    "id": season_num,
                    "name": "Season %s" % season_num,
                    "episode_count": len(x_eps),
                    "episodes": x_eps,
                }
            )
    if args.xtream_dir and x_seasons:
        x_seasons.sort(key=lambda s: int(s["season_number"] or 0))
        xinfo = {
            "series_id": str(sid),
            "name": _clean_title(name),
            "cover": str(logo or ""),
            "category_id": str(item.get("category_id") or ""),
            "seasons": x_seasons,
        }
    return block, xinfo


def _matches_lang(item, lang):
    needle = lang.lower()
    haystacks = [
        item.get("language"),
        item.get("audio"),
        item.get("genres_str"),
        item.get("name"),
        item.get("original_name"),
    ]
    for value in haystacks:
        if value and needle in str(value).lower():
            return True
    return False


def _config_sig(portal, args):
    h = hashlib.sha256()
    parts = [
        str(portal.base_url or "").rstrip("/"),
        str(portal.mac or "").upper(),
        repr(sorted(str(c) for c in (args.category or [])) if args.category else [None]),
        repr(sorted(str(c) for c in (args.remove_cats or [])) if args.remove_cats else [None]),
        str(args.group or ""),
        str(bool(args.no_verify)),
        str(args.lang or ""),
        str(args.search or ""),
        str(bool(args.no_resolve)),
        str(os.path.normpath(args.xtream_dir) if args.xtream_dir else ""),
    ]
    h.update("|".join(parts).encode("utf-8"))
    return h.hexdigest()


def _load_checkpoint(path, portal, args):
    if not path or not os.path.exists(path):
        return None
    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            ck = json.load(fh)
        if not isinstance(ck, dict) or not isinstance(ck.get("done"), dict):
            return None
        saved_sig = ck.get("config_sig")
        curr_sig = _config_sig(portal, args)
        if saved_sig != curr_sig:
            print("[!] Configuracion distinta: se descarta el checkpoint anterior (guardado: %s..., actual: %s...)" % (str(saved_sig)[:8], str(curr_sig)[:8]))
            return None
        return ck
    except Exception as exc:
        print("[!] Error leyendo checkpoint (%s): %s" % (path, exc))
        return None


def _save_checkpoint(path, ck, portal, args):
    ck["config_sig"] = _config_sig(portal, args)
    tmp = path + ".tmp"
    opener = gzip.open if path.endswith(".gz") else open
    with opener(tmp, "wt", encoding="utf-8") as fh:
        json.dump(ck, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _write_m3u(path, entries):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        fh.writelines(sorted(x for x in entries if x is not None))
    os.replace(tmp, path)


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _collect_xtream(xinfo, xt_series, streams, xt_dir):
    seasons = xinfo.get("seasons") or []
    if not seasons:
        return
    sid = str(xinfo["series_id"])
    xt_series.append(
        {
            "series_id": sid,
            "name": str(xinfo.get("name") or ""),
            "cover": str(xinfo.get("cover") or ""),
            "category_id": str(xinfo.get("category_id") or ""),
        }
    )
    episodes_map = {}
    for season in seasons:
        season_number = str(season.get("season_number") or "0")
        episodes_map[season_number] = season.get("episodes") or []
        for ep in episodes_map[season_number]:
            streams[ep["id"]] = ep.get("stream_url") or ""
    payload = {
        "seasons": seasons,
        "episodes": episodes_map,
        "info": {
            "name": str(xinfo.get("name") or ""),
            "cover": str(xinfo.get("cover") or ""),
            "category_id": str(xinfo.get("category_id") or ""),
        },
    }
    _write_json(os.path.join(xt_dir, "series", sid + ".json"), payload)


def _git_push(*paths):
    if not os.environ.get("GITHUB_TOKEN"):
        print("[!] _git_push: no hay GITHUB_TOKEN en el entorno")
        return
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True, capture_output=True)
        subprocess.run(["git", "add", "--"] + list(paths), check=True, capture_output=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            return
        msg = "checkpoint M3U (%s)" % time.strftime("%F %R UTC", time.gmtime())
        subprocess.run(["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=github-actions[bot]@users.noreply.github.com", "commit", "-m", msg], check=True, capture_output=True)
        last = None
        for i in range(6):
            try:
                subprocess.run(["git", "push", "origin", "HEAD:main"], check=True, capture_output=True)
                return
            except subprocess.CalledProcessError as exc:
                last = exc
                time.sleep(2 + i * 2)
                try:
                    subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], check=True, capture_output=True)
                except subprocess.CalledProcessError:
                    pass
        raise last
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace").strip() or str(exc)
        print("[!] _git_push fallo: %s" % err)
    except Exception as exc:
        print("[!] _git_push fallo: %s" % exc)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extrae una lista M3U de series de un portal Stalker/MAG usando autenticacion MAC."
    )
    parser.add_argument("portal", help="URL base del portal, p.ej. http://portal.example.com")
    parser.add_argument("--mac", required=True, help="Direccion MAC del dispositivo, p.ej. 00:1A:79:AB:CD:EF")
    parser.add_argument("--out", default="series.m3u", help="Archivo de salida (por defecto: series.m3u)")
    parser.add_argument("--category", nargs="*", help="Filtrar por IDs de categoria de series (p.ej. 949 1006; omitir para todas)")
    parser.add_argument("--remove-cats", nargs="*", help="Nombres de categorias a excluir (coincidencia por nombre, todas las lenguas)")
    parser.add_argument("--search", help="Solo series cuyo nombre contenga este texto (sin distinguir mayusculas)")
    parser.add_argument("--lang", help="Solo series de este idioma, p.ej. espanol, latino, castellano, ingles (busca en language/genres_str/nombre)")
    parser.add_argument("--no-resolve", action="store_true", help="No resolver URLs; emitir el comando create_link")
    parser.add_argument("--group", choices=("series", "single"), default="series",
                        help="Agrupar por serie (series, cada serie es una categoria) o en un solo grupo (single). Por defecto: series")
    parser.add_argument("--threads", type=int, default=8, help="Hilos para resolver URLs (por defecto: 8)")
    parser.add_argument("--timeout", type=float, default=15, help="Tiempo de espera HTTP en segundos (por defecto: 15)")
    parser.add_argument("--no-verify", action="store_true", help="No verificar certificados SSL")
    parser.add_argument("--list-categories", action="store_true", help="Listar categorias de series y salir")
    parser.add_argument("--list-series", action="store_true", help="Listar series y salir")
    parser.add_argument("--checkpoint", help="Ruta del archivo de checkpoint para reanudar trabajo parcial")
    parser.add_argument("--progress", help="Ruta del archivo de log de progreso")
    parser.add_argument("--push-interval", type=int, default=None, help="Cada N segundos, escribir el M3U parcial y hacer push (requiere GITHUB_TOKEN y --checkpoint)")
    parser.add_argument("--xtream-dir", help="Generar ademas datos Xtream (JSON por serie + indices) en este directorio")
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except PortalError as exc:
        print("[!] %s" % exc, file=sys.stderr)
        return 1


def _run(args):
    portal = StalkerPortal(args.portal, args.mac, args.timeout, not args.no_verify)
    portal.handshake()
    print("[+] Token obtenido: %s..." % (portal.token[:12] if portal.token else "(vacio)"))
    print("[+] Endpoint: %s" % portal.entry)

    categories = portal.get_categories()
    cat_names = {}
    banned_regions = {"LATINO", "QUEBEC", "SUISSE", "SUIZA", "BELGIQUE", "BELGICA", "CANADA", "CANADIAN"}
    filtered_cat_ids = []
    for cat in categories:
        title = str(cat.get("title") or "")
        cid = str(cat.get("id") or "").strip()
        title_lower = title.lower().strip()
        if cid in ["*", "all", "0"] or title_lower in ["all", "todos", "all series", "todas las series", "tous"]:
            continue
        if any(r in _norm(title) for r in banned_regions):
            continue
        lp = _title_lang(title)
        if lp not in ["ES", "FR"]:
            continue
        if cid:
            cat_names[cid] = title
            filtered_cat_ids.append(cid)

    if args.list_categories:
        for cat in categories:
            if str(cat.get("id")) in filtered_cat_ids:
                print("  %s\t%s" % (cat.get("id"), _fmt_cat_name(cat.get("title"))))
        return 0

    remove_cats = set()
    for pat in (args.remove_cats or []):
        needle = _norm(pat)
        if not needle:
            continue
        for cat in categories:
            if _title_lang(str(cat.get("title") or "")) != "FR":
                continue
            if needle in _norm(_clean_series_name(str(cat.get("title") or ""))):
                remove_cats.add(str(cat.get("id")))
    if remove_cats:
        print("[+] Categorias excluidas (%d): %s" % (len(remove_cats), ", ".join(sorted(remove_cats))))

    cats = args.category or [None]
    if cats == [None]:
        cats = [cid for cid in filtered_cat_ids if cid not in remove_cats]
    else:
        req_cats = [str(c) for c in cats]
        cats = [c for c in req_cats if c not in remove_cats]
        for cat in categories:
            cid = str(cat.get("id") or "").strip()
            if cid in cats and cid not in cat_names:
                cat_names[cid] = str(cat.get("title") or cid)
    if not cats:
        print("[!] No quedan categorias tras excluir; nada que hacer")
        return 0
    series_list = []
    seen_ids = set()
    for cat in cats:
        try:
            fetched = portal.get_series(cat)
        except PortalError as exc:
            print("[!] Categoria %s truncada (%s); se reintenta en el proximo run" % (cat, exc))
            continue
        for s in fetched:
            sid = str(s.get("id") or "").split(":")[0]
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                series_list.append(s)
    if args.lang:
        series_list = [s for s in series_list if _matches_lang(s, args.lang)]
    if args.search:
        needle = args.search.lower()
        series_list = [s for s in series_list if needle in str(s.get("name", "")).lower()]
    print("[+] Series encontradas: %d" % len(series_list))

    if args.list_series:
        for s in series_list:
            print("  %s\t%s" % (s.get("id"), s.get("name")))
        return 0

    xt_dir = args.xtream_dir
    if xt_dir:
        os.makedirs(os.path.join(xt_dir, "series"), exist_ok=True)

    entries = []
    done_ids = set()
    ck = None
    xt_series = []
    streams = {}
    if args.checkpoint:
        ck = _load_checkpoint(args.checkpoint, portal, args) or {"done": {}}
        for sid, val in ck["done"].items():
            done_ids.add(sid)
            entry = val if isinstance(val, dict) else {"m3u": val, "xtream": None}
            entries.extend(entry.get("m3u", []))
            if entry.get("xtream"):
                _collect_xtream(entry["xtream"], xt_series, streams, xt_dir)
        print("[+] Checkpoint: %d series ya procesadas (%d episodios)" % (len(done_ids), len(entries)))

    pending = [s for s in series_list if str(s.get("id") or "").split(":")[0] not in done_ids]
    print("[+] Series pendientes: %d" % len(pending))

    last_push = [time.time()]

    def _save_and_push(force=False):
        if not (args.checkpoint and args.push_interval):
            return
        if not force and time.time() - last_push[0] < args.push_interval:
            return
        last_push[0] = time.time()
        try:
            _save_checkpoint(args.checkpoint, ck, portal, args)
            prog_file = args.progress or "progress.log"
            with open(prog_file, "a", encoding="utf-8") as fh:
                fh.write("%s series=%d/%d eps=%d\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    done, total, len(entries)))
        except Exception as exc:
            print("[!] Error guardando checkpoint: %s" % exc)
        _git_push(args.checkpoint, args.progress or "progress.log")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as pool:
        future_map = {pool.submit(process_series, portal, s, args): s for s in pending}
        total = len(future_map)
        done = 0
        for fut in concurrent.futures.as_completed(future_map):
            series = future_map[fut]
            done += 1
            try:
                block, xinfo = fut.result()
            except Exception as exc:
                print("[!] Error procesando serie %s (%s): %s" % (series.get("id"), series.get("name"), exc))
                continue
            if block:
                entries.extend(block)
                sid = str(series.get("id") or "").split(":")[0]
                if args.checkpoint and sid:
                    ck["done"][sid] = {"m3u": block, "xtream": xinfo}
                    if not args.push_interval:
                        _save_checkpoint(args.checkpoint, ck, portal, args)
                if xinfo and xt_dir:
                    _collect_xtream(xinfo, xt_series, streams, xt_dir)
            _save_and_push()
            if done % 20 == 0 or done == total:
                print("[+] Series procesadas: %d/%d" % (done, total))

    if xt_dir:
        selected = set(str(c) for c in cats if c is not None)
        cat_out = [
            {"category_id": cid, "category_name": _fmt_cat_name(title), "parent_id": 0}
            for cid, title in cat_names.items()
            if not selected or cid in selected
        ]
        _write_json(os.path.join(xt_dir, "series_categories.json"), cat_out)
        _write_json(os.path.join(xt_dir, "series.json"), xt_series)
        _write_json(os.path.join(xt_dir, "streams.json"), streams)
        print("[+] Datos Xtream guardados en %s (%d series, %d streams)" % (xt_dir, len(xt_series), len(streams)))

    _write_m3u(args.out, entries)
    if args.checkpoint:
        try:
            _save_checkpoint(args.checkpoint, ck, portal, args)
        except Exception as exc:
            print("[!] Error guardando checkpoint final: %s" % exc)
    if args.checkpoint and args.push_interval:
        _git_push(args.out, args.checkpoint)
    print("[+] Lista guardada en %s (%d episodios)" % (args.out, len(entries)))
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
