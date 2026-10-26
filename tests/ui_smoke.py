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
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "home", ".config")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "home", ".cache")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "home", ".local", "share")
os.makedirs(os.environ["HOME"])

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

errors = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)


sys.excepthook = hook

from ithaca import features  # noqa: E402
from ithaca.app import IthacaApp  # noqa: E402
from ithaca.i18n import tr  # noqa: E402
from ithaca.settings import settings  # noqa: E402
from server import Server  # noqa: E402

SRV = os.path.join(TMP, "srv")
OUT = os.path.join(TMP, "home", "Téléchargements")
os.makedirs(SRV)
os.makedirs(OUT)
for name, size in (("petit.bin", 50_000), ("autre.bin", 80_000), ("lent.bin", 3_000_000),
                   ("à la corbeille.txt", 1000)):
    with open(os.path.join(SRV, name), "wb") as fh:
        fh.write(os.urandom(size))
fast = Server(SRV)
slow = Server(SRV, rate=50_000)
settings.data["download_dir"] = OUT

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
            finish(win)
            return False
        except Exception:
            errors.append(traceback.format_exc())
            finish(win)
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


def finish(win):
    win._closing = True
    app.quit()


def find(win, name):
    for d in win.manager.downloads:
        if d.name == name or d.url.endswith("/" + name):
            return d
    return None


def visible_rows(win):
    return [r for r in win.rows.values() if r.get_child_visible()]


def dialogs(win):
    return [w for w in Gtk.Window.list_toplevels() if w is not win and w.get_visible()
            and w.get_transient_for() is win]


@step
def ouverture(win):
    yield Wait(10, until=lambda: win.manager.engine_ready or win.manager.engine_error)
    check(win.manager.engine_ready, "aria2 démarré")
    check(win.stack.get_visible_child_name() == "empty", "page d'accueil sans téléchargement")
    check(not win.download_btn.get_sensitive(), "« Télécharger » inactif sans adresse")
    check(not win.banner.get_revealed(), "pas de bandeau d'erreur")


@step
def ajout_par_le_champ(win):
    win.url_entry.set_text(fast.url("petit.bin"))
    check(win.download_btn.get_sensitive(), "« Télécharger » actif avec une adresse")
    win.download_btn.emit("clicked")
    check(win.url_entry.get_text() == "", "champ vidé après l'ajout")
    yield Wait(10, until=lambda: find(win, "petit.bin") and find(win, "petit.bin").state == "complete")
    d = find(win, "petit.bin")
    check(d is not None and d.state == "complete", "téléchargement terminé")
    check(os.path.isfile(os.path.join(OUT, "petit.bin")), "fichier dans le dossier de téléchargement")
    check(win.stack.get_visible_child_name() == "list", "liste affichée")
    row = win.rows[d.id]
    check(row.main_btn.get_action_name() == "win.dl-open", "carte terminée : bouton Ouvrir")
    check(win.filter_counts["complete"].get_label() == "1", "compteur des terminés")


@step
def adresse_refusee(win):
    n = len(win.manager.downloads)
    win.url_entry.set_text("pas une adresse")
    win.url_entry.emit("activate")
    yield pump(0.3)
    check(len(win.manager.downloads) == n, "adresse invalide refusée")
    check(win.url_entry.get_text() == "pas une adresse", "texte gardé pour correction")
    win.url_entry.set_text("")


@step
def plusieurs_adresses(win):
    win.url_entry.set_text(fast.url("autre.bin") + "\n" + fast.url("introuvable.bin"))
    win.url_entry.emit("activate")
    yield Wait(15, until=lambda: find(win, "introuvable.bin") and find(win, "introuvable.bin").state == "error"
               and find(win, "autre.bin").state == "complete")
    check(find(win, "autre.bin").state == "complete", "deux adresses d'un coup : la bonne terminée")
    bad = find(win, "introuvable.bin")
    check(bad.state == "error", "adresse introuvable : échec")
    row = win.rows[bad.id]
    check(row.main_btn.get_action_name() == "win.dl-retry", "carte en échec : bouton Réessayer")
    check(win.filter_counts["failed"].get_label() == "1", "compteur des échecs")


@step
def nom_deja_pris(win):
    win.add_urls([fast.url("petit.bin")])
    yield Wait(10, until=lambda: os.path.exists(os.path.join(OUT, "petit (2).bin")))
    check(os.path.exists(os.path.join(OUT, "petit (2).bin")), "même nom : « petit (2).bin »")


@step
def pause_reprise(win):
    win.add_urls([slow.url("lent.bin")])
    yield Wait(10, until=lambda: find(win, "lent.bin") and find(win, "lent.bin").done > 0)
    d = find(win, "lent.bin")
    target = GLib.Variant.new_string(d.id)
    check(win.speed_label.get_label().startswith("↓"), "vitesse totale dans la barre de titre")
    win.activate_action("win.dl-pause", target)
    yield pump(1.2)
    check(d.state == "paused", "mis en pause")
    check(win.rows[d.id].main_btn.get_action_name() == "win.dl-resume", "bouton Reprendre")
    check(win.pause_all_content.get_label() == tr("Tout reprendre"), "bouton « Tout reprendre »")
    win.pause_all_btn.emit("clicked")
    yield Wait(3, until=lambda: d.state == "active")
    check(d.state == "active", "repris par « Tout reprendre »")


