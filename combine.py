#!/usr/bin/env python3
"""Combina series.m3u + vod.m3u + itv.m3u en global.m3u.

Cada seccion que no exista se omite. El encabezado #EXTM3U se escribe una
sola vez. Ademas genera los JSON Xtream para ITV y VOD (categorias, listas y
mapa de URLs por stream_id) bajo xtream/, de modo que el Worker pueda servirlos
por la API player_api.php.
"""

import json
import os
import re

SECTIONS = ("series.m3u", "vod.m3u", "itv.m3u")
XTREAM_DIR = "xtream"

EXTINF_RE = re.compile(r'#EXTINF:-?\d+\s+(.*)')
ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')
NAME_RE = re.compile(r',\s*([^,]+)\s*$')


def _lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    if lines and lines[0].startswith("#EXTM3U"):
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def _entries(lines):
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF:"):
            m = EXTINF_RE.match(line)
            if not m:
                i += 1
                continue
            attrs = dict(ATTR_RE.findall(m.group(1)))
            name_m = NAME_RE.search(line)
            name = name_m.group(1).strip() if name_m else attrs.get("tvg-name", "")
            raw_url = lines[i + 1] if i + 1 < len(lines) else ""
            url = re.sub(r"^(?:ffmpeg|ffrt)\s+", "", raw_url, flags=re.IGNORECASE).strip()
            url = re.sub(r"^\d+:\d+\s+", "", url).strip()
            if url and not url.startswith("#"):
                entries.append(
                    {
                        "id": attrs.get("tvg-id", ""),
                        "name": name or attrs.get("tvg-name", ""),
                        "icon": attrs.get("tvg-logo", ""),
                        "group": attrs.get("group-title", ""),
                        "url": url,
                    }
                )
            i += 2
        else:
            i += 1
    return entries


def _ext_from_url(url):
    m = re.search(r"[?&]stream=([\w.]+)", url)
    if m:
        base = m.group(1)
        dot = base.rfind(".")
        if dot > 0:
            ext = base[dot + 1:]
            if re.match(r"^[A-Za-z0-9]{2,4}$", ext):
                return ext.lower()
    m = re.search(r"\.([A-Za-z0-9]{2,4})(\?|$)", url)
    return (m.group(1) or "mkv") if m else "mkv"


def _category_id(cat_map, cats, group):
    if group in cat_map:
        return cat_map[group]
    cid = len(cats) + 1
    cat_map[group] = cid
    cats.append({"category_id": cid, "category_name": group or "Sin grupo", "parent_id": 0})
    return cid


def _build_live(entries):
    cats = []
    cat_map = {}
    streams = []
    urls = {}
    for num, e in enumerate(entries, 1):
        cid = _category_id(cat_map, cats, e["group"])
        streams.append(
            {
                "num": num,
                "name": e["name"],
                "stream_type": "live",
                "stream_id": e["id"],
                "stream_icon": e["icon"],
                "epg_channel_id": e["id"],
                "added": "2026-01-01 00:00:00",
                "category_id": cid,
                "custom_sid": "",
                "tv_archive": 0,
                "direct_source": e["url"],
                "container_extension": "ts",
            }
        )
        urls[e["id"]] = e["url"]
    return cats, streams, urls


def _build_vod(entries):
    cats = []
    cat_map = {}
    streams = []
    urls = {}
    for num, e in enumerate(entries, 1):
        cid = _category_id(cat_map, cats, e["group"])
        ext = _ext_from_url(e["url"])
        streams.append(
            {
                "num": num,
                "name": e["name"],
                "stream_type": "movie",
                "stream_id": e["id"],
                "stream_icon": e["icon"],
                "rating": "0",
                "rating_5based": 0.0,
                "added": "2026-01-01 00:00:00",
                "category_id": cid,
                "container_extension": ext,
                "custom_sid": "",
                "direct_source": "",
            }
        )
        urls[e["id"]] = e["url"]
    return cats, streams, urls


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)
    print("  %s: %d elementos" % (path, len(data)))


def load_config():
    path = os.environ.get("PORTAL_CONFIG_PATH") or "config.json"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _is_portal_paused(p_dir):
    cfg_file = os.path.join(p_dir, "config.json")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as fh:
                return bool(json.load(fh).get("paused"))
        except Exception:
            pass
    return False


