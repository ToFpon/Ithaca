"""Fenêtre principale, disposition d'Amberol : page d'accueil (ajouter des morceaux, restaurer la
file), puis pochette, forme d'onde, titre / artiste / album, commandes, et la file en panneau latéral
(replié en dessous de 560 sp, comme Amberol)."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .. import APP_ID, features, format as fmt  # noqa: E402
from ..backend.playqueue import load_saved  # noqa: E402
from ..backend.waveform import WaveformGenerator  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import choose_files, choose_folder, copy_to_clipboard, show_in_folder, shown, toast  # noqa: E402
from .cover import CoverView, Recolor  # noqa: E402
from .marquee import Marquee  # noqa: E402
from .playlist_view import PlaylistView  # noqa: E402
from .waveform_view import WaveformView  # noqa: E402

TEXT_WIDGETS = (Gtk.Text, Gtk.TextView, Gtk.Entry, Gtk.SearchEntry)
MIN_WIDTH = 360                       # largeur minimale de la fenêtre
SIDEBAR_MAX, SIDEBAR_FRACTION = 360, 0.4
COLLAPSE_SP = 560                     # en dessous, la file passe par-dessus le lecteur (Adw.Breakpoint)
COMPACT_WIDTH = 370                   # file masquée : le lecteur seul, comme la capture « compact » du site


def width_with_playlist(content: int) -> int:
    """Largeur de fenêtre qui garde au lecteur la largeur `content` une fois la file affichée
    (file = 40 % de la fenêtre, 360 px au plus), sans retomber sous le point de repli."""
    if content + SIDEBAR_MAX >= SIDEBAR_MAX / SIDEBAR_FRACTION:
        width = content + SIDEBAR_MAX
    else:
        width = round(content / (1 - SIDEBAR_FRACTION))
    dpi = Gtk.Settings.get_default().get_property("gtk-xft-dpi") if Gtk.Settings.get_default() else -1
    scale = dpi / (96 * 1024) if dpi and dpi > 0 else 1.0     # sp = px × mise à l'échelle du texte
    return max(width, round(COLLAPSE_SP * scale) + 40)


def artist_line(song) -> str:
    """Seconde ligne : l'artiste ; pour une webradio, la station (ou « Webradio »)."""
    if song.stream:
        return song.title if song.now_playing and song.title else tr("Webradio")
    return song.artist or tr("Artiste inconnu")