@step
def filtres(win):
    for i, fid in enumerate(("all", "progress", "complete", "failed")):
        win.filter_list.select_row(win.filter_list.get_row_at_index(i))
        yield pump(0.2)
        shown_ids = {r.download.id for r in visible_rows(win)}
        from ithaca.backend.downloads import matches
        expected = {d.id for d in win.manager.downloads if matches(d, fid)}
        check(shown_ids == expected, f"filtre {fid}")
    win.filter_list.select_row(win.filter_list.get_row_at_index(0))


@step
def details(win):
    d = find(win, "lent.bin")
    win.activate_action("win.dl-details", GLib.Variant.new_string(d.id))
    yield pump(1.2)
    ds = dialogs(win)
    check(len(ds) == 1 and ds[0].name.get_subtitle() == "lent.bin", "fenêtre de détails")
    for dlg in ds:
        dlg.close()
    yield pump(0.3)
    check(not dialogs(win), "détails fermés")


@step
def menu_contextuel(win):
    from ithaca.ui.common import popup_menu
    row = win.rows[find(win, "petit.bin").id]
    pop = popup_menu(row, row.menu(), 20, 20)
    yield pump(0.3)
    check(pop.get_visible(), "menu contextuel ouvert")
    pop.popdown()
    yield pump(0.2)
    row = win.rows[find(win, "lent.bin").id]
    popup_menu(row, row.menu(), 20, 20).popdown()


@step
def preferences(win):
    app.activate_action("preferences", None)
    yield pump(0.8)
    ds = [w for w in dialogs(win) if isinstance(w, Adw.PreferencesWindow)]
    check(len(ds) == 1, "préférences ouvertes")
    for page in ("downloads", "connection", "features", "general"):
        ds[0].set_visible_page_name(page)
        yield pump(0.2)
    settings["speed_limit_enabled"] = True
    yield pump(0.3)
    check(win.limit_label.get_visible(), "« Vitesse limitée » affiché")
    settings["speed_limit_enabled"] = False
    yield pump(0.3)
    check(not win.limit_label.get_visible(), "limite retirée")
    ds[0].close()


@step
def presse_papiers(win):
    win.get_clipboard().set(fast.url("autre.bin") + "\n")
    yield pump(0.3)
    win._paste_into_entry()
    yield pump(0.5)
    check(win.url_entry.get_text() == fast.url("autre.bin"), "Coller met l'adresse dans le champ")
    win.url_entry.set_text("")


@step
def ouverture_dbus(win):
    n = len(win.manager.downloads)
    app.do_open([Gio.File.new_for_uri(fast.url("petit.bin"))], 1, "")
    yield pump(0.5)
    check(len(win.manager.downloads) == n + 1, "lien reçu par activation D-Bus (do_open)")


@step
def notifications(win):
    d = find(win, "petit.bin")
    features.set_enabled("notifications", True)
    app._on_download_event("completed", d)
    app._on_download_event("failed", find(win, "introuvable.bin"))
    features.set_enabled("notifications", False)
    app._on_download_event("completed", d)
    features.set_enabled("notifications", True)
    yield pump(0.2)
    check(True, "notifications envoyées sans erreur")


@step
def corbeille(win):
    win.add_urls([fast.url("à la corbeille.txt")])
    yield Wait(10, until=lambda: find(win, "à la corbeille.txt") and find(win, "à la corbeille.txt").state == "complete")
    d = find(win, "à la corbeille.txt")
    path = d.path
    win.activate_action("win.dl-trash", GLib.Variant.new_string(d.id))
    yield Wait(5, until=lambda: d not in win.manager.downloads)
    check(not os.path.exists(path), "fichier mis à la corbeille")
    check(d.id not in win.rows, "carte retirée")


@step
def retirer(win):
    d = find(win, "autre.bin")
    path = d.path
    win.activate_action("win.dl-remove", GLib.Variant.new_string(d.id))
    yield pump(0.5)
    check(d.id not in win.rows and os.path.exists(path), "terminé retiré de la liste, fichier gardé")


@step
def fermeture_demande(win):
    d = find(win, "lent.bin")
    check(d.state in ("active", "waiting"), "un téléchargement tourne encore")
    win.close()
    yield pump(0.6)
    ds = dialogs(win)
    check(len(ds) == 1 and isinstance(ds[0], Adw.MessageDialog), "fermer pendant un téléchargement : question")
    if ds:
        ds[0].response("cancel")
        ds[0].close()
    yield pump(0.3)
    check(win.get_visible(), "Annuler : la fenêtre reste")


@step
def annuler_en_cours(win):
    d = find(win, "lent.bin")
    path = d.path
    win.activate_action("win.dl-remove", GLib.Variant.new_string(d.id))
    yield Wait(5, until=lambda: not os.path.exists(path))
    check(not os.path.exists(path) and not os.path.exists(path + ".aria2"), "annulé : fichier partiel supprimé")


@step
def etat_enregistre(win):
    win.manager.save()
    import json
    with open(win.manager.state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    names = {d["name"] for d in data["downloads"]}
    check("petit.bin" in names and "lent.bin" not in names, "état enregistré (liste à jour)")


def on_window(_a, w):
    if not log:
        log.append("start")
        GLib.timeout_add(500, run_all, w)


app.connect("window-added", on_window)
app.run([sys.argv[0]])
fast.close()
slow.close()
shutil.rmtree(TMP, ignore_errors=True)
sys.stderr.flush()
time.sleep(0.2)                 # le fil de relecture finit de lire la sortie d'erreur
for line in CRITICALS:
    errors.append("ÉCHEC : avertissement critique de GTK / GLib : " + line)
print("\n".join(errors) if errors else "\nAucune erreur.")
sys.exit(1 if errors else 0)
