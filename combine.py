#!/usr/bin/env python3
"""Combina series.m3u + vod.m3u + itv.m3u en global.m3u.

Cada seccion que no exista se omite. El encabezado #EXTM3U se escribe una
sola vez.
"""

import os


SECTIONS = ("series.m3u", "vod.m3u", "itv.m3u")


def _lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    if lines and lines[0].startswith("#EXTM3U"):
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def combine(out="global.m3u"):
    tmp = out + ".tmp"
    counts = {}
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for sec in SECTIONS:
            lines = _lines(sec)
            counts[sec] = len(lines)
            for ln in lines:
                fh.write(ln + "\n")
    os.replace(tmp, out)
    for sec in SECTIONS:
        print("  %s: %d lineas" % (sec, counts.get(sec, 0)))
    print("[+] Combinado en %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(combine())
