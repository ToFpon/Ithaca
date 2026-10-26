"""Fenêtre principale : barre latérale (ajout, filtres) et liste des téléchargements, comme Varia."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_ID, format as fmt  # noqa: E402
from ..backend import aria2  # noqa: E402
from ..backend.downloads import Download, is_supported_url, matches  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import download_dir, settings  # noqa: E402
from .common import (alert, choose_folder, copy_to_clipboard, launch_default, open_folder,  # noqa: E402
                     show_in_folder, shown, toast)
from .row import DownloadRow  # noqa: E402

FILTERS = [("all", tr("Tous"), "view-list-symbolic"),
           ("progress", tr("En cours"), "content-loading-symbolic"),
           ("complete", tr("Terminés"), "object-select-symbolic"),
           ("failed", tr("Échecs"), "dialog-error-symbolic")]

TEXT_WIDGETS = (Gtk.Text, Gtk.TextView, Gtk.Entry)


def split_urls(text: str) -> list[str]:
    """Liens d'un texte collé ou déposé (un par ligne, ou séparés par des espaces)."""
    out = []
    for part in (text or "").replace("\r", "\n").split():
        part = part.strip().strip("<>\"'")
        if part and not part.startswith("#") and part not in out:
            out.append(part)
    return out


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ithaca", icon_name=APP_ID)
        self.manager = app.manager
        self.filter = "all"
        self.rows: dict[str, DownloadRow] = {}
        self.set_default_size(settings["window_width"], settings["window_height"])
        self.set_size_request(360, 300)

        self.toast_overlay = Adw.ToastOverlay()
        self.split = Adw.OverlaySplitView(min_sidebar_width=230, max_sidebar_width=280)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.toast_overlay.set_child(self.split)
        self.set_content(self.toast_overlay)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 620sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        self.split.connect("notify::collapsed", lambda *_: self._sync_sidebar_button())
        self.split.connect("notify::show-sidebar", lambda *_: self._sync_sidebar_button())
        self._sync_sidebar_button()

        self._install_actions()
        self._install_drop()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        for d in self.manager.downloads:
            self._add_row(d)
        self._listener = self._on_event
        self.manager.connect(self._listener)
        self._sync_engine()
        self._refresh()
        self.connect("close-request", self._on_close_request)

    # -- construction --------------------------------------------------------------
    def _build_sidebar(self) -> Gtk.Widget:
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(css_classes=["flat"])
        header.set_title_widget(Gtk.Box())
        prefs = Gtk.Button(icon_name="preferences-system-symbolic", tooltip_text=tr("Préférences"),
                           action_name="app.preferences")
        header.pack_start(prefs)
        menu = Gio.Menu()
        first = Gio.Menu()
        first.append(tr("Ouvrir le dossier des téléchargements"), "win.open-folder")
        first.append(tr("Tout mettre en pause"), "win.pause-all")
        first.append(tr("Tout reprendre"), "win.resume-all")
        menu.append_section(None, first)
        second = Gio.Menu()
        second.append(tr("Effacer les terminés de la liste"), "win.clear-finished")
        second.append(tr("Tout annuler"), "win.cancel-all")
        menu.append_section(None, second)
        third = Gio.Menu()
        third.append(tr("Préférences"), "app.preferences")
        third.append(tr("À propos d'Ithaca"), "app.about")
        third.append(tr("Quitter"), "app.quit")
        menu.append_section(None, third)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text=tr("Menu principal"), primary=True))
        tv.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        add = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["add-box"])
        self.url_entry = Gtk.Entry(placeholder_text=tr("Adresse (URL)"), input_purpose=Gtk.InputPurpose.URL,
                                   secondary_icon_name="edit-paste-symbolic",
                                   secondary_icon_tooltip_text=tr("Coller"))
        self.url_entry.connect("activate", lambda *_: self._download_clicked())
        self.url_entry.connect("changed", lambda *_: self._sync_add_buttons())
        self.url_entry.connect("icon-press", lambda *_: self._paste_into_entry())
        add.append(self.url_entry)
        self.download_btn = Gtk.Button(css_classes=["suggested-action"])
        self.download_btn.set_child(Adw.ButtonContent(icon_name="folder-download-symbolic",
                                                      label=tr("Télécharger"), can_shrink=True))
        self.download_btn.connect("clicked", lambda *_: self._download_clicked())
        add.append(self.download_btn)
        frame = Gtk.Frame(child=add, margin_start=8, margin_end=8, margin_top=4)
        box.append(frame)

        self.filter_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.filter_counts = {}
        for fid, label, icon in FILTERS:
            row = Gtk.ListBoxRow()
            row.filter_id = fid
            line = Gtk.Box(spacing=12, margin_start=6, margin_end=6)
            line.append(Gtk.Image(icon_name=icon))
            line.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            count = Gtk.Label(css_classes=["dim-label", "numeric", "filter-count"])
            line.append(count)
            self.filter_counts[fid] = count
            row.set_child(line)
            self.filter_list.append(row)
        self.filter_list.select_row(self.filter_list.get_row_at_index(0))
        self.filter_list.connect("row-selected", self._on_filter_selected)
        box.append(self.filter_list)
        box.append(Gtk.Box(vexpand=True))

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_bottom=10)
        self.limit_label = Gtk.Label(label=tr("Vitesse limitée"), css_classes=["dim-label", "caption"])
        self.status_box.append(self.limit_label)
        box.append(self.status_box)
        tv.set_content(box)
        settings.connect(self._on_setting)
        self._sync_status_labels()
        return tv

    def _build_content(self) -> Gtk.Widget:
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.sidebar_btn = Gtk.Button(icon_name="sidebar-show-symbolic", tooltip_text=tr("Afficher la barre latérale"))
        self.sidebar_btn.connect("clicked", lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        header.pack_start(self.sidebar_btn)
        self.speed_label = Gtk.Label(css_classes=["heading", "numeric"])
        header.set_title_widget(self.speed_label)
        self.pause_all_content = Adw.ButtonContent(icon_name="media-playback-pause-symbolic",
                                                   label=tr("Tout mettre en pause"))
        self.pause_all_btn = Gtk.Button(child=self.pause_all_content, css_classes=["flat"])
        self.pause_all_btn.connect("clicked", self._toggle_pause_all)
        header.pack_end(self.pause_all_btn)
        tv.add_top_bar(header)

        self.banner = Adw.Banner()
        tv.add_top_bar(self.banner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty_page = Adw.StatusPage(icon_name=APP_ID + "-symbolic", title=tr("Aucun téléchargement"),
                                         description=tr("Collez une adresse dans le champ de gauche, "
                                                        "ou glissez un lien dans la fenêtre."))
        self.stack.add_named(self.empty_page, "empty")
        self.nothing_page = Adw.StatusPage(icon_name="edit-find-symbolic", title=tr("Rien ici"))
        self.stack.add_named(self.nothing_page, "nothing")
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["download-list"],
                                valign=Gtk.Align.START)
        self.list.set_filter_func(lambda row: matches(row.download, self.filter))
        self.list.set_sort_func(lambda a, b: (b.download.added > a.download.added) - (b.download.added < a.download.added))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list, vexpand=True)
        self.stack.add_named(scroller, "list")
        tv.set_content(self.stack)
        return tv

    # -- actions -------------------------------------------------------------------
    def _install_actions(self):
        simple = {
            "open-folder": lambda: open_folder(self, download_dir()),
            "pause-all": self.manager.pause_all,
            "resume-all": self.manager.resume_all,
            "clear-finished": self.manager.clear_finished,
            "cancel-all": self._cancel_all,
            "focus-url": self.focus_url,
        }
        for name, cb in simple.items():
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
        per_download = {
            "dl-open": self._open, "dl-show": self._show, "dl-pause": self.manager.pause,
            "dl-resume": self.manager.resume, "dl-retry": self.manager.retry, "dl-remove": self.manager.remove,
            "dl-trash": self._trash, "dl-copy-url": self._copy_url, "dl-details": self._details,
        }
        for name, cb in per_download.items():
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, p, cb=cb: self._with_download(p.get_string(), cb))
            self.add_action(a)

    def _with_download(self, did: str, cb):
        for d in self.manager.downloads:
            if d.id == did:
                cb(d)
                return

    def _open(self, d: Download):
        if not launch_default(self, Gio.File.new_for_path(d.path)):
            from .common import open_with_default
            open_with_default(self, d.path)

    def _show(self, d: Download):
        show_in_folder(self, d.path)

    def _copy_url(self, d: Download):
        copy_to_clipboard(self, d.url)
        toast(self, tr("Adresse copiée"))

    def _details(self, d: Download):
        from .details import DetailsWindow
        DetailsWindow(self.manager, d).present(self)

    def _trash(self, d: Download):
        f = Gio.File.new_for_path(d.path)

        def done(src, res):
            try:
                src.trash_finish(res)
            except GLib.Error as e:
                alert(self, tr("Impossible de mettre le fichier à la corbeille"), e.message)
                return
            self.manager.remove(d)
            toast(self, tr("« {0} » a été mis à la corbeille").format(shown(d.display_name)))
        f.trash_async(GLib.PRIORITY_DEFAULT, None, done)

    def _cancel_all(self):
        n = sum(1 for d in self.manager.downloads if matches(d, "progress"))
        if not n:
            return
        alert(self, tr("Annuler tous les téléchargements ?"),
              ntr("{0} téléchargement en cours sera arrêté et son fichier partiel supprimé.",
                  "{0} téléchargements en cours seront arrêtés et leurs fichiers partiels supprimés.", n).format(n),
              responses=(("cancel", tr("Annuler")), ("stop", tr("Tout annuler"))), default="cancel",
              destructive="stop", callback=lambda r: r == "stop" and self.manager.cancel_all())

    def _toggle_pause_all(self, *_):
        if self.manager.any_running():
            self.manager.pause_all()
        else:
            self.manager.resume_all()

    # -- ajout ---------------------------------------------------------------------
    def focus_url(self):
        self.split.set_show_sidebar(True)
        self.url_entry.grab_focus()

    def _sync_add_buttons(self):
        self.download_btn.set_sensitive(bool(self.url_entry.get_text().strip()))

    def _paste_into_entry(self):
        def done(clip, res):
            try:
                text = clip.read_text_finish(res)
            except GLib.Error:
                return
            if text:
                self.url_entry.set_text(" ".join(split_urls(text)))
                self.url_entry.set_position(-1)
                self.url_entry.grab_focus()
        self.get_clipboard().read_text_async(None, done)

    def _download_clicked(self):
        urls = split_urls(self.url_entry.get_text())
        if not urls:
            return
        if self.add_urls(urls):
            self.url_entry.set_text("")

    def add_urls(self, urls: list[str], folder: str = "") -> bool:
        """Ajoute des liens ; False (et un message) si aucun n'est utilisable."""
        good = [u for u in urls if is_supported_url(u)]
        bad = [u for u in urls if u not in good]
        if bad:
            toast(self, ntr("Adresse non prise en charge : {0}", "Adresses non prises en charge : {0}",
                            len(bad)).format(shown(", ".join(bad))))
        if not good:
            return False
        if settings["ask_dir"] and not folder:
            choose_folder(self, tr("Enregistrer dans"), download_dir(),
                          lambda path: [self.manager.add(u, path) for u in good])
        else:
            for u in good:
                self.manager.add(u, folder)
        return True

    def _install_drop(self):
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        target.connect("drop", lambda _t, value, _x, _y: self.add_urls(split_urls(value)))
        self.toast_overlay.add_controller(target)

    def _on_key(self, _ctrl, keyval, _code, state):
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and mods == Gdk.ModifierType.CONTROL_MASK:
            focus = self.get_focus()
            if not isinstance(focus, TEXT_WIDGETS):
                # Ctrl+V hors d'un champ : le lien collé va dans le champ d'adresse
                self.split.set_show_sidebar(True)
                self._paste_into_entry()
                return True
        return False

    # -- état ----------------------------------------------------------------------
    def _add_row(self, d: Download):
        row = DownloadRow(d)
        self.rows[d.id] = row
        self.list.append(row)

    def _on_event(self, event: str, d):
        if event == "added":
            self._add_row(d)
        elif event == "removed":
            row = self.rows.pop(d.id, None)
            if row is not None:
                self.list.remove(row)
        elif event == "changed":
            row = self.rows.get(d.id)
            if row is not None:
                old = getattr(row, "_state", None)
                row.update()
                if old != d.state:
                    row._state = d.state
                    self.list.invalidate_filter()
            self._refresh_counts()
            return
        elif event == "engine":
            self._sync_engine()
        elif event == "stats":
            self._refresh_header()
            return
        elif event in ("completed", "failed"):
            return
        self.list.invalidate_filter()
        self._refresh()

    def _refresh(self):
        self._refresh_counts()
        self._refresh_header()

    def _refresh_counts(self):
        for fid, label in self.filter_counts.items():
            n = self.manager.count(fid)
            label.set_label(str(n) if n else "")
        if not self.manager.downloads:
            self.stack.set_visible_child_name("empty")
        elif not self.manager.count(self.filter):
            self.nothing_page.set_title({"progress": tr("Aucun téléchargement en cours"),
                                         "complete": tr("Aucun téléchargement terminé"),
                                         "failed": tr("Aucun échec")}.get(self.filter, tr("Rien ici")))
            self.stack.set_visible_child_name("nothing")
        else:
            self.stack.set_visible_child_name("list")

    def _refresh_header(self):
        running = self.manager.any_running()
        if running:
            self.speed_label.set_label("↓ " + fmt.speed(self.manager.total_speed))
        else:
            self.speed_label.set_label("Ithaca")
        resumable = any(d.state == "paused" for d in self.manager.downloads)
        self.pause_all_btn.set_visible(running or resumable)
        if running:
            self.pause_all_content.set_icon_name("media-playback-pause-symbolic")
            self.pause_all_content.set_label(tr("Tout mettre en pause"))
        else:
            self.pause_all_content.set_icon_name("media-playback-start-symbolic")
            self.pause_all_content.set_label(tr("Tout reprendre"))

    def _sync_engine(self):
        err = self.manager.engine_error
        if err:
            if not aria2.find_aria2c():
                self.banner.set_title(tr("aria2 est introuvable : sudo apt install aria2"))
            else:
                self.banner.set_title(tr("aria2 n'a pas démarré : {0}").format(err))
        self.banner.set_revealed(bool(err))
        self.download_btn.set_sensitive(bool(self.url_entry.get_text().strip()))

    def _on_filter_selected(self, _box, row):
        if row is None:
            return
        self.filter = row.filter_id
        self.list.invalidate_filter()
        self._refresh_counts()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def _sync_sidebar_button(self):
        self.sidebar_btn.set_visible(self.split.get_collapsed() or not self.split.get_show_sidebar())

    def _on_setting(self, key, _value):
        if key in ("speed_limit_enabled", "speed_limit"):
            self._sync_status_labels()

    def _sync_status_labels(self):
        on = settings["speed_limit_enabled"]
        self.limit_label.set_visible(on)
        if on:
            self.limit_label.set_label(tr("Vitesse limitée à {0}").format(fmt.speed(settings["speed_limit"] * 1000)))

    # -- fermeture -----------------------------------------------------------------
    def _on_close_request(self, *_):
        if getattr(self, "_closing", False) or not self.manager.any_running():
            return self._finish_close()
        n = sum(1 for d in self.manager.downloads if d.state in ("waiting", "active"))
        alert(self, tr("Quitter Ithaca ?"),
              ntr("{0} téléchargement est en cours. Il reprendra au prochain lancement d'Ithaca.",
                  "{0} téléchargements sont en cours. Ils reprendront au prochain lancement d'Ithaca.", n).format(n),
              responses=(("cancel", tr("Annuler")), ("quit", tr("Quitter"))), default="quit",
              suggested="quit", callback=self._close_answer)
        return True

    def _close_answer(self, response: str):
        if response == "quit":
            self._closing = True
            self.close()

    def _finish_close(self):
        w, h = self.get_default_size()
        if not self.is_maximized() and w > 0 and h > 0:
            settings["window_width"], settings["window_height"] = w, h
        self.manager.disconnect(self._listener)
        settings.disconnect(self._on_setting)
        return False
