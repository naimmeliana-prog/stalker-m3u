"""Genera la lista M3U a partir de config.json y el secret MAG_MAC.

La MAC se toma de la variable de entorno MAG_MAC (secret de GitHub) y, si no
existe, de la clave 'mac' de config.json.
"""

import json
import os
import sys

from stalker_series_m3u import main


def _load_config():
    with open("config.json", encoding="utf-8") as fh:
        return json.load(fh)


def _build_args(cfg):
    mac = os.environ.get("MAG_MAC") or cfg.get("mac")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return None
    args = [cfg["portal"], "--mac", mac, "--out", cfg.get("out", "series.m3u")]
    cats = cfg.get("categories") or []
    if cats:
        args += ["--category"] + [str(c) for c in cats]
    rc = cfg.get("remove_categories") or []
    if rc:
        args += ["--remove-cats"] + [str(n) for n in rc]
    if cfg.get("search"):
        args += ["--search", cfg["search"]]
    if cfg.get("no_verify"):
        args += ["--no-verify"]
    if cfg.get("group"):
        args += ["--group", cfg["group"]]
    if cfg.get("checkpoint"):
        args += ["--checkpoint", cfg["checkpoint"]]
    if cfg.get("push_interval"):
        args += ["--push-interval", str(cfg["push_interval"])]
    if cfg.get("xtream_dir"):
        args += ["--xtream-dir", cfg["xtream_dir"]]
    args += ["--threads", str(cfg.get("threads", 8))]
    return args


if __name__ == "__main__":
    config = _load_config()
    argv = _build_args(config)
    if argv is None:
        raise SystemExit(1)
    raise SystemExit(main(argv))
