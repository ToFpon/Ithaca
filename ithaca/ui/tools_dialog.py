"""Installation et mise à jour de yt-dlp et Deno (programmes gérés par Ithaca), avec progression."""

from __future__ import annotations

import threading
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..backend import tools  # noqa: E402
from ..i18n import tr  # noqa: E402
from .common import AlertDialog, DialogWindow, alert  # noqa: E402


def ensure_tools(parent: Gtk.Widget, callback: Callable[[], None]):
    """yt-dlp (et Deno) présents : callback() tout de suite ; sinon on propose de les installer."""
    missing = tools.missing()
    if not missing:
        callback()
        return
    size = sum(tools.TOOLS[t].size_hint for t in missing)
    names = " + ".join(tools.TOOLS[t].name for t in missing)
    d = AlertDialog.new(tr("Installer les composants vidéo ?"),
                        tr("Ithaca a besoin de {0} (environ {1}, depuis GitHub). Ils sont installés dans "
                           "votre dossier personnel et tenus à jour automatiquement.").format(names, fmt.size(size)))
    d.add_response("cancel", tr("Annuler"))
    d.add_response("install", tr("Installer"))
    d.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response("install")
    d.set_close_response("cancel")
    d.connect("response", lambda _d, r: r == "install" and run_install(parent, missing, callback))
    d.present(parent)


def run_install(parent: Gtk.Widget, ids: list[str], callback: Optional[Callable[[], None]] = None):
    InstallWindow(ids, callback).present(parent)


class InstallWindow(DialogWindow):
    """Téléchargement vérifié de chaque programme, dans un fil ; annulable."""

    def __init__(self, ids: list[str], callback: Optional[Callable[[], None]]):
        super().__init__(title=tr("Composants vidéo"), content_width=460, content_height=-1)
        self.ids = ids
        self.callback = callback
        self.cancel_event = threading.Event()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        self.label = Gtk.Label(xalign=0, wrap=True)
        self.bar = Gtk.ProgressBar(show_text=True)
        box.append(self.label)
        box.append(self.bar)
        cancel = Gtk.Button(label=tr("Annuler"), halign=Gtk.Align.END)
        cancel.connect("clicked", lambda *_: self.close())
        box.append(cancel)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        tv.set_content(box)
        self.set_child(tv)
        self.connect("closed", lambda *_: self.cancel_event.set())
        self.done = False
        threading.Thread(target=self._work, daemon=True).start()

    def _set(self, text: str, fraction: float, detail: str = ""):
        self.label.set_label(text)
        self.bar.set_fraction(fraction)
        self.bar.set_text(detail)
        return False

    def _work(self):
        for tid in self.ids:
            name = tools.TOOLS[tid].name
            GLib.idle_add(self._set, tr("Téléchargement de {0}…").format(name), 0.0, "")

            def progress(got, total, name=name):
                frac = got / total if total else 0.0
                GLib.idle_add(self._set, tr("Téléchargement de {0}…").format(name), frac,
                              tr("{0} sur {1}").format(fmt.size(got), fmt.size(total)) if total else fmt.size(got))
            try:
                tools.install(tid, progress, self.cancel_event)
            except tools.ToolError as e:
                if not self.cancel_event.is_set():
                    GLib.idle_add(self._failed, name, str(e))
                return
        GLib.idle_add(self._success)

    def _failed(self, name: str, message: str):
        parent = self.get_transient_for()
        self.close()
        alert(parent, tr("Impossible d'installer {0}").format(name), message)
        return False

    def _success(self):
        self.done = True
        parent = self.get_transient_for()
        self.close()
        if self.callback:
            self.callback()
        elif parent is not None:
            from .common import toast
            toast(parent, tr("Composants vidéo à jour"))
        return False


def check_updates_async(on_done: Callable[[list[str]], None]):
    """Liste des programmes installés dont une nouvelle version est publiée (dans un fil)."""
    def work():
        out = []
        for tid in tools.TOOLS:
            if tools.installed(tid):
                try:
                    if tools.update_available(tid):
                        out.append(tid)
                except tools.ToolError:
                    pass
        GLib.idle_add(lambda: (on_done(out), False)[1])
    threading.Thread(target=work, daemon=True).start()


def update_silently(ids: list[str], on_done: Optional[Callable[[], None]] = None):
    """Mise à jour en arrière-plan (yt-dlp casse vite face à YouTube s'il n'est pas à jour)."""
    def work():
        for tid in ids:
            try:
                tools.install(tid)
            except tools.ToolError:
                pass
        if on_done:
            GLib.idle_add(lambda: (on_done(), False)[1])
    threading.Thread(target=work, daemon=True).start()
