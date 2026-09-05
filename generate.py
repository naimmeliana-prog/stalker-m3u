"""Genera la lista M3U a partir de config.json y el secret MAG_MAC.

La MAC se toma de la variable de entorno MAG_MAC (secret de GitHub) y, si no
existe, de la clave 'mac' de config.json.
"""

import json
import os
import sys

from stalker_series_m3u import main


def _load_config():
    path = "config.json"
    # Find if a json argument is passed
    for i, arg in enumerate(sys.argv):
        if i > 0 and arg.endswith(".json"):
            path = sys.argv.pop(i)
            break
    if os.environ.get("PORTAL_CONFIG_PATH"):
        path = os.environ.get("PORTAL_CONFIG_PATH")
    os.environ["PORTAL_CONFIG_PATH"] = path
    os.environ["PORTAL_CONFIG_DIR"] = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _build_args(cfg):
    mac = cfg.get("mac") or os.environ.get("MAG_MAC")
    if not mac:
        print("[!] Falta la MAC: define el secret MAG_MAC o la clave 'mac' en config.json", file=sys.stderr)
        return None
    config_dir = os.environ.get("PORTAL_CONFIG_DIR") or ""
    def resolve_path(p):
        if p and not os.path.isabs(p) and config_dir:
            return os.path.join(config_dir, p)
        return p
    args = [cfg["portal"], "--mac", mac, "--out", resolve_path(cfg.get("out", "series.m3u"))]
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
        args += ["--checkpoint", resolve_path(cfg["checkpoint"])]
    args += ["--progress", resolve_path(cfg.get("progress", "progress.log"))]
    if cfg.get("push_interval"):
        args += ["--push-interval", str(cfg["push_interval"])]
    if cfg.get("xtream_dir"):
        args += ["--xtream-dir", resolve_path(cfg["xtream_dir"])]
    args += ["--threads", str(cfg.get("threads", 8))]
    if not cfg.get("resolve", False):
        args += ["--no-resolve"]
    return args


if __name__ == "__main__":
    config = _load_config()
    if config.get("paused"):
        print("[+] Portal pausado. Omitiendo...")
        sys.exit(0)
    argv = _build_args(config)
    if argv is None:
        raise SystemExit(1)
    raise SystemExit(main(argv))
