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
            url = lines[i + 1] if i + 1 < len(lines) else ""
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


def build_xtream(xtream_dir, itv_path, vod_path):
    os.makedirs(xtream_dir, exist_ok=True)
    for src, kind in ((itv_path, "live"), (vod_path, "vod")):
        entries = _entries(_lines(src))
        if not entries:
            print("[!] %s: sin entradas, no se generan JSON %s" % (src, kind))
            continue
        if kind == "live":
            cats, streams, urls = _build_live(entries)
        else:
            cats, streams, urls = _build_vod(entries)
        _write_json(os.path.join(xtream_dir, kind + "_categories.json"), cats)
        _write_json(os.path.join(xtream_dir, kind + "_streams.json"), streams)
        _write_json(os.path.join(xtream_dir, kind + "_urls.json"), urls)
        print("  %s: %d canales/peliculas (%d categorias)" % (src, len(entries), len(cats)))


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

    sections = [series_path, vod_path, itv_path]
    tmp = out_path + ".tmp"
    counts = {}

    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for sec in sections:
            lines = _lines(sec)
            counts[sec] = len(lines)
            for ln in lines:
                fh.write(ln + "\n")
    os.replace(tmp, out_path)
    for sec in sections:
        print("  %s: %d lineas" % (sec, counts.get(sec, 0)))
    print("[+] Combinado en %s" % out_path)
    build_xtream(xtream_dir, itv_path, vod_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(combine())
