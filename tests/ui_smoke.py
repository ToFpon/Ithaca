#!/usr/bin/env python3
"""Test de fumée de l'interface : pilote une vraie fenêtre (sous xvfb-run) et signale toute exception.

    xvfb-run -a python3.12 tests/ui_smoke.py        # doit finir par « Aucune erreur. »
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
TMP = tempfile.mkdtemp(prefix="ithaca-ui-")
os.environ["HOME"] = os.path.join(TMP, "home")
for var, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[var] = os.path.join(TMP, "home", sub)
os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
os.makedirs(os.environ["HOME"])

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

errors = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)


sys.excepthook = hook

import media  # noqa: E402
from ithaca import features  # noqa: E402
from ithaca.app import IthacaApp  # noqa: E402
from ithaca.i18n import tr  # noqa: E402
from ithaca.settings import settings  # noqa: E402

LIB = media.library(os.path.join(TMP, "lib"))
app = IthacaApp()
steps = []
log = []


def step(fn):
    steps.append(fn)
    return fn


class Wait:
    """Les étapes sont des générateurs : « yield Wait(s) » rend la main à la vraie boucle GLib."""
    def __init__(self, seconds=0.4, until=None):
        self.seconds = seconds
        self.until = until


def pump(seconds=0.4):
    return Wait(seconds)


CRITICALS = []
_err_r, _err_w = os.pipe()
_err_orig = os.dup(2)
os.dup2(_err_w, 2)


def _tee_stderr():
    with os.fdopen(_err_r, "rb", 0) as f:
        for line in iter(f.readline, b""):
            os.write(_err_orig, line)
            if b"-CRITICAL **" in line:
                CRITICALS.append(line.decode("utf-8", "replace").strip())


threading.Thread(target=_tee_stderr, daemon=True).start()


def check(cond, msg):
    if not cond:
        errors.append("ÉCHEC : " + msg)
        print("✗", msg)
    else:
        print("✓", msg)


def run_all(win):
    def driver():
        for fn in steps:
            log.append(fn.__name__)
            gen = fn(win)
            if gen is not None and hasattr(gen, "__next__"):
                yield from gen
            yield Wait(0.2)

    it = driver()

    def tick():
        try:
            w = next(it)
        except StopIteration:
            app._quit()
            return False
        except Exception:
            errors.append(traceback.format_exc())
            app._quit()
            return False
        start = time.time()

        def poll():
            if w.until is not None:
                if w.until() or time.time() - start > w.seconds:
                    GLib.timeout_add(50, tick)
                    return False
                return True
            GLib.timeout_add(50, tick)
            return False
        if w.until is not None:
            GLib.timeout_add(50, poll)
        else:
            GLib.timeout_add(int(w.seconds * 1000), poll)
        return False
    tick()
    return False


def dialogs(win):
    return [w for w in Gtk.Window.list_toplevels() if w is not win and w.get_visible()
            and w.get_transient_for() is win]


def c():
    return app.controller


def visible_rows(win):
    return [win.playlist.filtered.get_item(i).song for i in range(win.playlist.filtered.get_n_items())]


@step
def accueil(win):
    check(win.stack.get_visible_child_name() == "empty", "page d'accueil sans musique")
    check(not win.restore_btn.get_visible(), "pas de file à restaurer au premier lancement")
    check(not win.lookup_action("play").get_enabled(), "lecture inactive sans morceau")


@step
def ajout_dossier(win):
    win.add_paths([LIB["album_a"]], play=True)
    yield Wait(10, until=lambda: len(c().queue) == 3 and c().playing)
    check(win.stack.get_visible_child_name() == "main", "lecteur affiché")
    check(len(c().queue) == 3 and c().playing, "album ajouté et lu")
    check(win.title_label.get_label() == "Ouverture", "titre du morceau")
    check(win.artist_label.get_label() == "Les Testeurs", "artiste")
    check(win.cover.get_visible_child_name() == "cover", "pochette intégrée affichée")
    check(win.playlist.store.get_n_items() == 3, "file de lecture remplie")
    check(win.play_btn.get_icon_name() == "media-playback-pause-symbolic", "bouton pause pendant la lecture")
    yield Wait(10, until=lambda: bool(win.waveform.peaks))
    check(bool(win.waveform.peaks), "forme d'onde calculée")


@step
def commandes(win):
    win.activate_action("win.play", None)
    yield pump(0.3)
    check(not c().playing, "pause")
    win.activate_action("win.next", None)
    yield pump(0.3)
    check(c().queue.pos == 1 and win.title_label.get_label() == "Traversée", "morceau suivant")
    win.activate_action("win.previous", None)
    yield pump(0.3)
    check(c().queue.pos == 0, "morceau précédent")
    win.waveform.emit("seek", 1.0)
    yield pump(0.4)
    check(abs(c().player.position() - 1.0) < 0.3, "clic dans la forme d'onde : déplacement")
    win._on_key(None, Gdk.KEY_space, 0, 0)
    yield Wait(3, until=lambda: c().playing)
    check(c().playing, "Espace : lecture")
    win._on_key(None, Gdk.KEY_space, 0, 0)
    yield pump(0.3)
    check(not c().playing, "Espace : pause")


@step
def modes(win):
    win.shuffle_btn.set_active(True)
    yield pump(0.2)
    check(c().queue.shuffled and settings["shuffle"], "aléatoire")
    check(c().queue.items[0] is c().song, "le morceau courant reste en tête")
    win.shuffle_btn.set_active(False)
    icons = []
    for _ in range(3):
        win.repeat_btn.emit("clicked")
        yield pump(0.1)
        icons.append(win.repeat_btn.get_icon_name())
    check(icons == ["media-playlist-repeat-symbolic", "media-playlist-repeat-song-symbolic",
                    "media-playlist-consecutive-symbolic"], f"répétition : tout, morceau, aucune ({icons})")
    check(settings["repeat"] == "consecutive", "mode de répétition enregistré")


@step
def volume(win):
    win.volume_scale.set_value(0.5)
    yield pump(0.2)
    check(abs(settings["volume"] - 0.5) < 0.01, "curseur de volume")
    win.mute_btn.emit("clicked")
    yield pump(0.2)
    check(settings["muted"] and win.mute_btn.get_icon_name() == "audio-volume-muted-symbolic", "son coupé")
    win.mute_btn.emit("clicked")
    win.volume_scale.set_value(1.0)


@step
def ajout_et_illisible(win):
    win.add_paths([LIB["album_b"], LIB["bare"]])
    yield Wait(10, until=lambda: len(c().queue) == 6)
    check(len(c().queue) == 6, "second album et fichier sans étiquette ajoutés à la fin")
    check(win.playlist.subtitle.get_label().startswith("6"), "nombre de morceaux dans l'en-tête")
    win.add_paths([LIB["fake"]])
    yield pump(1.0)
    check(len(c().queue) == 6, "faux fichier audio ignoré")
    win.add_paths([LIB["album_a"]])
    yield pump(1.0)
    check(len(c().queue) == 6, "déjà dans la file : pas de doublon")


@step
def recherche(win):
    win.activate_action("win.search", None)
    yield pump(0.2)
    check(win.playlist.search_bar.get_search_mode(), "Ctrl+F ouvre la recherche")
    win.playlist.search_entry.set_text("matin")
    yield pump(0.5)
    rows = visible_rows(win)
    check(len(rows) == 1 and rows[0].title == "Matin", "recherche dans la file")
    win.playlist.search_entry.set_text("traversee")
    yield pump(0.5)
    check([s.title for s in visible_rows(win)] == ["Traversée"], "recherche sans accents")
    win.playlist.search_entry.emit("activate")
    yield Wait(3, until=lambda: c().song and c().song.title == "Traversée")
    check(c().song.title == "Traversée" and c().playing, "Entrée joue le premier résultat")
    # Ctrl+← dans le champ : mot précédent, pas « morceau précédent » (raccourci de l'application)
    win.playlist.search_entry.set_text("bonjour monde")
    win.playlist.search_entry.grab_focus()
    yield pump(0.2)
    text = win.get_focus()
    text.set_position(-1)
    song = c().song
    win._on_key(None, Gdk.KEY_Left, 0, Gdk.ModifierType.CONTROL_MASK)
    check(isinstance(text, Gtk.Text) and text.get_position() == len("bonjour "), "Ctrl+← dans la recherche : mot précédent")
    win._on_key(None, Gdk.KEY_Right, 0, Gdk.ModifierType.SHIFT_MASK)
    check(text.get_selection_bounds() == (8, 9), "Maj+→ dans la recherche : sélection")
    check(c().song is song, "le morceau ne change pas")
    win.playlist.search_entry.set_text("introuvable")
    yield pump(0.5)
    check(win.playlist.stack.get_visible_child_name() == "empty", "aucun résultat")
    win.playlist.search_entry.set_text("")
    win.playlist.search_btn.set_active(False)
    yield pump(0.3)


@step
def clic_dans_la_file(win):
    win.playlist._on_activate(None, 3)
    yield Wait(3, until=lambda: c().song is c().queue.items[3])
    check(c().song.title == "Matin", "clic sur une ligne : lecture")
    check(win.cover.get_visible_child_name() == "cover", "pochette du dossier (cover.jpg)")
    from ithaca.ui.common import popup_menu
    rows = [r for r in win.playlist._rows if r._item is not None]
    if rows:
        pop = popup_menu(rows[0], Gio.Menu(), 10, 10)
        yield pump(0.2)
        pop.popdown()
    win.activate_action("win.remove-index", GLib.Variant.new_int32(5))
    yield pump(0.3)
    check(len(c().queue) == 5, "retirer un morceau depuis son menu")


@step
def recoloration(win):
    check(not win.has_css_class("recolored"), "couleurs du thème par défaut")
    win.activate_action("win.recolor", None)
    yield pump(0.3)
    check(win.has_css_class("recolored") and win.recolor.colors, "couleurs de la pochette")
    win.activate_action("win.recolor", None)
    yield pump(0.3)
    check(not win.has_css_class("recolored"), "retour au thème")


@step
def replaygain(win):
    win.activate_action("win.replaygain", GLib.Variant.new_string("track"))
    yield pump(0.2)
    check(settings["replaygain"] == "track", "ReplayGain : morceau")
    win.activate_action("win.replaygain", GLib.Variant.new_string("album"))


@step
def preferences(win):
    app.activate_action("preferences", None)
    yield pump(0.8)
    ds = [w for w in dialogs(win) if isinstance(w, Adw.PreferencesWindow)]
    check(len(ds) == 1, "préférences ouvertes")
    if ds:
        ds[0].set_visible_page_name("features")
        ds[0].feature_rows["waveform"].set_active(False)
        yield pump(0.3)
        check(not win.waveform.peaks, "forme d'onde désactivée sans redémarrer")
        ds[0].feature_rows["waveform"].set_active(True)
        yield Wait(10, until=lambda: bool(win.waveform.peaks))
        check(bool(win.waveform.peaks), "forme d'onde réactivée")
        ds[0].close()
    win.activate_action("win.show-help-overlay", None)
    yield pump(0.5)
    helps = [w for w in Gtk.Window.list_toplevels() if isinstance(w, Gtk.ShortcutsWindow) and w.get_visible()]
    check(len(helps) == 1, "raccourcis clavier")
    for w in helps:
        w.close()


@step
def deposer(win):
    files = Gdk.FileList.new_from_list([Gio.File.new_for_path(LIB["b2"])])
    n = len(c().queue)
    win._on_drop(None, files, 0, 0)
    yield pump(1.0)
    check(len(c().queue) == n or any(s.path == LIB["b2"] for s in c().queue.items), "fichier déposé")


@step
def arriere_plan(win):
    c().play()
    yield Wait(3, until=lambda: c().playing)
    features.set_enabled("background", True)
    win.close()
    yield pump(0.5)
    check(not win.get_visible() and c().playing, "fermer pendant la lecture : la musique continue")
    app.present()
    yield pump(0.4)
    check(win.get_visible(), "la fenêtre revient")
    c().pause()


@step
def restauration(win):
    c().save()
    saved = [s.path for s in c().queue.items]
    c().player.stop()
    c().queue.clear()                       # vidée sans enregistrer : la file enregistrée reste
    yield pump(0.4)
    check(win.stack.get_visible_child_name() == "empty", "file vide : page d'accueil")
    check(win.restore_btn.get_visible(), "« Restaurer la file » proposé")
    win.activate_action("win.restore", None)
    yield Wait(10, until=lambda: len(c().queue) == len(saved))
    check([s.path for s in c().queue.items] == saved, "file restaurée")


@step
def listes_de_lecture(win):
    from ithaca.backend import playlists
    from ithaca.backend.song import Song
    from ithaca.ui.window import artist_line
    folder = os.path.join(TMP, "listes")
    os.makedirs(folder)
    songs = list(c().queue.items)
    path = win.write_playlist(songs, os.path.join(folder, "Voyage.xspf"))
    check(path and len(playlists.parse(path)) == len(songs), "file enregistrée en XSPF")
    m3u = win.write_playlist(songs[:2], os.path.join(folder, "Deux"))
    check(m3u.endswith("Deux.m3u"), "extension .m3u ajoutée")
    win.append_to_playlist(songs, m3u)
    check(len(playlists.parse(m3u)) == len(songs), "liste complétée sans doublon")
    n = len(c().queue)
    c().clear()
    yield pump(0.3)
    win.add_paths([path])
    yield Wait(10, until=lambda: len(c().queue) == n)
    check(len(c().queue) == n, "liste ouverte : morceaux dans la file")
    win.playlist.set_selection_mode(True)
    check(not win.playlist.save_btn.get_sensitive(), "rien de sélectionné : enregistrement inactif")
    win.playlist.select_all()
    check(win.playlist.save_btn.get_sensitive(), "sélection : « Enregistrer comme liste »")
    win.playlist.set_selection_mode(False)
    radio = Song("http://radio.example/flux", title="Radio Ithaque")
    check(artist_line(radio) == tr("Webradio") and radio.display_title == "Radio Ithaque", "webradio affichée")
    radio.now_playing = "Ouverture"
    check(artist_line(radio) == "Radio Ithaque" and radio.display_title == "Ouverture",
          "titre annoncé par la webradio, station en dessous")


@step
def selection(win):
    win.playlist.set_selection_mode(True)
    yield pump(0.2)
    check(win.playlist.action_bar.get_revealed(), "mode sélection")
    win.playlist.select_all()
    check(len(win.playlist.selected_songs()) == len(c().queue), "tout sélectionner")
    win.playlist.remove_selected()
    yield pump(0.4)
    check(len(c().queue) == 0 and win.stack.get_visible_child_name() == "empty", "tout retirer : page d'accueil")


def on_window(_a, w):
    if not log:
        log.append("start")
        GLib.timeout_add(500, run_all, w)


app.connect("window-added", on_window)
app.run([sys.argv[0]])
shutil.rmtree(TMP, ignore_errors=True)
sys.stderr.flush()
time.sleep(0.2)
for line in CRITICALS:
    errors.append("ÉCHEC : avertissement critique de GTK / GLib : " + line)
print("\n".join(errors) if errors else "\nAucune erreur.")
sys.exit(1 if errors else 0)