REPEAT_ICONS = {"consecutive": "media-playlist-consecutive-symbolic", "all": "media-playlist-repeat-symbolic",
                "one": "media-playlist-repeat-song-symbolic"}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ithaca", icon_name=APP_ID)
        # add_css_class, pas css_classes= : la liste remplaçait les classes du thème (« background »…)
        # et la fenêtre perdait ses couleurs (texte blanc sur fond blanc)
        self.add_css_class("ithaca-window")
        self.c = app.controller
        self.set_default_size(settings["window_width"], settings["window_height"])
        self.set_size_request(MIN_WIDTH, 540)
        self.waveforms = WaveformGenerator()
        self.recolor = Recolor()
        self._dragging_volume = False

        self.toast_overlay = Adw.ToastOverlay()
        overlay = Gtk.Overlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_empty(), "empty")
        self.stack.add_named(self._build_main(), "main")
        overlay.set_child(self.stack)
        self.drop_hint = Adw.StatusPage(icon_name="folder-music-symbolic", title=tr("Déposez pour ajouter à la file"),
                                        css_classes=["drop-hint"], visible=False, can_target=False)
        overlay.add_overlay(self.drop_hint)
        self.toast_overlay.set_child(overlay)
        # Pas de Gtk.WindowHandle autour de toute la fenêtre : il s'emparait du glisser d'une ligne de
        # la file (déplacement de la fenêtre au lieu de réordonner). Il n'entoure que le lecteur et la
        # page d'accueil (_build_main / _build_empty) ; les barres de titre déplacent aussi la fenêtre.
        self.set_content(self.toast_overlay)

        # Fenêtre étroite : la file passe par-dessus le lecteur — seulement si elle est affichée. Une
        # file masquée ne « replie » pas la vue : sinon replier la fenêtre avec la file (F9) changeait de
        # mode en pleine animation (« Trying to snapshot AdwGizmo without a current allocation »).
        self._narrow = False
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {COLLAPSE_SP}sp"))
        bp.connect("apply", lambda *_: self._set_narrow(True))
        bp.connect("unapply", lambda *_: self._set_narrow(False))
        self.add_breakpoint(bp)
        self._frame = None                  # cadre de la fenêtre (CSD) : taille par défaut − largeur
        self._wide = None                   # (largeur avec la file, largeur compacte posée) pour revenir
        low = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-height: 680sp"))
        self.cover.add_size_setters(low, 180)
        self.add_breakpoint(low)
        self.split.connect("notify::collapsed", lambda *_: self._sync_playlist_button())
        self.split.connect("notify::show-sidebar", lambda *_: self._sync_playlist_button())
        self.split.set_show_sidebar(settings["playlist_visible"])

        self._install_actions()
        self._install_drop()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self._listener = self._on_event
        self.c.connect(self._listener)
        settings.connect(self._on_setting)
        self._sync_all()
        self.connect("close-request", self._on_close_request)

    # -- construction -----------------------------------------------------------------------
    def _build_empty(self) -> Gtk.Widget:
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        header.pack_end(self._menu_button(direction=Gtk.ArrowType.DOWN))
        tv.add_top_bar(header)
        page = Adw.StatusPage(icon_name=APP_ID + "-symbolic", title="Ithaca",
                              description=tr("Choisissez des morceaux ou un dossier, ou glissez-les depuis "
                                             "le gestionnaire de fichiers dans la fenêtre"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, homogeneous=True)
        self.restore_btn = Gtk.Button(label=tr("Restaurer la file"), css_classes=["suggested-action", "pill"],
                                      action_name="win.restore")
        box.append(self.restore_btn)
        box.append(Gtk.Button(label=tr("Ajouter un dossier"), css_classes=["pill"], action_name="win.add-folder"))
        box.append(Gtk.Button(label=tr("Ajouter des morceaux"), css_classes=["pill"], action_name="win.add-songs"))
        page.set_child(box)
        tv.set_content(Gtk.WindowHandle(child=Adw.Clamp(maximum_size=450, child=page, vexpand=True)))
        return tv

    def _build_main(self) -> Gtk.Widget:
        self.split = Adw.OverlaySplitView(max_sidebar_width=SIDEBAR_MAX, sidebar_width_fraction=SIDEBAR_FRACTION,
                                          sidebar_position=self._playlist_side())
        content = Adw.ToolbarView()
        self.content_header = header = Adw.HeaderBar(show_title=False)
        # comme le bouton de la barre latérale d'Odysseus ; F9 et le bouton du bas font la même chose.
        # Posé du côté de la file (_sync_side)
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text=tr("Afficher la file"))
        self.sidebar_btn.connect("toggled", self._on_playlist_toggled)
        content.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, vexpand=True,
                      css_classes=["main-box"])
        self.cover = CoverView(256)
        box.append(self.cover)

        wave_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.CENTER,
                           width_request=256)
        self.waveform = WaveformView()
        self.waveform.set_size_request(256, -1)
        self.waveform.connect("seek", lambda _w, seconds: self.c.seek(seconds))
        self.waveform.step = float(settings["seek_step"])
        wave_box.append(self.waveform)
        times = Gtk.CenterBox()
        self.elapsed = Gtk.Label(label="0:00", css_classes=["caption", "numeric", "dim-label"])
        self.remaining = Gtk.Label(label="0:00", css_classes=["caption", "numeric", "dim-label"])
        times.set_start_widget(self.elapsed)
        times.set_end_widget(self.remaining)
        wave_box.append(times)
        box.append(wave_box)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["song-details"])
        self.title_label = Marquee(css_classes=["song-title"])     # titre trop long : il défile
        self.title_label.set_margin_start(18)
        self.title_label.set_margin_end(18)
        self.artist_label = Gtk.Label(css_classes=["song-artist"], ellipsize=Pango.EllipsizeMode.END,
                                      max_width_chars=34)
        self.album_label = Gtk.Label(css_classes=["song-album", "dim-label"], ellipsize=Pango.EllipsizeMode.END,
                                     max_width_chars=34)
        for w in (self.title_label, self.artist_label, self.album_label):
            details.append(w)
        box.append(details)
        box.append(self._build_controls())
        content.set_content(Gtk.WindowHandle(child=box))       # tirer le lecteur déplace la fenêtre
        self.split.set_content(content)

        self.playlist = PlaylistView(self.c)
        self.playlist.back_btn.connect("clicked", lambda *_: self.set_playlist_shown(False))
        self.split.set_sidebar(self.playlist)
        self._sync_side()
        return self.split

    def _build_controls(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                        width_request=256, css_classes=["playback-control"])
        buttons = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER)
        self.prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic", css_classes=["circular"],
                                   valign=Gtk.Align.CENTER, tooltip_text=tr("Morceau précédent"),
                                   action_name="win.previous")
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", css_classes=["circular", "large"],
                                   valign=Gtk.Align.CENTER, tooltip_text=tr("Lecture / pause"),
                                   action_name="win.play")
        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic", css_classes=["circular"],
                                   valign=Gtk.Align.CENTER, tooltip_text=tr("Morceau suivant"),
                                   action_name="win.next")
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            buttons.append(b)
        outer.append(buttons)

        volume = Gtk.Box(spacing=0, css_classes=["volume"])
        self.mute_btn = Gtk.Button(icon_name="audio-volume-low-symbolic", css_classes=["flat", "circular"],
                                   valign=Gtk.Align.CENTER, tooltip_text=tr("Couper le son"))
        self.mute_btn.connect("clicked", lambda *_: self.c.set_muted(not settings["muted"]))
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.05)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        # molette : « Cran de molette » des Préférences (le pas de Gtk.Scale ne se règle pas ainsi)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                          | Gtk.EventControllerScrollFlags.DISCRETE,
                                          propagation_phase=Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_volume_wheel)
        volume.add_controller(wheel)
        volume.append(self.mute_btn)
        volume.append(self.volume_scale)
        volume.append(Gtk.Image(icon_name="audio-volume-high-symbolic", margin_end=10))
        outer.append(volume)

        bottom = Gtk.CenterBox()
        start = Gtk.Box(spacing=12, margin_end=18)
        self.playlist_btn = Gtk.ToggleButton(icon_name="view-list-symbolic", css_classes=["circular"],
                                             valign=Gtk.Align.CENTER, tooltip_text=tr("Afficher la file"))
        self.playlist_btn.connect("toggled", self._on_playlist_toggled)
        self.shuffle_btn = Gtk.ToggleButton(icon_name="media-playlist-shuffle-symbolic", css_classes=["circular"],
                                            valign=Gtk.Align.CENTER, tooltip_text=tr("Lecture aléatoire"))
        self.shuffle_btn.connect("toggled", lambda b: self.c.set_shuffled(b.get_active()))
        start.append(self.playlist_btn)
        start.append(self.shuffle_btn)
        end = Gtk.Box(spacing=12, margin_start=18)
        self.repeat_btn = Gtk.Button(css_classes=["circular"], valign=Gtk.Align.CENTER)
        self.repeat_btn.connect("clicked", lambda *_: self.c.cycle_repeat())
        end.append(self.repeat_btn)
        end.append(self._menu_button(direction=Gtk.ArrowType.UP))
        bottom.set_start_widget(start)
        bottom.set_end_widget(end)
        outer.append(bottom)
        return outer

    def _menu_button(self, direction) -> Gtk.MenuButton:
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append(tr("Copier le titre"), "win.copy")
        s1.append(tr("Ouvrir le dossier"), "win.open-folder")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append(tr("Ajouter des morceaux"), "win.add-songs")
        s2.append(tr("Ajouter un dossier"), "win.add-folder")
        s2.append(tr("Enregistrer la file…"), "win.save-playlist")
        s2.append(tr("Ajouter la file à une liste…"), "win.append-playlist")
        s2.append(tr("Vider la file"), "win.clear")
        menu.append_section(None, s2)
        s3 = Gio.Menu()
        s3.append(tr("Couleurs de la pochette"), "win.recolor")
        s3.append(tr("Lecture en arrière-plan"), "win.background")
        rg = Gio.Menu()
        for label, mode in ((tr("Album"), "album"), (tr("Morceau"), "track"), (tr("Désactivé"), "off")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.replaygain", GLib.Variant.new_string(mode))
            rg.append_item(item)
        s3.append_submenu(tr("ReplayGain"), rg)
        menu.append_section(None, s3)
        s4 = Gio.Menu()
        s4.append(tr("Préférences"), "app.preferences")
        s4.append(tr("Raccourcis clavier"), "win.show-help-overlay")
        s4.append(tr("À propos d'Ithaca"), "app.about")
        s4.append(tr("Quitter"), "app.quit")
        menu.append_section(None, s4)
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, direction=direction,
                              css_classes=["circular"], valign=Gtk.Align.CENTER, primary=True,
                              tooltip_text=tr("Menu principal"))

    # -- actions ------------------------------------------------------------------------------
    def _install_actions(self):
        simple = {
            "play": self.c.toggle, "next": self.c.next, "previous": self.c.previous,
            "add-songs": self.add_songs, "add-folder": self.add_folder, "clear": self.c.clear,
            "restore": self.restore, "copy": self._copy, "open-folder": self._open_folder,
            "save-playlist": lambda: self.save_playlist(list(self.c.queue.items)),
            "append-playlist": lambda: self.append_playlist(list(self.c.queue.items)),
            "save-selection": lambda: self.save_playlist(self.playlist.selected_songs()),
            "append-selection": lambda: self.append_playlist(self.playlist.selected_songs()),
            "search": self._toggle_search, "toggle-playlist": self._toggle_playlist,
            "mute": lambda: self.c.set_muted(not settings["muted"]),
            "seek-forward": lambda: self.c.seek(min(self.c.player.duration(),
                                                    self.c.player.position() + settings["seek_step"])),
            "seek-backward": lambda: self.c.seek(max(0.0, self.c.player.position() - settings["seek_step"])),
        }
        for name, cb in simple.items():
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
        for name, fid in (("recolor", "recolor"), ("background", "background")):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(features.enabled(fid)))
            a.connect("activate", lambda act, _p, fid=fid: features.set_enabled(fid, not features.enabled(fid)))
            self.add_action(a)
        a = Gio.SimpleAction.new_stateful("replaygain", GLib.VariantType.new("s"),
                                          GLib.Variant.new_string(settings["replaygain"]))
        a.connect("activate", lambda _a, p: settings.__setitem__("replaygain", p.get_string()))
        self.add_action(a)
        for name, cb in (("play-index", self.c.play_index), ("show-index", self._show_index),
                         ("remove-index", self._remove_index)):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("i"))
            a.connect("activate", lambda _a, p, cb=cb: cb(p.get_int32()))
            self.add_action(a)
        app = self.get_application()
        for action, accels in (("win.next", ["<Control>Right"]), ("win.previous", ["<Control>Left"]),
                               ("win.add-songs", ["<Control>o"]), ("win.add-folder", ["<Control><Shift>o"]),
                               ("win.search", ["<Control>f"]), ("win.toggle-playlist", ["F9"]),
                               ("win.mute", ["<Control>m"]), ("win.seek-forward", ["<Shift>Right"]),
                               ("win.seek-backward", ["<Shift>Left"])):
            app.set_accels_for_action(action, accels)
        self._install_help()

    def _install_help(self):
        """Fenêtre des raccourcis clavier (Gtk.ShortcutsWindow de GTK 4.14), avec ? et Ctrl+?.
        Reconstruite quand « Saut avec ← / → » change (la durée est dans le libellé)."""
        win = Gtk.ShortcutsWindow(modal=True, transient_for=self)
        section = Gtk.ShortcutsSection(section_name="main", visible=True)
        n = settings["seek_step"]
        for title, items in (
                (tr("Lecture"), ((tr("Lecture / pause"), "space"), (tr("Morceau suivant"), "<Control>Right"),
                                 (tr("Morceau précédent"), "<Control>Left"),
                                 (ntr("Avancer de {0} seconde", "Avancer de {0} secondes", n).format(n), "<Shift>Right"),
                                 (ntr("Reculer de {0} seconde", "Reculer de {0} secondes", n).format(n), "<Shift>Left"),
                                 (tr("Couper le son"), "<Control>m"))),
                (tr("File de lecture"), ((tr("Ajouter des morceaux"), "<Control>o"),
                                         (tr("Ajouter un dossier"), "<Control><Shift>o"),
                                         (tr("Rechercher dans la file"), "<Control>f"),
                                         (tr("Afficher la file"), "F9"))),
                (tr("Général"), ((tr("Préférences"), "<Control>comma"), (tr("Raccourcis clavier"), "<Control>question"),
                                 (tr("Quitter"), "<Control>q")))):
            group = Gtk.ShortcutsGroup(title=title, visible=True)
            for label, accel in items:
                group.append(Gtk.ShortcutsShortcut(title=label, accelerator=accel, visible=True))
            section.append(group)
        win.add_section(section)
        self.set_help_overlay(win)

    def _show_index(self, index: int):
        if 0 <= index < len(self.c.queue):
            show_in_folder(self, self.c.queue.items[index].path)

    def _remove_index(self, index: int):
        if 0 <= index < len(self.c.queue):
            self.c.remove([self.c.queue.items[index]])

    def _copy(self):
        song = self.c.song
        if song is not None:
            text = " — ".join(x for x in (song.display_title, song.artist) if x)
            copy_to_clipboard(self, text)
            toast(self, tr("« {0} » copié").format(shown(text)))

    def _open_folder(self):
        song = self.c.song
        if song is not None:
            show_in_folder(self, song.path)

    def _toggle_search(self):
        self.set_playlist_shown(True)
        self.playlist.search_btn.set_active(not self.playlist.search_btn.get_active())
        if self.playlist.search_btn.get_active():
            self.playlist.search_entry.grab_focus()

    def _toggle_playlist(self):
        self.set_playlist_shown(not self.split.get_show_sidebar())

    def set_playlist_shown(self, show: bool):
        """Afficher / masquer la file. Masquée : la fenêtre passe au format compact (le lecteur seul,
        COMPACT_WIDTH) ; réaffichée : elle retrouve la largeur d'avant (ou s'élargit de la file si on l'a
        redimensionnée entre-temps). Masquer laissait sinon une grande fenêtre vide autour du lecteur
        (retour de Tof). Rien ne bouge si la fenêtre est agrandie ou en plein écran, ni pour masquer la
        file posée par-dessus le lecteur (fenêtre étroite, « repliée »)."""
        if self.split.get_show_sidebar() == show:
            return
        free = self.get_mapped() and not self.is_maximized() and not self.is_fullscreen()
        dw, dh = self.get_default_size()
        width = None
        if free and dw > 0:
            # Calcul sur la taille DEMANDÉE (connue tout de suite), jamais sur la taille mesurée : avec un
            # vrai gestionnaire de fenêtres, le redimensionnement arrive plus tard, et des clics rapides
            # faisaient grandir la fenêtre à chaque aller-retour.
            if self._frame is None:
                self._frame = max(dw - self.get_width(), 0)   # cadre CSD : compté par la taille par défaut
            current = dw - self._frame
            if not show and not self.split.get_collapsed():   # file à côté du lecteur (pas par-dessus)
                width = min(current, COMPACT_WIDTH)
                self._wide = (current, width)
            elif show:
                if self._wide and self._wide[1] == current:   # pas redimensionnée depuis : comme avant
                    width = self._wide[0]
                else:
                    width = width_with_playlist(current)      # file masquée : le lecteur a toute la fenêtre
                self._wide = None
        if show and width is None and self._narrow:
            self.split.set_collapsed(True)                    # fenêtre étroite qu'on n'élargit pas
        elif show and width is not None:
            # on élargit : la file s'affiche directement à côté, sans passer par-dessus le lecteur le
            # temps du redimensionnement (changer de mode en pleine animation → AdwGizmo)
            self.split.set_collapsed(False)
        self.split.set_show_sidebar(show)
        if width is not None:
            self.set_default_size(width + self._frame, dh)

    @staticmethod
    def _playlist_side() -> Gtk.PackType:
        """File à gauche (par défaut, comme Amberol) ou à droite (réglage playlist_side, idée de Tof :
        gauchers / droitiers ; repris de Nausicaa)."""
        return Gtk.PackType.END if settings["playlist_side"] == "end" else Gtk.PackType.START

    def _sync_side(self):
        """Ce qui suit le côté de la file : bouton de la barre de titre (posé de ce côté, icône assortie),
        flèche de fermeture de la file."""
        right = self._playlist_side() == Gtk.PackType.END
        if self.sidebar_btn.get_parent() is not None:
            self.content_header.remove(self.sidebar_btn)
        (self.content_header.pack_end if right else self.content_header.pack_start)(self.sidebar_btn)
        self.sidebar_btn.set_icon_name("sidebar-show-right-symbolic" if right else "sidebar-show-symbolic")
        self.playlist.back_btn.set_icon_name("go-next-symbolic" if right else "go-previous-symbolic")

    def _set_narrow(self, narrow: bool):
        self._narrow = narrow
        self.split.set_collapsed(narrow and self.split.get_show_sidebar())

    # -- ajout --------------------------------------------------------------------------------
    def _music_dir(self) -> str:
        d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
        return d if d and os.path.isdir(d) else os.path.expanduser("~")

    def add_songs(self):
        from ..backend.playlists import READ_EXTS
        from ..backend.song import AUDIO_EXTS
        exts = AUDIO_EXTS + READ_EXTS
        choose_files(self, tr("Ajouter des morceaux"), self._music_dir(), self.add_paths,
                     filters=[(tr("Musique et listes de lecture"), ["*" + e for e in exts] + ["*" + e.upper() for e in exts]),
                              (tr("Listes de lecture"), ["*" + e for e in READ_EXTS] + ["*" + e.upper() for e in READ_EXTS]),
                              (tr("Tous les fichiers"), ["*"])])

    # -- listes de lecture ---------------------------------------------------------------------
    def save_playlist(self, songs: list):
        """Nouvelle liste (.m3u, .m3u8 ou .xspf d'après le nom ; .m3u si aucun)."""
        if not songs:
            return
        from .common import choose_save
        choose_save(self, tr("Enregistrer la liste de lecture"),
                    os.path.join(self._music_dir(), tr("Liste de lecture") + ".m3u"),
                    lambda path: self.write_playlist(songs, path))

    def write_playlist(self, songs: list, path: str) -> str:
        from ..backend import playlists
        try:
            final = playlists.save(songs, path)
        except OSError as e:
            from .common import error
            error(self, tr("Impossible d'enregistrer la liste"), e.strerror or str(e))
            return ""
        toast(self, tr("Liste enregistrée : {0}").format(shown(os.path.basename(final))))
        self.playlist.set_selection_mode(False)
        return final

    def append_playlist(self, songs: list):
        """Compléter une liste existante (sans doublon, contenu gardé)."""
        if not songs:
            return
        from ..backend import playlists
        choose_files(self, tr("Ajouter à une liste de lecture"), self._music_dir(),
                     lambda paths: self.append_to_playlist(songs, paths[0]), multiple=False,
                     filters=[(tr("Listes de lecture"), ["*" + e for e in playlists.WRITE_EXTS])])

    def append_to_playlist(self, songs: list, path: str):
        from ..backend import playlists
        if not path.lower().endswith(playlists.WRITE_EXTS):
            toast(self, tr("Format non pris en charge : {0}").format(shown(os.path.basename(path))))
            return
        try:
            added, present = playlists.append(songs, path)
        except (OSError, ValueError, SyntaxError) as e:      # liste illisible (XML abîmé : ParseError)
            from .common import error
            error(self, tr("Impossible de compléter la liste"), str(e))
            return
        name = shown(os.path.basename(path))
        if added:
            msg = ntr("{0} morceau ajouté à « {1} »", "{0} morceaux ajoutés à « {1} »", added).format(added, name)
            if present:
                msg += " " + ntr("({0} déjà présent)", "({0} déjà présents)", present).format(present)
        else:
            msg = tr("Tout est déjà dans « {0} »").format(name)
        toast(self, msg)
        self.playlist.set_selection_mode(False)

    def add_folder(self):
        choose_folder(self, tr("Ajouter un dossier"), self._music_dir(), lambda p: self.add_paths([p]))

    def add_paths(self, paths: list[str], play: bool = False):
        def done(read, skipped):
            if not read and skipped:
                toast(self, tr("Aucun morceau lisible"))
            elif skipped:
                toast(self, ntr("{0} fichier ignoré (illisible)", "{0} fichiers ignorés (illisibles)",
                                skipped).format(skipped))
            elif not read:
                toast(self, tr("Aucun morceau trouvé"))
        self.c.add_paths(paths, play=play, on_done=done)

    def restore(self):
        self.restore_btn.set_sensitive(False)
        self.c.restore_saved()

    def _install_drop(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("enter", lambda *_: (self.drop_hint.set_visible(True), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: self.drop_hint.set_visible(False))
        target.connect("drop", self._on_drop)
        self.toast_overlay.add_controller(target)

    def _on_drop(self, _target, value, _x, _y):
        self.drop_hint.set_visible(False)
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if paths:
            self.add_paths(paths)
        return bool(paths)

    # Raccourcis de l'application sur des touches qui ont un sens dans un champ : GTK 4 traite les
    # raccourcis AVANT le widget qui a le focus (piège noté dans Odysseus). Dans la recherche,
    # Ctrl+← / → (mot par mot) et Maj+← / → (sélection) changeaient de morceau ou sautaient de 10 s :
    # ce contrôleur en capture les rend au champ (signal move-cursor de Gtk.Text).
    _TEXT_MOVES = {
        (Gdk.KEY_Left, Gdk.ModifierType.CONTROL_MASK): (Gtk.MovementStep.WORDS, -1, False),
        (Gdk.KEY_Right, Gdk.ModifierType.CONTROL_MASK): (Gtk.MovementStep.WORDS, 1, False),
        (Gdk.KEY_Left, Gdk.ModifierType.SHIFT_MASK): (Gtk.MovementStep.VISUAL_POSITIONS, -1, True),
        (Gdk.KEY_Right, Gdk.ModifierType.SHIFT_MASK): (Gtk.MovementStep.VISUAL_POSITIONS, 1, True),
    }

    def _on_key(self, _ctrl, keyval, _code, state):
        mods = state & Gtk.accelerator_get_default_mod_mask()
        focus = self.get_focus()
        move = self._TEXT_MOVES.get((keyval, mods))
        if move is not None and isinstance(focus, Gtk.Text):
            focus.emit("move-cursor", *move)
            return True
        # Espace = lecture / pause partout, même quand un bouton a le focus : sinon Espace « cliquait »
        # le dernier bouton utilisé (suivant, aléatoire…), retour de Tof. Restent à Espace : les champs
        # (taper un espace), les menus ouverts, les cases du mode sélection.
        if keyval == Gdk.KEY_space and not mods and not isinstance(focus, TEXT_WIDGETS + (Gtk.CheckButton,)) \
                and (focus is None or focus.get_ancestor(Gtk.Popover) is None):
            self.c.toggle()
            return True
        return False

    # -- état ---------------------------------------------------------------------------------
    def _on_event(self, event, *args):
        if event == "position":
            self._sync_position(args[0])
        elif event == "song":
            self._sync_song()
        elif event == "cover":                            # logo d'une webradio arrivé
            if args and args[0] is self.c.song:
                self._sync_song()
        elif event == "state":
            self._sync_state()
        elif event in ("queue", "loading"):
            self._sync_page()
            self._sync_state()
        elif event == "modes":
            self._sync_modes()
        elif event == "volume":
            self._sync_volume()
        elif event == "error":
            song, message = args
            name = song.display_title if song else ""
            toast(self, tr("Lecture impossible : {0}").format(shown(name or message)))
        elif event == "ended":
            self._sync_state()

    def _sync_all(self):
        self._sync_page()
        self._sync_song()
        self._sync_state()
        self._sync_modes()
        self._sync_volume()
        self._sync_playlist_button()

    def _sync_page(self):
        has = len(self.c.queue) > 0
        self.stack.set_visible_child_name("main" if has else "empty")
        self.restore_btn.set_visible(not has and load_saved() is not None)
        self.restore_btn.set_sensitive(not self.c.loading)

    def _sync_song(self):
        song = self.c.song
        if song is None:
            self.title_label.set_label("")
            self.artist_label.set_label("")
            self.album_label.set_label("")
            self.cover.set_cover("")
            self.waveform.set_peaks(None)
            self.waveform.set_position(0, 0)
            self._apply_recolor("")
            self.set_title("Ithaca")
            return
        self.title_label.set_label(shown(song.display_title))
        self.title_label.set_tooltip_text(shown(song.display_title))
        self.artist_label.set_label(shown(artist_line(song)))
        self.album_label.set_label(shown(song.album))
        self.album_label.set_visible(bool(song.album))
        self.cover.set_cover(song.cover)
        self.set_title(shown(" — ".join(x for x in (song.display_title, song.artist) if x)))
        if self.waveform.song_path != song.path:          # titre d'une webradio changé : même flux
            self.waveform.song_path = song.path
            self.waveform.set_peaks(None)
            self.waveform.set_position(0, song.duration)
            if features.enabled("waveform") and not song.stream:
                self.waveforms.request(song, self._on_peaks)
        self._apply_recolor(song.cover)
        self._sync_position(self.c.player.position())

    def _on_peaks(self, song, peaks):
        if song is self.c.song:
            self.waveform.set_peaks(peaks)

    def _sync_position(self, pos: float):
        song = self.c.song
        if song is not None and song.stream:
            self.waveform.set_position(0, 0)               # pas de durée : ni forme d'onde ni avance
            self.elapsed.set_label(fmt.duration(pos))
            self.remaining.set_label(tr("En direct"))
            return
        dur = self.c.player.duration() if song is not None else 0.0
        self.waveform.set_position(pos, dur)
        self.elapsed.set_label(fmt.duration(pos))
        self.remaining.set_label("-" + fmt.duration(max(0.0, dur - pos)) if dur else "0:00")

    def _sync_state(self):
        playing = self.c.playing
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_btn.set_tooltip_text(tr("Pause") if playing else tr("Lire"))
        has = len(self.c.queue) > 0
        for name in ("play", "next", "previous", "copy", "open-folder", "clear", "save-playlist", "append-playlist"):
            self.lookup_action(name).set_enabled(has)
        app = self.get_application()
        if app is not None:
            app.sync_inhibit(playing)

    def _sync_modes(self):
        if self.shuffle_btn.get_active() != self.c.queue.shuffled:
            self.shuffle_btn.set_active(self.c.queue.shuffled)
        mode = self.c.queue.repeat
        self.repeat_btn.set_icon_name(REPEAT_ICONS.get(mode, REPEAT_ICONS["consecutive"]))
        self.repeat_btn.set_tooltip_text({"consecutive": tr("Répétition : désactivée"),
                                          "all": tr("Répétition : toute la file"),
                                          "one": tr("Répétition : ce morceau")}[mode])
        (self.repeat_btn.add_css_class if mode != "consecutive" else self.repeat_btn.remove_css_class)("active-mode")

    def _sync_volume(self):
        muted = settings["muted"]
        upper = self.c.max_volume()
        adj = self.volume_scale.get_adjustment()
        if abs(adj.get_upper() - upper) > 0.001:          # « Volume maximal » changé
            self._dragging_volume = True
            adj.set_upper(upper)
            self.volume_scale.clear_marks()
            if upper > 1.0:
                self.volume_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)   # repère des 100 %
            self._dragging_volume = False
        if abs(self.volume_scale.get_value() - settings["volume"]) > 0.001:
            self._dragging_volume = True
            self.volume_scale.set_value(settings["volume"])
            self._dragging_volume = False
        self.mute_btn.set_icon_name("audio-volume-muted-symbolic" if muted else "audio-volume-low-symbolic")
        self.mute_btn.set_tooltip_text(tr("Rétablir le son") if muted else tr("Couper le son"))

    def _on_volume_changed(self, scale):
        if not self._dragging_volume:
            self.c.set_muted(False)
            self.c.set_volume(scale.get_value())

    def _on_volume_wheel(self, _ctrl, _dx, dy):
        if dy:
            step = settings["wheel_step"]
            self.c.change_volume(-step if dy > 0 else step)
        return True                                       # pas aussi la molette de Gtk.Scale

    def _on_playlist_toggled(self, button):
        if self.split.get_show_sidebar() != button.get_active():
            self.set_playlist_shown(button.get_active())

    def _sync_playlist_button(self):
        shown_ = self.split.get_show_sidebar()
        for btn in (self.playlist_btn, self.sidebar_btn):
            if btn.get_active() != shown_:
                btn.set_active(shown_)
        self.playlist.back_btn.set_visible(self.split.get_collapsed())
        if not self.split.get_collapsed():
            settings["playlist_visible"] = shown_

    def _apply_recolor(self, cover: str):
        if features.enabled("recolor") and cover:
            self.recolor.apply(cover)
            self.add_css_class("recolored")
        else:
            self.recolor.clear()
            self.remove_css_class("recolored")

    def _on_setting(self, key, value):
        if key == "features":
            self.lookup_action("recolor").set_state(GLib.Variant.new_boolean(features.enabled("recolor")))
            self.lookup_action("background").set_state(GLib.Variant.new_boolean(features.enabled("background")))
            self._apply_recolor(self.c.song.cover if self.c.song else "")
            if features.enabled("waveform") and self.c.song is not None:
                self.waveforms.request(self.c.song, self._on_peaks)
            elif not features.enabled("waveform"):
                self.waveforms.cancel()
                self.waveform.set_peaks(None)
        elif key == "replaygain":
            self.lookup_action("replaygain").set_state(GLib.Variant.new_string(value))
        elif key == "playlist_side":
            self.split.set_sidebar_position(self._playlist_side())     # tout de suite, sans redémarrer
            self._sync_side()
        elif key == "seek_step":
            self.waveform.step = float(value)
            self._install_help()                          # libellés « Avancer de N secondes »

    # -- fermeture ------------------------------------------------------------------------------
    def _on_close_request(self, *_):
        w, h = self.get_default_size()
        if not self.is_maximized() and w > 0 and h > 0:
            settings["window_width"], settings["window_height"] = w, h
        app = self.get_application()
        if app is not None and features.enabled("background") and self.c.playing:
            app.go_background(self)          # la musique continue, fenêtre masquée
            return True
        self.c.disconnect(self._listener)
        self.playlist.close()
        settings.disconnect(self._on_setting)
        self.waveforms.cancel()
        self.recolor.clear()
        return False


GObject.type_ensure(WaveformView)
