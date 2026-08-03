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
        while page <= max_pages:
            params = {"type": "series", "action": "get_ordered_list", "p": page, "JsHttpRequest": "1-xml"}
            params.update(extra)
            js = self._js(self._request(params))
            data = self._normalize_data(js.get("data", []))
            if not data:
                break
            items.extend(data)
            total = int(js.get("total_items") or 0)
            if total and len(items) >= total:
                break
            page += 1
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
        raw = season.get("series", [])
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
        params = {
            "type": "vod",
            "action": "create_link",
            "cmd": season_cmd,
            "series": str(episode),
            "JsHttpRequest": "1-xml",
        }
        try:
            data = self._request(params)
        except PortalError:
            return None
        raw = self._js(data).get("cmd") or data.get("cmd") or ""
        return self._clean_cmd(raw) or None

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
    logo = item.get("screenshot_uri") or item.get("cover")
    if logo and str(logo).startswith("/"):
        logo = portal.base_url + str(logo)
    try:
        seasons = portal.get_seasons(sid)
    except PortalError:
        return block, xinfo
    if not seasons:
        seasons = [s for s in item.get("seasons", [])] if isinstance(item.get("seasons"), list) else []
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
        portal.base_url,
        portal.mac,
        repr(sorted(args.category or []) or [None]),
        str(args.group or ""),
        str(bool(args.no_verify)),
        str(args.lang or ""),
        str(args.search or ""),
        str(bool(args.no_resolve)),
        str(args.xtream_dir or ""),
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
        if ck.get("config_sig") != _config_sig(portal, args):
            print("[!] Configuracion distinta: se descarta el checkpoint anterior")
            return None
        return ck
    except Exception:
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
        fh.writelines(sorted(entries))
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
        subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True)
        last = None
        for i in range(6):
            try:
                subprocess.run(["git", "push"], check=True, capture_output=True)
                return
            except subprocess.CalledProcessError as exc:
                last = exc
                time.sleep(2 + i * 2)
                try:
                    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True, capture_output=True)
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
    for cat in categories:
        cid = str(cat.get("id") or "")
        if cid:
            cat_names[cid] = str(cat.get("title") or cid)
    if args.list_categories:
        for cat in categories:
            print("  %s\t%s" % (cat.get("id"), cat.get("title")))
        return 0

    cats = args.category or [None]
    series_list = []
    seen_ids = set()
    for cat in cats:
        for s in portal.get_series(cat):
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

    def _save_and_push():
        if not (args.checkpoint and args.push_interval):
            return
        if time.time() - last_push[0] < args.push_interval:
            return
        last_push[0] = time.time()
        try:
            _save_checkpoint(args.checkpoint, ck, portal, args)
            with open("progress.log", "a", encoding="utf-8") as fh:
                fh.write("%s series=%d/%d eps=%d\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    done, total, len(entries)))
        except Exception as exc:
            print("[!] Error guardando checkpoint: %s" % exc)
        _git_push(args.checkpoint, "progress.log")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as pool:
        future_map = {pool.submit(process_series, portal, s, args): s for s in pending}
        total = len(future_map)
        done = 0
        for fut in concurrent.futures.as_completed(future_map):
            series = future_map[fut]
            block, xinfo = fut.result()
            done += 1
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
            print("[+] Series procesadas: %d/%d" % (done, total))

    if xt_dir:
        selected = set(str(c) for c in cats if c is not None)
        cat_out = [
            {"category_id": cid, "category_name": title, "parent_id": 0}
            for cid, title in cat_names.items()
            if not selected or cid in selected
        ]
        _write_json(os.path.join(xt_dir, "series_categories.json"), cat_out)
        _write_json(os.path.join(xt_dir, "series.json"), xt_series)
        _write_json(os.path.join(xt_dir, "streams.json"), streams)
        print("[+] Datos Xtream guardados en %s (%d series, %d streams)" % (xt_dir, len(xt_series), len(streams)))

    _write_m3u(args.out, entries)
    if args.checkpoint and args.push_interval:
        _git_push(args.out, args.checkpoint)
    print("[+] Lista guardada en %s (%d episodios)" % (args.out, len(entries)))
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
