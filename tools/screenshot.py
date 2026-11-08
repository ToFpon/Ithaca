#!/usr/bin/env python3
"""Capture de la fenêtre d'Ithaca (à lancer sous xvfb-run) pour la documentation et les tests.

    xvfb-run -a python3.12 tools/screenshot.py SORTIE.png [options]

Options : --empty (page d'accueil) --dark --recolor --size=LxH --prefs=page --delay=ms --hidden-list
          --search=texte (recherche dans la file) --selection (mode sélection, un morceau sur deux)
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

from ithaca import features  # noqa: E402
from ithaca.app import IthacaApp  # noqa: E402

out = os.path.abspath(sys.argv[1])
opts = {}
for a in sys.argv[2:]:
    if a.startswith("--"):
        k, _, v = a[2:].partition("=")
        opts[k] = v
delay = int(opts.get("delay", 3500))


def song(path: str, seconds: int, freq: int, tags: dict, cover: str):
    """Un son dont le volume varie (forme d'onde vivante, pas une barre pleine comme un sinus)."""
    import subprocess
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shape = f"volume='0.15+0.85*abs(sin(t*{0.7 + freq / 900:.2f}))*abs(cos(t*0.23))':eval=frame"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
           "-i", cover, "-map", "0:a", "-map", "1:v", "-af", shape, "-c:v", "mjpeg",
           "-disposition:v", "attached_pic", "-c:a", "libmp3lame", "-id3v2_version", "3"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run(cmd + [path], check=True)


def demo_library() -> list[str]:
    """Deux albums aux pochettes colorées, l'Odyssée en musique."""
    root = os.path.join(TMP, "Musique")
    import subprocess
    albums = (
        ("Ulysse", "Le retour à Ithaque", "mandelbrot=size=600x600",
         ["Le chant des sirènes", "Calypso", "La tempête", "Pénélope tisse", "L'arc d'Ulysse", "Ithaque"]),
        ("Pénélope", "La toile et l'attente",
         "mandelbrot=size=600x600:start_x=-0.743643887:start_y=0.131825904:start_scale=0.004:inner=period",
         ["Le métier à tisser", "Les prétendants", "Argos le fidèle", "Le retour du roi"]),
    )
    folders = []
    for n, (artist, album, source, titles) in enumerate(albums):
        art = os.path.join(TMP, f"art{n}.png")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source, "-frames:v", "1", art],
                       check=True)
        folder = os.path.join(root, artist, album)
        folders.append(folder)
        for i, t in enumerate(titles, 1):
            song(os.path.join(folder, f"{i:02d} - {t}.mp3"), 40 + 17 * i, 180 + 70 * i + 40 * n,
                 {"title": t, "artist": artist, "album": album, "track": str(i)}, art)
    return folders                      # Ulysse d'abord


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
        GLib.timeout_add(1800, lambda: (app.controller.seek(21.0), False)[1])
    if "search" in opts:
        def search():
            win.split.set_show_sidebar(True)
            win.playlist.search_btn.set_active(True)
            win.playlist.search_entry.set_text(opts["search"])
            return False
        GLib.timeout_add(delay - 1200, search)
    if "selection" in opts:
        def select():
            win.playlist.set_selection_mode(True)
            for i in range(0, win.playlist.store.get_n_items(), 2):
                win.playlist.store.get_item(i).selected = True
            win.playlist._refresh_checks()
            return False
        GLib.timeout_add(delay - 1200, select)
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
