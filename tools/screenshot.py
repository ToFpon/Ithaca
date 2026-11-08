#!/usr/bin/env python3
"""Capture de la fenêtre d'Ithaca (à lancer sous xvfb-run) pour la documentation et les tests.

    xvfb-run -a python3.12 tools/screenshot.py SORTIE.png [options]

Options : --empty (page d'accueil) --dark --recolor --size=LxH --prefs=page --delay=ms --hidden-list
Musique d'exemple générée par ffmpeg, sortie son factice, configuration jetable.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="ithaca-shot-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

import media  # noqa: E402
from ithaca import features  # noqa: E402
from ithaca.app import IthacaApp  # noqa: E402

out = os.path.abspath(sys.argv[1])
opts = {}
for a in sys.argv[2:]:
    if a.startswith("--"):
        k, _, v = a[2:].partition("=")
        opts[k] = v
delay = int(opts.get("delay", 3500))


def demo_library() -> list[str]:
    """Un album avec une pochette colorée, pour que l'on voie quelque chose."""
    root = os.path.join(TMP, "Musique")
    art = media.image(os.path.join(TMP, "art.png"), "600x600", "mandelbrot")
    album = os.path.join(root, "Ulysse", "Le retour à Ithaque")
    titles = ["Le chant des sirènes", "Calypso", "La tempête", "Pénélope tisse", "L'arc d'Ulysse", "Ithaque"]
    for i, t in enumerate(titles, 1):
        media.tone(os.path.join(album, f"{i:02d} - {t}.mp3"), 4 + i, 220 + 60 * i,
                   {"title": t, "artist": "Ulysse", "album": "Le retour à Ithaque", "track": str(i)}, cover=art)
    return [root]


def snap(win):
    def do():
        paint = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        snapshot = Gtk.Snapshot()
        paint.snapshot(snapshot, w, h)
        for d in Gtk.Window.list_toplevels():
            if d is not win and d.get_visible() and d.get_transient_for() is win:
                dw, dh = d.get_width(), d.get_height()
                snapshot.save()
                snapshot.translate(Graphene.Point().init(max((w - dw) / 2, 0), max((h - dh) / 2, 0)))
                Gtk.WidgetPaintable.new(d).snapshot(snapshot, dw, dh)
                snapshot.restore()
        tex = win.get_renderer().render_texture(snapshot.to_node(), None)
        tex.save_to_png(out)
        print("capture", out, w, h)
        app._quit()
        return False
    GLib.timeout_add(delay, do)


app = IthacaApp(os.path.join(TMP, "queue.json"))


def on_ready(win):
    if getattr(app, "_shot", False):
        return False
    app._shot = True
    if "dark" in opts:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    if "recolor" in opts:
        features.set_enabled("recolor", True)
    w, _, h = opts.get("size", "860x700").partition("x")
    win.set_default_size(int(w), int(h))
    if "hidden-list" in opts:
        win.split.set_show_sidebar(False)
    if "empty" not in opts:
        paths = demo_library()
        GLib.timeout_add(300, lambda: (win.add_paths(paths, play=True), False)[1])
        GLib.timeout_add(1800, lambda: (app.controller.seek(2.3), False)[1])
    if "prefs" in opts:
        def prefs():
            from ithaca.ui.preferences import PreferencesWindow
            d = PreferencesWindow()
            if opts["prefs"]:
                d.set_visible_page_name(opts["prefs"])
            d.set_transient_for(win)
            d.present()
            return False
        GLib.timeout_add(delay - 1000, prefs)
    snap(win)
    return False


app.connect("window-added", lambda _a, w: GLib.idle_add(on_ready, w))
status = app.run([sys.argv[0]])
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(status)
