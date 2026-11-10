#!/usr/bin/env python3
"""Vrais gestes de souris (xdotool) : glisser-déposer dans la file (la 1re ligne déposée sur le bas de la
3e), puis molette — volume sur tout le lecteur, simple défilement sur la file.

    xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_drag.py     # doit finir par « Aucune erreur. »

Pourquoi un vrai glisser : un Gtk.WindowHandle autour de toute la fenêtre s'emparait du geste (la
fenêtre se déplaçait, rien n'était réordonné) — invisible quand on appelle move_song directement.
Le geste est joué depuis un autre fil : la boucle GTK doit traiter les mouvements au fil de l'eau.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="ithaca-drag-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi  # noqa: E402

gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene  # noqa: E402

import media  # noqa: E402
from ithaca.app import IthacaApp  # noqa: E402

if shutil.which("xdotool") is None:
    print("xdotool absent : test sauté (sudo apt install xdotool)")
    sys.exit(0)

lib = media.library(os.path.join(TMP, "lib"))
app = IthacaApp(os.path.join(TMP, "queue.json"))
errors = []


def xdo(*args):
    subprocess.run(["xdotool", *map(str, args)], check=False)


def check(cond, msg):
    print(("✓ " if cond else "✗ ") + msg, flush=True)
    if not cond:
        errors.append("ÉCHEC : " + msg)


def screen_point(win, widget, x, y):
    p = widget.compute_point(win, Graphene.Point().init(x, y))[1]
    sx, sy = win.get_native().get_surface_transform()
    wid = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", str(os.getpid())],
                         capture_output=True, text=True).stdout.split()[-1]
    geo = subprocess.run(["xdotool", "getwindowgeometry", wid], capture_output=True, text=True).stdout
    ox, oy = (int(v) for v in geo.split("Position:")[1].split()[0].split(","))
    return int(ox + sx + p.x), int(oy + sy + p.y)


def drag(a, b):
    def run():
        xdo("mousemove", *a)
        time.sleep(0.2)
        xdo("mousedown", 1)
        time.sleep(0.2)
        for k in range(1, 11):
            xdo("mousemove", a[0] + (b[0] - a[0]) * k // 10, a[1] + (b[1] - a[1]) * k // 10)
            time.sleep(0.05)
        time.sleep(0.3)
        xdo("mouseup", 1)
    threading.Thread(target=run, daemon=True).start()


def go(win):
    if getattr(app, "_go", False):
        return False
    app._go = True
    c = app.controller
    win.add_paths([lib["album_a"], lib["album_b"]], play=False)

    def start():
        if len(c.queue) < 4:
            return True                               # répétition voulue : on attend la file
        pv = win.playlist
        before = list(c.queue.items)
        rows = {id(box._item.song): box for box in pv._rows if box._item is not None}
        src, dst = rows[id(before[0])], rows[id(before[2])]
        drag(screen_point(win, src, src.get_width() / 2, src.get_height() / 2),
             screen_point(win, dst, dst.get_width() / 2, dst.get_height() * 0.8))

        def verify():
            check(c.queue.items == before[1:3] + [before[0]] + before[3:],
                  "vrai glisser : 1er morceau déposé après le 3e")
            check(not any(b.has_css_class("drop-above") or b.has_css_class("drop-below") for b in pv._rows),
                  "ligne d'insertion retirée après le dépôt")
            wheel_tests()
            return False
        GLib.timeout_add(3000, verify)
        return False

    def wheel_tests():
        """Vraie molette : sur tout le lecteur (ici la pochette), elle règle le volume ; sur la file, elle
        la fait défiler sans toucher au volume."""
        from ithaca.settings import settings
        settings["wheel_step"] = 10
        settings["volume"] = 0.5
        on_cover = screen_point(win, win.cover, win.cover.get_width() / 2, win.cover.get_height() / 2)
        row = win.playlist._rows[0]
        on_list = screen_point(win, row, row.get_width() / 2, row.get_height() / 2)

        def run():
            xdo("mousemove", *on_cover)
            time.sleep(0.3)
            xdo("click", 5)                           # molette vers le bas : -10 %
            time.sleep(0.5)
            GLib.idle_add(lambda: (results.append(settings["volume"]), False)[1])
            time.sleep(0.3)
            xdo("mousemove", *on_list)
            time.sleep(0.3)
            xdo("click", 5)
            time.sleep(0.5)
            GLib.idle_add(lambda: (results.append(settings["volume"]), False)[1])
            time.sleep(0.3)
            GLib.idle_add(done)
        results = []

        def done():
            check(results and abs(results[0] - 0.4) < 1e-6, f"molette sur la pochette : volume -10 % ({results[:1]})")
            check(len(results) > 1 and abs(results[1] - 0.4) < 1e-6, "molette sur la file : volume inchangé")
            app._quit()
            return False
        threading.Thread(target=run, daemon=True).start()
    GLib.timeout_add(1500, start)
    GLib.timeout_add(20000, lambda: (errors.append("ÉCHEC : délai dépassé"), app._quit(), False)[-1])
    return False


app.connect("window-added", lambda _a, w: GLib.idle_add(go, w))
app.run([sys.argv[0]])
shutil.rmtree(TMP, ignore_errors=True)
print("\n".join(errors) if errors else "\nAucune erreur.")
sys.exit(1 if errors else 0)
