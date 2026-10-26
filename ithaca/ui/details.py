"""Détails d'un téléchargement (vraie fenêtre, mise à jour en direct)."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..backend.downloads import Download  # noqa: E402
from ..i18n import tr  # noqa: E402
from .common import DialogWindow, copy_to_clipboard, full_date, open_folder, shown, toast  # noqa: E402
from .row import status_text  # noqa: E402

STATE_LABELS = {"waiting": tr("En attente"), "active": tr("En cours"), "paused": tr("En pause"),
                "complete": tr("Terminé"), "error": tr("Échec")}


class DetailsWindow(DialogWindow):
    def __init__(self, manager, d: Download):
        super().__init__(title=tr("Détails du téléchargement"), content_width=560, content_height=620)
        self.manager = manager
        self.download = d
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.toast_overlay = Adw.ToastOverlay()
        page = Adw.PreferencesPage()
        self.toast_overlay.set_child(page)
        tv.set_content(self.toast_overlay)
        self.set_child(tv)

        files = Adw.PreferencesGroup(title=tr("Fichier"))
        self.name = self._row(files, tr("Nom"))
        self.folder = self._row(files, tr("Dossier"))
        open_btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Ouvrir le dossier"),
                              valign=Gtk.Align.CENTER, css_classes=["flat"])
        open_btn.connect("clicked", lambda b: open_folder(b, self.download.dir))
        self.folder.add_suffix(open_btn)
        self.url = self._row(files, tr("Adresse"))
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=tr("Copier l'adresse"),
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        copy.connect("clicked", self._copy)
        self.url.add_suffix(copy)
        page.add(files)

        progress = Adw.PreferencesGroup(title=tr("Progression"))
        self.state = self._row(progress, tr("État"))
        self.size = self._row(progress, tr("Taille"))
        self.done = self._row(progress, tr("Téléchargé"))
        self.speed = self._row(progress, tr("Vitesse"))
        self.eta = self._row(progress, tr("Temps restant"))
        self.connections = self._row(progress, tr("Connexions"))
        page.add(progress)

        dates = Adw.PreferencesGroup(title=tr("Dates"))
        self.added = self._row(dates, tr("Ajouté le"))
        self.finished = self._row(dates, tr("Terminé le"))
        page.add(dates)

        self._listener = self._on_event
        manager.connect(self._listener)
        self.connect("closed", lambda *_: manager.disconnect(self._listener))
        self.update()

    def _row(self, group, title) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle_selectable=True, css_classes=["property"])
        group.add(row)
        return row

    def _copy(self, button):
        copy_to_clipboard(button, self.download.url)
        toast(self.toast_overlay, tr("Adresse copiée"))

    def _on_event(self, event, d):
        if d is self.download and event == "removed":
            self.close()
        elif d is self.download or event == "stats":
            self.update()

    def update(self):
        d = self.download
        self.name.set_subtitle(shown(d.display_name))
        self.folder.set_subtitle(shown(d.dir))
        self.url.set_subtitle(shown(d.url))
        self.state.set_subtitle(status_text(d) if d.state == "error" else STATE_LABELS.get(d.state, d.state))
        self.size.set_subtitle(fmt.size(d.total) if d.total else tr("Inconnue"))
        self.done.set_subtitle(fmt.size(d.done))
        active = d.state == "active"
        self.speed.set_subtitle(fmt.speed(d.speed))
        self.speed.set_visible(active)
        self.eta.set_subtitle(fmt.duration(d.eta) if d.eta >= 0 else tr("Inconnu"))
        self.eta.set_visible(active)
        self.connections.set_subtitle(str(d.connections))
        self.connections.set_visible(active)
        self.added.set_subtitle(full_date(d.added))
        self.finished.set_subtitle(full_date(d.finished))
        self.finished.set_visible(bool(d.finished))
        self.folder.set_visible(os.path.isdir(d.dir) or bool(d.dir))
