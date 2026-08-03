#!/usr/bin/env python3
"""Portal simulado para probar vod_m3u.py, itv_m3u.py y combine.py.

Ejecucion:
    python mock_vod_itv.py
"""

import json
import os
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import combine
import itv_m3u
import vod_m3u

TOKEN = "mocktoken456"
MAC = "00:1A:79:AA:BB:CC"

VOD_CATS = [
    {"id": "100", "title": "|ES| ACCION"},
    {"id": "200", "title": "|ES| LATINO"},
    {"id": "300", "title": "|FR| DRAME"},
    {"id": "400", "title": "|FR| DOCUMENTAIRE"},
    {"id": "500", "title": "|QC| FILMS"},
]

VOD_MOVIES = {
    "100": [
        {"id": "9001", "name": "|ES| Pelicula Uno", "cmd": "/media/m9001.mpg"},
        {"id": "9002", "name": "|ES| Pelicula Dos", "cmd": "/media/m9002.mpg"},
    ],
    "300": [
        {"id": "9001", "name": "|FR| Pelicula Uno", "cmd": "/media/m9001.mpg"},
        {"id": "9003", "name": "|FR| Drame Tres", "cmd": "/media/m9003.mpg"},
    ],
}

ITV_GENRES = [
    {"id": "700", "title": "ES| TDT ESPANA"},
    {"id": "701", "title": "FR| FRANCE HD"},
    {"id": "702", "title": "FR| DAZN PPV"},
    {"id": "703", "title": "UK| NEWS"},
    {"id": "704", "title": "UK| MOVIES"},
    {"id": "705", "title": "IR| IRELAND"},
]

ITV_CHANNELS = {
    "700": [
        {"id": "8001", "name": "##### TDT ESPANA #####", "cmd": "ffmpeg http://cdn.example.com/live/1.ts", "logo": ""},
        {"id": "8002", "name": "ES| LA 1 HD", "cmd": "ffmpeg http://cdn.example.com/live/2.ts", "logo": "http://cdn.example.com/la1.png"},
        {"id": "8003", "name": "ES| ANTENA 3 HD", "cmd": "ffmpeg http://cdn.example.com/live/3.ts", "logo": ""},
    ],
    "701": [
        {"id": "8010", "name": "FR| TF1 HD", "cmd": "ffmpeg http://cdn.example.com/live/10.ts", "logo": ""},
    ],
    "702": [
        {"id": "8020", "name": "FR| DAZN 1", "cmd": "ffmpeg http://cdn.example.com/live/20.ts", "logo": ""},
    ],
    "703": [
        {"id": "8030", "name": "UK| BBC NEWS", "cmd": "ffmpeg http://cdn.example.com/live/30.ts", "logo": ""},
    ],
    "704": [
        {"id": "8040", "name": "UK| SKY MOVIES", "cmd": "ffmpeg http://cdn.example.com/live/40.ts", "logo": ""},
    ],
    "705": [
        {"id": "8050", "name": "IR| VIRGIN 1", "cmd": "ffmpeg http://cdn.example.com/live/50.ts", "logo": ""},
    ],
}


