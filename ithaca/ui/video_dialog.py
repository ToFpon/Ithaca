"""Fenêtre de choix d'une vidéo / playlist (comme Varia) : type, qualité, format, sous-titres, dossier."""

from __future__ import annotations

import threading
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..backend import videos  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import download_dir, settings  # noqa: E402
from .common import DialogWindow, choose_folder, combo_row, shown  # noqa: E402

AUDIO_LABELS = ["MP3", "M4A", "Opus", "FLAC", "WAV"]
CONTAINER_LABELS = ["MP4", "MKV"]
BOT_HINT = "not a bot"


def friendly_error(message: str) -> str:
    """Message de yt-dlp → explication utile quand on la connaît."""
    if BOT_HINT in message:
        return tr("YouTube demande de confirmer que vous n'êtes pas un robot. Choisissez votre navigateur "
                  "dans Préférences → Vidéos → Cookies du navigateur (il faut y être connecté à YouTube).")
    if "Unsupported URL" in message:
        return tr("Ce site n'est pas pris en charge par yt-dlp.")
    if "Private video" in message or "private" in message.lower() and "video" in message.lower():
        return tr("Cette vidéo est privée.")
    return message


class VideoDialog(DialogWindow):
    def __init__(self, manager, url: str):
        super().__init__(title=tr("Vidéo / audio"), content_width=520, content_height=640)
        self.manager = manager
        self.url = url
        self.info = None
        self.folder = download_dir()
        self.closed = False

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=tr("Annuler"))
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.go = Gtk.Button(label=tr("Télécharger"), css_classes=["suggested-action"], sensitive=False)
        self.go.connect("clicked", lambda *_: self._download())
        header.pack_end(self.go)
        tv.add_top_bar(header)
        self.set_default_widget(self.go)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                          margin_start=24, margin_end=24)
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        loading.append(spinner)
        loading.append(Gtk.Label(label=tr("Lecture des informations…"), css_classes=["title-4"]))
        loading.append(Gtk.Label(label=shown(url), css_classes=["dim-label", "caption"], wrap=True,
                                 wrap_mode=Pango.WrapMode.CHAR, max_width_chars=50))
        self.stack.add_named(loading, "loading")
        self.error_page = Adw.StatusPage(icon_name="dialog-error-symbolic",
                                         title=tr("Impossible de lire cette adresse"))
        self.stack.add_named(self.error_page, "error")
        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "options")
        tv.set_content(self.stack)
        self.set_child(tv)
        self.connect("closed", lambda *_: setattr(self, "closed", True))
        threading.Thread(target=self._probe, daemon=True).start()

    # -- lecture des informations (fil) ----------------------------------------------------
    def _probe(self):
        try:
            info = videos.probe(self.url)
        except videos.VideoError as e:
            GLib.idle_add(self._show_error, str(e))
            return
        GLib.idle_add(self._show_options, videos.summarize(info))

    def _show_error(self, message: str):
        if not self.closed:
            # la description d'Adw.StatusPage est du balisage Pango : « <HTTPError 404> » la cassait
            self.error_page.set_description(GLib.markup_escape_text(shown(friendly_error(message))))
            self.stack.set_visible_child_name("error")
        return False

    def _show_options(self, info: dict):
        if self.closed:
            return False
        self.info = info
        self._build(info)
        self.stack.set_visible_child_name("options")
        self.go.set_sensitive(True)
        self.go.grab_focus()
        if info["thumbnail"]:
            threading.Thread(target=self._fetch_thumbnail, args=(info["thumbnail"],), daemon=True).start()
        return False

    def _fetch_thumbnail(self, url: str):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(5_000_000)
        except (OSError, ValueError):
            return
        GLib.idle_add(self._set_thumbnail, data)

    def _set_thumbnail(self, data: bytes):
        if self.closed:
            return False
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return False                     # format d'image non pris en charge (webp…) : pas d'aperçu
        self.picture.set_paintable(texture)
        self.picture.set_visible(True)
        return False

    # -- options ---------------------------------------------------------------------------
    def _build(self, info: dict):
        head = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, height_request=200, visible=False,
                                   css_classes=["video-thumbnail"], can_shrink=True)
        box.append(self.picture)
        box.append(Gtk.Label(label=shown(info["title"]), wrap=True, xalign=0, css_classes=["title-3"]))
        details = []
        if info["playlist"]:
            details.append(ntr("Playlist : {0} élément", "Playlist : {0} éléments", info["count"]).format(info["count"]))
        if info["uploader"]:
            details.append(info["uploader"])
        if info["duration"]:
            details.append(fmt.duration(info["duration"]))
        if details:
            box.append(Gtk.Label(label=shown(" · ".join(details)), xalign=0, wrap=True, css_classes=["dim-label"]))
        head.add(box)
        self.page.add(head)

        group = Adw.PreferencesGroup(title=tr("Télécharger"))
        modes = [tr("Vidéo"), tr("Audio seul")] if info["has_video"] else [tr("Audio seul")]
        self.mode_values = ["video", "audio"] if info["has_video"] else ["audio"]
        cur = settings["video_mode"] if settings["video_mode"] in self.mode_values else self.mode_values[0]
        self.mode = combo_row(tr("Type"), modes, self.mode_values.index(cur))
        group.add(self.mode)

        self.height_values = [0] + info["heights"]
        labels = [tr("Meilleure")] + [f"{h}p" for h in info["heights"]]
        want = int(settings["video_height"])
        sel = 0
        if want:
            fitting = [i for i, h in enumerate(self.height_values) if h and h <= want]
            sel = fitting[0] if fitting else 0
        self.quality = combo_row(tr("Qualité"), labels, sel)
        group.add(self.quality)
        self.container = combo_row(tr("Format"), CONTAINER_LABELS,
                                   videos.CONTAINERS.index(settings["video_container"])
                                   if settings["video_container"] in videos.CONTAINERS else 0)
        group.add(self.container)
        self.audio = combo_row(tr("Format audio"), AUDIO_LABELS,
                               videos.AUDIO_FORMATS.index(settings["audio_format"])
                               if settings["audio_format"] in videos.AUDIO_FORMATS else 0)
        group.add(self.audio)
        self.sub_values = [""] + list(info["subtitles"])
        self.subs = combo_row(tr("Sous-titres"), [tr("Aucun")] + [shown(n) for n in info["subtitles"].values()], 0,
                              tr("Intégrés à la vidéo"))
        self.subs.set_visible(bool(info["subtitles"]))
        group.add(self.subs)
        self.page.add(group)

        where = Adw.PreferencesGroup(title=tr("Enregistrer dans"))
        self.folder_row = Adw.ActionRow(title=shown(self.folder), subtitle_selectable=True)
        if info["playlist"]:
            self.folder_row.set_subtitle(tr("Dans un sous-dossier « {0} »").format(shown(info["title"])))
        browse = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Choisir le dossier"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        browse.connect("clicked", self._choose_folder)
        self.folder_row.add_suffix(browse)
        self.folder_row.set_activatable_widget(browse)
        where.add(self.folder_row)
        self.page.add(where)

        self.mode.connect("notify::selected", lambda *_: self._sync())
        self._sync()

    def _sync(self):
        video = self.mode_values[self.mode.get_selected()] == "video"
        self.quality.set_visible(video and len(self.height_values) > 1)
        self.container.set_visible(video)
        self.audio.set_visible(not video)
        self.subs.set_visible(video and len(self.sub_values) > 1)

    def _choose_folder(self, button):
        def done(path):
            self.folder = path
            self.folder_row.set_title(shown(path))
        choose_folder(button, tr("Enregistrer dans"), self.folder, done)

    def options(self) -> dict:
        mode = self.mode_values[self.mode.get_selected()]
        return {"mode": mode,
                "height": self.height_values[self.quality.get_selected()] if mode == "video" else 0,
                "container": videos.CONTAINERS[self.container.get_selected()],
                "audio_format": videos.AUDIO_FORMATS[self.audio.get_selected()],
                "subs": [s for s in [self.sub_values[self.subs.get_selected()]] if s] if mode == "video" else [],
                "playlist": bool(self.info and self.info["playlist"]),
                "title": self.info["title"] if self.info else ""}

    def _download(self):
        if self.info is None:
            return
        opts = self.options()
        # derniers choix gardés pour la prochaine fois
        settings["video_mode"] = opts["mode"]
        if opts["mode"] == "video":
            settings["video_height"] = opts["height"]
            settings["video_container"] = opts["container"]
        else:
            settings["audio_format"] = opts["audio_format"]
        self.manager.add_video(self.url, opts, self.folder)
        self.close()
