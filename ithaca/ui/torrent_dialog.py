"""Fenêtre de choix d'un torrent : dossier de destination, nom du dossier (plusieurs fichiers) ou du
fichier (un seul), fichiers à télécharger et leurs noms."""

from __future__ import annotations

import os
import threading
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..backend import torrents  # noqa: E402
from ..backend.downloads import free_name  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import download_dir  # noqa: E402
from .common import DialogWindow, alert, choose_folder, shown, toast  # noqa: E402

BAD_NAMES = ("", ".", "..")


def valid_name(name: str) -> bool:
    return name.strip() not in BAD_NAMES and "/" not in name and "\0" not in name


class TorrentDialog(DialogWindow):
    """source : lien magnet, chemin d'un .torrent, ou adresse http(s) d'un .torrent."""

    def __init__(self, manager, source: str, data: bytes = b""):
        super().__init__(title=tr("Torrent"), content_width=560, content_height=680)
        self.manager = manager
        self.source = source
        self.pending = None
        self.started = False
        self.closed = False
        self.folder = download_dir()
        self.rows: list = []            # (TorrentFile, check, entry)

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=tr("Annuler"))
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.go = Gtk.Button(label=tr("Télécharger"), css_classes=["suggested-action"], sensitive=False)
        self.go.connect("clicked", lambda *_: self._download())
        header.pack_end(self.go)
        tv.add_top_bar(header)
        self.toast_overlay = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                          margin_start=24, margin_end=24)
        loading.append(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        loading.append(Gtk.Label(label=tr("Recherche des informations du torrent…"), css_classes=["title-4"],
                                 wrap=True, justify=Gtk.Justification.CENTER))
        loading.append(Gtk.Label(label=tr("Il faut trouver un premier pair : cela peut prendre un moment."),
                                 css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER))
        self.stack.add_named(loading, "loading")
        self.error_page = Adw.StatusPage(icon_name="dialog-error-symbolic", title=tr("Torrent illisible"))
        self.stack.add_named(self.error_page, "error")
        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "options")
        self.toast_overlay.set_child(self.stack)
        tv.set_content(self.toast_overlay)
        self.set_child(tv)
        self.set_default_widget(self.go)
        self.connect("closed", self._on_closed)

        if torrents.is_magnet(source):
            try:
                self.pending = manager.torrents.prepare_magnet(source, self._ready_main, self._error_main)
            except torrents.TorrentError as e:
                msg = tr("Ce torrent est déjà dans la liste") if str(e) == "duplicate" else str(e)
                GLib.idle_add(self._show_error, msg)
        elif data:
            self._load_bytes(data)
        elif source.startswith(("http://", "https://")):
            threading.Thread(target=self._fetch, daemon=True).start()
        else:
            try:
                with open(source, "rb") as fh:
                    self._load_bytes(fh.read(20_000_000))
            except OSError as e:
                GLib.idle_add(self._show_error, e.strerror or str(e))

    # -- chargement -----------------------------------------------------------------------
    def _fetch(self):
        try:
            req = urllib.request.Request(self.source, headers={"User-Agent": "Ithaca"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(20_000_000)
        except (OSError, ValueError) as e:
            GLib.idle_add(self._show_error, str(e))
            return
        GLib.idle_add(lambda: (self._load_bytes(data), False)[1])

    def _load_bytes(self, data: bytes):
        try:
            self.pending = self.manager.torrents.prepare_file(data, self.source)
        except torrents.TorrentError as e:
            GLib.idle_add(self._show_error, str(e))
            return
        GLib.idle_add(lambda: (self._ready_main(self.pending), False)[1])

    def _ready_main(self, pending):
        if self.closed:
            return
        dup = self.manager.torrents.duplicate_of(pending)
        if dup is not None:
            self._show_error(tr("Ce torrent est déjà dans la liste : {0}").format(dup.display_name))
            return
        self._build(pending)
        self.stack.set_visible_child_name("options")
        self.go.set_sensitive(True)

    def _error_main(self, message: str):
        self._show_error(message)

    def _show_error(self, message: str):
        if not self.closed:
            self.error_page.set_description(GLib.markup_escape_text(shown(message)))
            self.stack.set_visible_child_name("error")
        return False

    def _on_closed(self, *_):
        self.closed = True
        if self.pending is not None and not self.started:
            self.manager.torrents.discard(self.pending)

    # -- options --------------------------------------------------------------------------
    def _build(self, p):
        head = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(Gtk.Label(label=shown(p.name), wrap=True, xalign=0, css_classes=["title-3"],
                             wrap_mode=Pango.WrapMode.WORD_CHAR))
        n = len(p.files)
        self.summary = Gtk.Label(xalign=0, css_classes=["dim-label", "numeric"])
        box.append(self.summary)
        head.add(box)
        self.page.add(head)

        where = Adw.PreferencesGroup(title=tr("Enregistrer dans"))
        self.folder_row = Adw.ActionRow(title=shown(self.folder))
        browse = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Choisir le dossier"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        browse.connect("clicked", self._choose_folder)
        self.folder_row.add_suffix(browse)
        self.folder_row.set_activatable_widget(browse)
        where.add(self.folder_row)
        first = p.files[0].path if p.files else p.name
        top = first.split("/")[0] if p.multi else first
        self.name_row = Adw.EntryRow(title=tr("Nom du dossier") if p.multi else tr("Nom du fichier"),
                                     text=shown(free_name(self.folder, top)))
        self.name_row.connect("changed", lambda *_: self._sync())
        where.add(self.name_row)
        self.page.add(where)

        if p.multi:
            files = Adw.PreferencesGroup(title=ntr("{0} fichier", "{0} fichiers", n).format(n))
            toggle = Gtk.Box(spacing=6)
            for label, value in ((tr("Tout"), True), (tr("Aucun"), False)):
                b = Gtk.Button(label=label, css_classes=["flat"], valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda _b, v=value: self._check_all(v))
                toggle.append(b)
            files.set_header_suffix(toggle)
            for f in p.files:
                parts = f.path.split("/")
                row = Adw.EntryRow(title=shown("/".join(parts[1:-1]) or tr("Fichier")), text=shown(parts[-1]))
                check = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
                check.connect("toggled", lambda *_: self._sync())
                row.add_prefix(check)
                row.add_suffix(Gtk.Label(label=fmt.size(f.size), css_classes=["dim-label", "numeric"]))
                row.connect("changed", lambda *_: self._sync())
                files.add(row)
                self.rows.append((f, check, row))
            self.page.add(files)
        self._sync()

    def _check_all(self, value: bool):
        for _f, check, _row in self.rows:
            check.set_active(value)

    def _choose_folder(self, button):
        def done(path):
            self.folder = path
            self.folder_row.set_title(shown(path))
            # le nom proposé reste libre dans le nouveau dossier
            self.name_row.set_text(free_name(path, self.name_row.get_text().strip() or self.pending.name))
        choose_folder(button, tr("Enregistrer dans"), self.folder, done)

    def _wanted(self) -> list:
        if not self.rows:
            return list(self.pending.files)
        return [f for f, check, _row in self.rows if check.get_active()]

    def _sync(self):
        wanted = self._wanted()
        size = sum(f.size for f in wanted)
        total = len(self.pending.files)
        self.summary.set_label(tr("{0} sur {1} — {2} fichiers sur {3}").format(
            fmt.size(size), fmt.size(self.pending.total), len(wanted), total) if total > 1 else fmt.size(size))
        names_ok = all(valid_name(row.get_text()) for _f, _c, row in self.rows)
        self.go.set_sensitive(bool(wanted) and valid_name(self.name_row.get_text()) and names_ok)

    # -- lancement --------------------------------------------------------------------------
    def _download(self):
        p = self.pending
        if p is None or p.ti is None:
            return
        name = self.name_row.get_text().strip()
        free = free_name(self.folder, name)
        if free != name:
            # pris entre-temps : jamais d'écrasement ni de reprise d'un fichier de l'utilisateur
            toast(self.get_transient_for(), tr("« {0} » existe déjà : enregistré sous « {1} »").format(name, free))
            name = free
        wanted = {f.index for f in self._wanted()}
        names = {}
        if p.multi:
            for f, _check, row in self.rows:
                new = row.get_text().strip()
                if new != f.path.split("/")[-1]:
                    names[f.index] = new
            root = name
        else:
            names[p.files[0].index] = name
            root = ""
        if not os.path.isdir(self.folder):
            alert(self, tr("Dossier introuvable"), self.folder)
            return
        self.started = True
        self.manager.add_torrent(p, self.folder, wanted, root, names)
        self.close()
