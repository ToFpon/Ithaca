"""Ithaca lecteur de musique par défaut (Préférences → Général, demande de Tof, comme Odysseus) : un
interrupteur qui fait l'équivalent de `xdg-mime default io.github.ithaca.Ithaca.desktop <type>` pour
les fichiers audio SEULEMENT — ni les dossiers (inode/directory, déclaré par le .desktop pour « Ouvrir
avec ») ni les listes de lecture (souvent ouvertes avec VLC, webradios : choix de Tof).

GIO écrit dans ~/.config/mimeapps.list. Avant de prendre la main, l'application par défaut de chaque
type est retenue (réglage `previous_audio_apps`) ; coupé, elle est rétablie (Rhythmbox, VLC…), sinon
notre association est retirée et le système reprend son choix. L'état affiché est celui du système."""

from __future__ import annotations

from gi.repository import Gio, GLib

from .. import APP_ID
from ..settings import settings

DESKTOP = APP_ID + ".desktop"
# les types audio du .desktop, sans les listes (audio/x-mpegurl, audio/mpegurl, audio/x-scpls)
AUDIO_TYPES = (
    "audio/mpeg", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/x-opus+ogg",
    "audio/mp4", "audio/x-m4a", "audio/aac", "audio/x-wav", "audio/x-wavpack", "audio/x-ape",
    "audio/x-musepack", "audio/x-aiff", "audio/x-ms-wma", "audio/x-matroska",
)
COMMAND = f"xdg-mime default {DESKTOP} " + " ".join(AUDIO_TYPES)
# affiché dans les Préférences : 16 types en entier seraient illisibles
SHORT_COMMAND = f"xdg-mime default {DESKTOP} audio/mpeg audio/flac …"


def _app() -> Gio.DesktopAppInfo | None:
    try:
        return Gio.DesktopAppInfo.new(DESKTOP)
    except (TypeError, GLib.Error):
        return None


def installed() -> bool:
    """Le .desktop d'Ithaca est-il installé ? (lancé depuis les sources : le système ne le connaît pas)"""
    return _app() is not None


def _default_id(mime: str) -> str:
    app = Gio.AppInfo.get_default_for_type(mime, False)
    return app.get_id() or "" if app is not None else ""


def is_default() -> bool:
    return all(_default_id(t) == DESKTOP for t in AUDIO_TYPES)


def set_default(on: bool) -> bool:
    """Fait (ou défait) d'Ithaca le lecteur par défaut des fichiers audio. True si c'est fait."""
    try:
        if on:
            app = _app()
            if app is None:
                return False
            previous = dict(settings["previous_audio_apps"])
            for mime in AUDIO_TYPES:
                old = _default_id(mime)
                if old and old != DESKTOP:
                    previous[mime] = old
                app.set_as_default_for_type(mime)
            settings["previous_audio_apps"] = previous
        else:
            previous = settings["previous_audio_apps"]
            for mime in AUDIO_TYPES:
                old = previous.get(mime)
                try:
                    back = Gio.DesktopAppInfo.new(old) if isinstance(old, str) and old else None
                except (TypeError, GLib.Error):
                    back = None
                if back is not None:                     # l'ancien lecteur, s'il est encore installé
                    back.set_as_default_for_type(mime)
                elif _default_id(mime) == DESKTOP:
                    Gio.AppInfo.reset_type_associations(mime)
            settings["previous_audio_apps"] = {}
    except GLib.Error as e:
        print("ithaca: lecteur par défaut :", e.message)
        return False
    return True
