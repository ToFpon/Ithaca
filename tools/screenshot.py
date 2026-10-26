#!/usr/bin/env python3
"""Capture de la fenêtre d'Ithaca (à lancer sous xvfb-run) pour la documentation et les tests.

    xvfb-run -a python3.12 tools/screenshot.py SORTIE.png [options]

Options : --torrent (fenêtre de choix sur un torrent d'exemple) --video (fenêtre de choix sur une vidéo locale ; yt-dlp : ITHACA_BIN_DIR) --demo (serveur local et téléchargements d'exemple : terminés, en cours, en pause, échec)
          --filter=all|progress|complete|failed --details --prefs=page --dark --delay=ms --size=LxH
Configuration et état dans un dossier jetable : les vrais téléchargements ne sont jamais touchés.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="ithaca-shot-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

from ithaca.app import IthacaApp  # noqa: E402
from ithaca.settings import settings  # noqa: E402

out = os.path.abspath(sys.argv[1])
opts = {}
for a in sys.argv[2:]:
    if a.startswith("--"):
        k, _, v = a[2:].partition("=")
        opts[k] = v
delay = int(opts.get("delay", 4000))
DOWNLOADS = os.path.join(TMP, "Téléchargements")
os.makedirs(DOWNLOADS)
settings.data["download_dir"] = DOWNLOADS


def demo(win):
    from server import Server
    srv_dir = os.path.join(TMP, "srv")
    os.makedirs(srv_dir)
    for name, size in (("ubuntu-24.04.1-desktop-amd64.iso", 6_000_000), ("Rapport annuel 2026.pdf", 900_000),
                       ("vacances-ete.zip", 3_000_000), ("podcast-episode-42.mp3", 2_500_000),
                       ("firmware-v2.bin", 4_000_000)):
        with open(os.path.join(srv_dir, name), "wb") as fh:
            fh.write(os.urandom(size))
    fast = Server(srv_dir)
    slow = Server(srv_dir, rate=60_000)
    win._servers = (fast, slow)
    m = win.manager
    m.add(fast.url("Rapport annuel 2026.pdf"))
    m.add(fast.url("vacances-ete.zip"))
    m.add(fast.url("introuvable.tar.gz"))
    m.add(slow.url("firmware-v2.bin"))
    d = m.add(slow.url("podcast-episode-42.mp3"))
    GLib.timeout_add(1500, lambda: (m.pause(d), False)[1])
    m.add(slow.url("ubuntu-24.04.1-desktop-amd64.iso"))


def snap(win):
    def do():
        target = win
        paint = Gtk.WidgetPaintable.new(target)
        w, h = target.get_width(), target.get_height()
        snapshot = Gtk.Snapshot()
        paint.snapshot(snapshot, w, h)
        # les dialogues sont de vraies fenêtres : posés au centre, par-dessus
        for d in Gtk.Window.list_toplevels():
            if d is not win and d.get_visible() and d.get_transient_for() is win:
                dw, dh = d.get_width(), d.get_height()
                snapshot.save()
                snapshot.translate(Graphene.Point().init(max((w - dw) / 2, 0), max((h - dh) / 2, 0)))
                Gtk.WidgetPaintable.new(d).snapshot(snapshot, dw, dh)
                snapshot.restore()
        tex = target.get_renderer().render_texture(snapshot.to_node(), None)
        tex.save_to_png(out)
        print("capture", out, w, h)
        win._closing = True
        win.get_application().quit()
        return False
    GLib.timeout_add(delay, do)


app = IthacaApp(os.path.join(TMP, "downloads.json"))


def on_ready(win):
    if getattr(app, "_shot", False):
        return False
    app._shot = True
    if "dark" in opts:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    w, _, h = opts.get("size", "980x640").partition("x")
    win.set_default_size(int(w), int(h))
    if "demo" in opts:
        GLib.timeout_add(600, lambda: (demo(win), False)[1])
    if "torrent" in opts:
        import libtorrent as lt
        tdir = os.path.join(TMP, "tsrc", "Concert à Ithaque (2026)")
        os.makedirs(os.path.join(tdir, "Bonus"))
        for name, size in (("01 - Ouverture.flac", 31_000_000), ("02 - La traversée.flac", 42_000_000),
                           ("03 - Retour.flac", 28_000_000), ("Bonus/Interview.mkv", 380_000_000),
                           ("pochette.jpg", 900_000)):
            with open(os.path.join(tdir, name), "wb") as fh:
                fh.truncate(size)
        fs = lt.file_storage()
        lt.add_files(fs, tdir)
        ct = lt.create_torrent(fs)
        lt.set_piece_hashes(ct, os.path.dirname(tdir))
        tpath = os.path.join(TMP, "concert.torrent")
        with open(tpath, "wb") as fh:
            fh.write(lt.bencode(ct.generate()))
        GLib.timeout_add(600, lambda: (win.open_torrent(tpath), False)[1])
    if "video" in opts:
        import subprocess
        from server import Server
        vdir = os.path.join(TMP, "vsrv")
        os.makedirs(vdir)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
                        "-f", "lavfi", "-i", "sine", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", os.path.join(vdir, "Voyage à Ithaque.mp4")], check=True)
        win._vserver = Server(vdir)
        GLib.timeout_add(600, lambda: (win.open_video(win._vserver.url("Voyage à Ithaque.mp4")), False)[1])

    def later():
        if "filter" in opts:
            ids = [f[0] for f in __import__("ithaca.ui.window", fromlist=["FILTERS"]).FILTERS]
            win.filter_list.select_row(win.filter_list.get_row_at_index(ids.index(opts["filter"])))
        if "details" in opts and win.manager.downloads:
            win.activate_action("win.dl-details", GLib.Variant.new_string(win.manager.downloads[-1].id))
        if "prefs" in opts:
            from ithaca.ui.preferences import PreferencesWindow
            d = PreferencesWindow()
            if opts["prefs"]:
                d.set_visible_page_name(opts["prefs"])
            d.set_transient_for(win)
            d.present()
        return False
    GLib.timeout_add(max(700, delay - 1200), later)
    snap(win)
    return False


app.connect("window-added", lambda _a, w: GLib.idle_add(on_ready, w))
status = app.run([sys.argv[0]])
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(status)