def combine_xtream_series(root_xtream_dir, portals_dir):
    os.makedirs(os.path.join(root_xtream_dir, "series"), exist_ok=True)
    all_cats = []
    seen_cat_ids = set()
    all_series = []
    seen_series_ids = set()
    all_streams = {}

    if os.path.exists(portals_dir):
        for p_name in sorted(os.listdir(portals_dir)):
            p_dir = os.path.join(portals_dir, p_name)
            p_xtream = os.path.join(p_dir, "xtream")
            if not os.path.isdir(p_xtream) or _is_portal_paused(p_dir):
                continue

            # 1. Categories
            cat_file = os.path.join(p_xtream, "series_categories.json")
            if os.path.exists(cat_file):
                try:
                    with open(cat_file, "r", encoding="utf-8") as fh:
                        cats = json.load(fh)
                        for c in cats:
                            cid = str(c.get("category_id"))
                            if cid not in seen_cat_ids:
                                seen_cat_ids.add(cid)
                                all_cats.append(c)
                except Exception:
                    pass

            # 2. Series list
            series_file = os.path.join(p_xtream, "series.json")
            if os.path.exists(series_file):
                try:
                    with open(series_file, "r", encoding="utf-8") as fh:
                        s_list = json.load(fh)
                        for s in s_list:
                            sid = str(s.get("series_id"))
                            if sid not in seen_series_ids:
                                seen_series_ids.add(sid)
                                all_series.append(s)
                except Exception:
                    pass

            # 3. Streams
            streams_file = os.path.join(p_xtream, "streams.json")
            if os.path.exists(streams_file):
                try:
                    with open(streams_file, "r", encoding="utf-8") as fh:
                        st = json.load(fh)
                        all_streams.update(st)
                except Exception:
                    pass

            # 4. Individual series JSON files
            p_series_dir = os.path.join(p_xtream, "series")
            if os.path.exists(p_series_dir):
                for fname in os.listdir(p_series_dir):
                    if fname.endswith(".json"):
                        src_f = os.path.join(p_series_dir, fname)
                        dst_f = os.path.join(root_xtream_dir, "series", fname)
                        try:
                            with open(src_f, "r", encoding="utf-8") as sf:
                                sdata = json.load(sf)
                            with open(dst_f, "w", encoding="utf-8") as df:
                                json.dump(sdata, df, ensure_ascii=False)
                        except Exception:
                            pass

    _write_json(os.path.join(root_xtream_dir, "series_categories.json"), all_cats)
    _write_json(os.path.join(root_xtream_dir, "series.json"), all_series)
    _write_json(os.path.join(root_xtream_dir, "streams.json"), all_streams)
    print("  %s: %d series (%d categorias, %d streams)" % (root_xtream_dir, len(all_series), len(all_cats), len(all_streams)))


def combine_xtream_live_vod(root_xtream_dir, portals_dir):
    os.makedirs(root_xtream_dir, exist_ok=True)
    if not os.path.exists(portals_dir):
        return
    for kind in ("live", "vod"):
        all_cats = []
        seen_cat_names = set()
        all_streams = []
        seen_stream_ids = set()
        all_urls = {}

        for p_name in sorted(os.listdir(portals_dir)):
            p_dir = os.path.join(portals_dir, p_name)
            p_xtream = os.path.join(p_dir, "xtream")
            if not os.path.isdir(p_xtream) or _is_portal_paused(p_dir):
                continue

            # Categories
            cat_file = os.path.join(p_xtream, f"{kind}_categories.json")
            if os.path.exists(cat_file):
                try:
                    with open(cat_file, "r", encoding="utf-8") as fh:
                        cats = json.load(fh)
                        for c in cats:
                            cname = str(c.get("category_name") or "")
                            if cname not in seen_cat_names:
                                seen_cat_names.add(cname)
                                all_cats.append(c)
                except Exception:
                    pass

            # Streams
            stream_file = os.path.join(p_xtream, f"{kind}_streams.json")
            if os.path.exists(stream_file):
                try:
                    with open(stream_file, "r", encoding="utf-8") as fh:
                        st_list = json.load(fh)
                        for st in st_list:
                            sid = str(st.get("stream_id"))
                            if sid not in seen_stream_ids:
                                seen_stream_ids.add(sid)
                                all_streams.append(st)
                except Exception:
                    pass

            # URLs
            url_file = os.path.join(p_xtream, f"{kind}_urls.json")
            if os.path.exists(url_file):
                try:
                    with open(url_file, "r", encoding="utf-8") as fh:
                        urls = json.load(fh)
                        all_urls.update(urls)
                except Exception:
                    pass

        if all_streams:
            _write_json(os.path.join(root_xtream_dir, f"{kind}_categories.json"), all_cats)
            _write_json(os.path.join(root_xtream_dir, f"{kind}_streams.json"), all_streams)
            _write_json(os.path.join(root_xtream_dir, f"{kind}_urls.json"), all_urls)
            print("  %s: %d %s streams (%d categorias) combinados" % (root_xtream_dir, len(all_streams), kind, len(all_cats)))


