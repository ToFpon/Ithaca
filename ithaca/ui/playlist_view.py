"""File de lecture (panneau latéral, comme Amberol) : recherche, mode sélection pour retirer des
morceaux, clic pour jouer, menu du clic droit. Gtk.ListView : seules les lignes visibles existent,
une file de plusieurs milliers de morceaux reste fluide."""

from __future__ import annotations

import unicodedata

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from .common import popup_menu, shown  # noqa: E402
from .cover import texture  # noqa: E402

THUMB = 40


def fold(text: str) -> str:
    """Comparaison sans casse ni accents (recherche) ; les écritures non latines restent intactes."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


class SongItem(GObject.Object):
    __gtype_name__ = "IthacaSongItem"

    def __init__(self, song):
        super().__init__()
        self.song = song
        self.search = fold(" ".join((song.display_title, song.artist, song.album)))
        self.selected = False


class PlaylistView(Gtk.Box):
    """controller : backend.controller.Controller."""

    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["playlist-pane"])
        self.c = controller
        self.selection_mode = False
        self._rows = []                   # lignes créées (pour rafraîchir l'indicateur de lecture)

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                           tooltip_text=tr("Rechercher dans la file"))
        header.pack_start(self.search_btn)
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title.append(Gtk.Label(label=tr("File de lecture"), css_classes=["heading"], ellipsize=Pango.EllipsizeMode.END))
        self.subtitle = Gtk.Label(css_classes=["caption", "dim-label", "numeric"], ellipsize=Pango.EllipsizeMode.END)
        title.append(self.subtitle)
        header.set_title_widget(title)
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=tr("Fermer la file"),
                                   visible=False)
        header.pack_end(self.back_btn)
        self.select_btn = Gtk.ToggleButton(icon_name="selection-mode-symbolic",
                                           tooltip_text=tr("Sélectionner des morceaux"))
        self.select_btn.connect("toggled", lambda b: self.set_selection_mode(b.get_active()))
        header.pack_end(self.select_btn)
        tv.add_top_bar(header)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text=tr("Rechercher un morceau"), hexpand=True)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        tv.add_top_bar(self.search_bar)

        self.store = Gio.ListStore.new(SongItem)
        self.filter = Gtk.CustomFilter.new(self._match)
        self.filtered = Gtk.FilterListModel.new(self.store, self.filter)
        self.selection = Gtk.NoSelection.new(self.filtered)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        factory.connect("unbind", self._unbind)
        self.view = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=True,
                                 css_classes=["navigation-sidebar", "playlist"])
        self.view.connect("activate", self._on_activate)
        self.stack = Gtk.Stack()
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.view)
        self.stack.add_named(scroller, "list")
        self.no_result = Adw.StatusPage(icon_name="edit-find-symbolic", title=tr("Aucun résultat"),
                                        css_classes=["compact"])
        self.stack.add_named(self.no_result, "empty")
        tv.set_content(self.stack)

        self.action_bar = Gtk.ActionBar(revealed=False)
        select_all = Gtk.Button(icon_name="edit-select-all-symbolic", tooltip_text=tr("Tout sélectionner"))
        select_all.connect("clicked", lambda *_: self.select_all())
        self.action_bar.pack_start(select_all)
        self.selected_label = Gtk.Label(css_classes=["caption"])
        self.action_bar.set_center_widget(self.selected_label)
        self.remove_btn = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text=tr("Retirer de la file"),
                                     css_classes=["destructive-action"])
        self.remove_btn.connect("clicked", lambda *_: self.remove_selected())
        self.action_bar.pack_end(self.remove_btn)
        tv.add_bottom_bar(self.action_bar)
        self.append(tv)
        self.vexpand = True
        self.set_vexpand(True)
        tv.set_vexpand(True)

        self._listener = self._on_event
        self.c.connect(self._listener)
        self.refresh()

    # -- modèle -----------------------------------------------------------------------------
    def refresh(self):
        items = [SongItem(s) for s in self.c.queue.items]
        self.store.splice(0, self.store.get_n_items(), items)
        n = len(items)
        self.subtitle.set_label(ntr("{0} morceau", "{0} morceaux", n).format(n) + " · " +
                                fmt.long_duration(self.c.queue.total_duration()) if n else "")
        self._sync_selected()
        self._sync_empty()

    def _match(self, item) -> bool:
        text = fold(self.search_entry.get_text().strip())
        return not text or all(word in item.search for word in text.split())

    def _on_search_changed(self, _entry):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sync_empty()

    def _sync_empty(self):
        searching = bool(self.search_entry.get_text().strip())
        self.stack.set_visible_child_name("empty" if searching and not self.filtered.get_n_items() else "list")

    def _on_event(self, event, *args):
        if event == "queue":
            self.refresh()
        elif event == "song":
            self._sync_playing()
            self.scroll_to_current()

    # -- lignes -------------------------------------------------------------------------------
    def _setup(self, _factory, li):
        box = Gtk.Box(spacing=12, css_classes=["song-row"])
        box._check = Gtk.CheckButton(valign=Gtk.Align.CENTER, visible=False, css_classes=["selection-mode"])
        box._check.connect("toggled", self._on_check)
        box._cover = Gtk.Stack(width_request=THUMB, height_request=THUMB, valign=Gtk.Align.CENTER)
        box._cover.add_named(Gtk.Image(icon_name="folder-music-symbolic", pixel_size=20,
                                       css_classes=["card", "dim-label", "small-cover"]), "none")
        box._picture = Gtk.Image(pixel_size=THUMB, css_classes=["small-cover"])
        box._picture.set_overflow(Gtk.Overflow.HIDDEN)
        box._cover.add_named(box._picture, "cover")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=2)
        box._title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["song-title"])
        box._artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["song-artist", "dim-label"])
        text.append(box._title)
        text.append(box._artist)
        box._playing = Gtk.Image(icon_name="audio-volume-high-symbolic", pixel_size=16, opacity=0,
                                 valign=Gtk.Align.CENTER, tooltip_text=tr("En cours de lecture"))
        for w in (box._check, box._cover, text, box._playing):
            box.append(w)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        box.add_controller(click)
        box._item = None
        li.set_child(box)
        self._rows.append(box)

    def _bind(self, _factory, li):
        box = li.get_child()
        item = li.get_item()
        box._item = item
        song = item.song
        box._title.set_label(shown(song.display_title))
        box._artist.set_label(shown(song.artist or tr("Artiste inconnu")))
        box.set_tooltip_text(shown(" — ".join(x for x in (song.display_title, song.artist, song.album) if x)))
        tex = texture(song.cover, THUMB * 2) if song.cover else None
        if tex is not None:
            box._picture.set_from_paintable(tex)
            box._cover.set_visible_child_name("cover")
        else:
            box._cover.set_visible_child_name("none")
        box._check.set_visible(self.selection_mode)
        box._check.set_active(item.selected)
        self._sync_row(box)

    def _unbind(self, _factory, li):
        li.get_child()._item = None

    def _sync_row(self, box):
        item = box._item
        playing = item is not None and item.song is self.c.song
        box._playing.set_opacity(1 if playing else 0)
        (box.add_css_class if playing else box.remove_css_class)("current")

    def _sync_playing(self):
        for box in self._rows:
            self._sync_row(box)

    def scroll_to_current(self):
        song = self.c.song
        if song is None or self.search_entry.get_text().strip():
            return
        for i in range(self.filtered.get_n_items()):
            if self.filtered.get_item(i).song is song:
                self.view.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
                break

    # -- actions ------------------------------------------------------------------------------
    def _on_activate(self, _view, position):
        item = self.filtered.get_item(position)
        if item is None:
            return
        if self.selection_mode:
            item.selected = not item.selected
            self._refresh_checks()
            return
        self.c.play_song(item.song)

    def _on_search_activate(self, _entry):
        if self.filtered.get_n_items():
            self.c.play_song(self.filtered.get_item(0).song)

    def _on_check(self, check):
        box = check.get_parent()
        if box is not None and box._item is not None and box._item.selected != check.get_active():
            box._item.selected = check.get_active()
            self._sync_selected()

    def _refresh_checks(self):
        for box in self._rows:
            if box._item is not None:
                box._check.set_active(box._item.selected)
        self._sync_selected()

    def selected_songs(self) -> list:
        return [self.store.get_item(i).song for i in range(self.store.get_n_items())
                if self.store.get_item(i).selected]

    def _sync_selected(self):
        n = len(self.selected_songs())
        self.selected_label.set_label(ntr("{0} morceau sélectionné", "{0} morceaux sélectionnés", n).format(n))
        self.remove_btn.set_sensitive(n > 0)

    def set_selection_mode(self, on: bool):
        self.selection_mode = on
        if self.select_btn.get_active() != on:
            self.select_btn.set_active(on)
        if not on:
            for i in range(self.store.get_n_items()):
                self.store.get_item(i).selected = False
        self.action_bar.set_revealed(on)
        for box in self._rows:
            box._check.set_visible(on)
        self._refresh_checks()

    def select_all(self):
        for i in range(self.filtered.get_n_items()):
            self.filtered.get_item(i).selected = True
        self._refresh_checks()

    def remove_selected(self):
        songs = self.selected_songs()
        if songs:
            self.c.remove(songs)
        self.set_selection_mode(False)

    def _on_right_click(self, gesture, n_press, x, y):
        box = gesture.get_widget()
        if box._item is None or self.selection_mode:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        index = self.c.queue.items.index(box._item.song) if box._item.song in self.c.queue.items else -1
        target = GLib.Variant.new_int32(index)
        menu = Gio.Menu()
        for label, action in ((tr("Lire"), "win.play-index"), (tr("Afficher dans le dossier"), "win.show-index"),
                              (tr("Retirer de la file"), "win.remove-index")):
            it = Gio.MenuItem.new(label, None)
            it.set_action_and_target_value(action, target)
            menu.append_item(it)
        popup_menu(box, menu, x, y)

    def close(self):
        self.c.disconnect(self._listener)