class MockPortal(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        action = (params.get("action") or [""])[0]
        stype = (params.get("type") or [""])[0]

        if stype == "stb" and action == "handshake":
            self._reply({"js": {"token": TOKEN, "random": {}}})
            return

        if stype == "vod" and action == "get_categories":
            self._reply({"js": {"data": VOD_CATS}})
            return
        if stype == "vod" and action == "get_ordered_list":
            cat = (params.get("category") or [""])[0]
            page = int((params.get("p") or ["1"])[0])
            data = VOD_MOVIES.get(cat, []) if page == 1 else []
            self._reply({"js": {"total_items": len(VOD_MOVIES.get(cat, [])), "data": data}})
            return
        if stype == "vod" and action == "create_link":
            cmd = (params.get("cmd") or [""])[0]
            media_id = cmd.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            url = "http://cdn.example.com/vod/movie-%s.mkv?token=%s" % (media_id, TOKEN)
            self._reply({"js": {"cmd": "ffmpeg 3:0 " + url}})
            return

        if stype == "itv" and action == "get_genres":
            self._reply({"js": ITV_GENRES})
            return
        if stype == "itv" and action == "get_ordered_list":
            genre = (params.get("genre") or [""])[0]
            page = int((params.get("p") or ["1"])[0])
            data = ITV_CHANNELS.get(genre, []) if page == 1 else []
            self._reply({"js": {"total_items": len(ITV_CHANNELS.get(genre, [])), "data": data}})
            return

        self._reply({"js": {}})


def _run_portal():
    server = HTTPServer(("127.0.0.1", 0), MockPortal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


def _check(name, condition):
    if not condition:
        raise SystemExit("FALLO: " + name)
    print("  OK  " + name)


def _cfg(portal_url, section, extra=None):
    cfg = {
        "portal": portal_url,
        "mac": MAC,
        "no_verify": True,
        section: {
            "out": extra.get("out") if extra else None,
            "checkpoint": (extra or {}).get("checkpoint"),
            "push_interval": 0,
            "threads": 4,
            "timeout": 15,
        },
    }
    if section == "vod":
        cfg[section]["languages"] = ["ES", "FR"]
        cfg[section]["exclude"] = ["LATINO", "SPORT", "TELENOVELA", "DOCUMENTAL", "DOCUMENTAIRE"]
    return cfg


def _run_test():
    print("Arrancando portal simulado (VOD/ITV)...")
    portal_url = _run_portal()
    tmpdir = tempfile.mkdtemp(prefix="vod_itv_test_")

    print("Prueba A: VOD basico")
    out_vod = os.path.join(tmpdir, "vod.m3u")
    cfg = _cfg(portal_url, "vod", {"out": out_vod})
    orig = vod_m3u.load_config
    vod_m3u.load_config = lambda: cfg
    rc = vod_m3u.main()
    vod_m3u.load_config = orig
    _check("vod rc=0", rc == 0)
    with open(out_vod, encoding="utf-8") as fh:
        content = fh.read()
    _check("vod grupo ACCION", 'group-title="ACCION"' in content)
    _check("vod grupo DRAME", 'group-title="DRAME"' in content)
    _check("vod sin LATINO", "LATINO" not in content and "9000" not in content)
    _check("vod sin DOCUMENTAIRE", "DOCUMENTAIRE" not in content)
    _check("vod sin QC", "QC" not in content and "500" not in content.replace("800", ""))
    _check("vod pelicula UNO (9001) una sola vez (dedup)", content.count("movie-m9001.mkv") == 1)
    n_vod = sum(1 for l in content.splitlines() if l.startswith("#EXTINF"))
    _check("vod 3 peliculas unicas", n_vod == 3)
    _check("vod URLs resueltas", "http://cdn.example.com/vod/movie-" in content)
    _check("vod prefijo limpiado", 'tvg-name="Pelicula Uno"' in content)

    print("Prueba B: VOD checkpoint y reanudacion")
    ck_vod = os.path.join(tmpdir, "vod_checkpoint.json.gz")
    cfg2 = _cfg(portal_url, "vod", {"out": out_vod, "checkpoint": ck_vod})
    vod_m3u.load_config = lambda: cfg2
    rc = vod_m3u.main()
    _check("vod checkpoint rc=0", rc == 0)
    _check("vod checkpoint creado", os.path.exists(ck_vod))
    vod_m3u.load_config = orig

    print("Prueba C: ITV basico")
    out_itv = os.path.join(tmpdir, "itv.m3u")
    cfg_i = _cfg(portal_url, "itv", {"out": out_itv})
    cfg_i["itv"]["es"] = "all"
    cfg_i["itv"]["fr"] = "no_sport"
    cfg_i["itv"]["uk"] = ["GENERAL", "DOCUMENTARY", "NEWS"]
    cfg_i["itv"]["ir"] = "none"
    orig_i = itv_m3u.load_config
    itv_m3u.load_config = lambda: cfg_i
    rc = itv_m3u.main()
    itv_m3u.load_config = orig_i
    _check("itv rc=0", rc == 0)
    with open(out_itv, encoding="utf-8") as fh:
        content_i = fh.read()
    _check("itv grupo ES TDT", 'group-title="TDT ESPANA"' in content_i)
    _check("itv grupo FR HD", 'group-title="FRANCE HD"' in content_i)
    _check("itv grupo UK NEWS", 'group-title="NEWS"' in content_i)
    _check("itv sin DAZN PPV (deporte FR)", "DAZN 1" not in content_i and "DAZN PPV" not in content_i)
    _check("itv sin MOVIES UK", "SKY MOVIES" not in content_i)
    _check("itv sin IR", "VIRGIN 1" not in content_i)
    _check("itv sin separador #####", "#####" not in content_i)
    _check("itv canal con logo", 'tvg-logo="http://cdn.example.com/la1.png"' in content_i)
    _check("itv nombre limpio", 'tvg-name="LA 1 HD"' in content_i)
    _check("itv URL sin ffmpeg", "ffmpeg http://" not in content_i)
    n_itv = sum(1 for l in content_i.splitlines() if l.startswith("#EXTINF"))
    _check("itv 4 canales", n_itv == 4)

    print("Prueba D: combine (global.m3u)")
    work = tempfile.mkdtemp(prefix="combine_test_")
    with open(os.path.join(work, "series.m3u"), "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n#EXTINF:-1,Serie\nhttp://s\n")
    with open(os.path.join(work, "vod.m3u"), "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n#EXTINF:-1,Peli\nhttp://v\n")
    with open(os.path.join(work, "itv.m3u"), "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n#EXTINF:-1,Canal\nhttp://i\n")
    cwd = os.getcwd()
    os.chdir(work)
    rc = combine.combine()
    os.chdir(cwd)
    _check("combine rc=0", rc == 0)
    with open(os.path.join(work, "global.m3u"), encoding="utf-8") as fh:
        g = fh.read()
    _check("global un solo #EXTM3U", g.count("#EXTM3U") == 1)
    _check("global contiene las 3 secciones", "Serie" in g and "Peli" in g and "Canal" in g)

    print("Prueba E: combine ignora secciones ausentes")
    work2 = tempfile.mkdtemp(prefix="combine_test2_")
    with open(os.path.join(work2, "series.m3u"), "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n#EXTINF:-1,Serie\nhttp://s\n")
    os.chdir(work2)
    rc = combine.combine()
    os.chdir(cwd)
    _check("combine sin vod/itv rc=0", rc == 0)
    with open(os.path.join(work2, "global.m3u"), encoding="utf-8") as fh:
        g2 = fh.read()
    _check("global solo series", "Serie" in g2 and "Peli" not in g2 and "Canal" not in g2)

    print("\nTODAS LAS PRUEBAS PASARON")


if __name__ == "__main__":
    _run_test()