def build_portal_map(root_xtream_dir, portals_dir):
    portal_map = {}
    if os.path.exists(portals_dir):
        for p_name in sorted(os.listdir(portals_dir)):
            p_dir = os.path.join(portals_dir, p_name)
            p_xtream = os.path.join(p_dir, "xtream")
            if not os.path.isdir(p_xtream) or _is_portal_paused(p_dir):
                continue
            for map_name in ("live_urls.json", "vod_urls.json", "streams.json"):
                path = os.path.join(p_xtream, map_name)
                if os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                            if isinstance(data, dict):
                                for sid in data.keys():
                                    portal_map[sid] = p_name
                    except Exception:
                        pass
    _write_json(os.path.join(root_xtream_dir, "portal_map.json"), portal_map)
    print("  %s: %d mappings de portales guardados en portal_map.json" % (root_xtream_dir, len(portal_map)))


def build_xtream(xtream_dir, itv_path, vod_path):
    os.makedirs(xtream_dir, exist_ok=True)
    for src, kind in ((itv_path, "live"), (vod_path, "vod")):
        entries = _entries(_lines(src))
        if not entries:
            print("[!] %s: sin entradas, no se generan JSON %s" % (src, kind))
            _write_json(os.path.join(xtream_dir, kind + "_categories.json"), [])
            _write_json(os.path.join(xtream_dir, kind + "_streams.json"), [])
            _write_json(os.path.join(xtream_dir, kind + "_urls.json"), {})
            continue
        if kind == "live":
            cats, streams, urls = _build_live(entries)
        else:
            cats, streams, urls = _build_vod(entries)
        _write_json(os.path.join(xtream_dir, kind + "_categories.json"), cats)
        _write_json(os.path.join(xtream_dir, kind + "_streams.json"), streams)
        _write_json(os.path.join(xtream_dir, kind + "_urls.json"), urls)
        print("  %s: %d canales/peliculas (%d categorias)" % (src, len(entries), len(cats)))


DEFAULT_WORKER_HOST = os.environ.get("WORKER_HOST") or "https://stalker-xtream.naimmeliana.workers.dev"


def _rewrite_m3u_section(path, kind, worker_host=DEFAULT_WORKER_HOST, username="test", password="test1"):
    lines = _lines(path)
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#EXTINF:"):
            out.append(ln)
            raw_url = lines[i + 1] if i + 1 < len(lines) else ""
            clean_url = re.sub(r"^(?:ffmpeg|ffrt)\s+", "", raw_url, flags=re.IGNORECASE).strip()
            clean_url = re.sub(r"^\d+:\d+\s+", "", clean_url).strip()
            out.append(clean_url)
            i += 2
        elif ln.startswith("#"):
            out.append(ln)
            i += 1
        else:
            i += 1
    return out


def combine(out="global.m3u"):
    cfg = load_config()
    config_dir = os.path.dirname(os.path.abspath(os.environ.get("PORTAL_CONFIG_PATH") or "config.json"))
    def resolve_path(p):
        if p and not os.path.isabs(p):
            return os.path.join(config_dir, p)
        return p

    series_path = resolve_path(cfg.get("out") or "series.m3u")
    vod_path = resolve_path(cfg.get("vod", {}).get("out") or "vod.m3u")
    itv_path = resolve_path(cfg.get("itv", {}).get("out") or "itv.m3u")
    xtream_dir = resolve_path(cfg.get("xtream_dir") or "xtream")
    out_path = resolve_path(out)
    portals_dir = resolve_path("portals")

    p_name = os.path.basename(config_dir) if os.path.basename(config_dir) != os.path.basename(os.getcwd()) else "test"

    sections = [
        (series_path, "series"),
        (vod_path, "movie"),
        (itv_path, "live")
    ]
    tmp = out_path + ".tmp"
    counts = {}

    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for sec, kind in sections:
            written_lines = _rewrite_m3u_section(sec, kind, DEFAULT_WORKER_HOST, p_name, "test1")
            counts[sec] = len(written_lines)
            for ln in written_lines:
                fh.write(ln + "\n")
    os.replace(tmp, out_path)
    for sec, kind in sections:
        print("  %s (%s): %d lineas" % (sec, kind, counts.get(sec, 0)))
    print("[+] Combinado en %s" % out_path)
    build_xtream(xtream_dir, itv_path, vod_path)
    combine_xtream_series(xtream_dir, portals_dir)
    combine_xtream_live_vod(xtream_dir, portals_dir)
    build_portal_map(xtream_dir, portals_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(combine())

