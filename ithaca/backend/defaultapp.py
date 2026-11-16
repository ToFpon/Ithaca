"""Ithaca lecteur de musique par défaut (Préférences → Général, demande de Tof), un seul interrupteur,
repris de celui de Nausicaa (validé chez Tof) et d'Odysseus :

- activé : Ithaca devient l'application par défaut des fichiers AUDIO seulement — ni les dossiers
  (inode/directory, déclaré par le .desktop pour « Ouvrir avec ») ni les listes de lecture (VLC, webradios :
  choix de Tof) — dans ~/.config/mimeapps.list, comme `xdg-mime default` ; l'application d'avant est notée
  pour chaque type (`previous_audio_apps`) ;
- coupé : l'application d'avant revient (sinon les associations du système).

L'état affiché est celui du système pour le type principal `MAIN` (audio/mpeg), relu à l'ouverture des
Préférences. PIÈGES (retour de Tof, 1.4.1 : interrupteur éteint à la réouverture) :
- « <bureau>-mimeapps.list » (zorin-, gnome-…, d'après XDG_CURRENT_DESKTOP) est lu AVANT mimeapps.list :
  ses lignes pour nos types sont retirées (leçon de Nausicaa / Odysseus) ;
- les ALIAS : GIO cherche sous le nom canonique (audio/x-wav → audio/vnd.wave, mime/aliases) ; on écrit
  sous chaque type, son nom canonique (`_targets`) et les alias déjà présents dans les fichiers."""

from __future__ import annotations

import configparser
import os

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
MAIN = AUDIO_TYPES[0]
COMMAND = f"xdg-mime default {DESKTOP} " + " ".join(AUDIO_TYPES)
# affiché dans les Préférences : 16 types en entier seraient illisibles
SHORT_COMMAND = f"xdg-mime default {DESKTOP} audio/mpeg audio/flac …"


def _app(desktop_id: str):
    if not desktop_id:
        return None
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error):          # PyGObject : « constructor returned NULL »
        return None


def installed() -> bool:
    """Le .desktop d'Ithaca est-il installé ? (lancé depuis les sources : le système ne le connaît pas)"""
    return _app(DESKTOP) is not None


def current(ctype: str) -> str:
    app = Gio.AppInfo.get_default_for_type(ctype, False)
    return (app.get_id() or "") if app is not None else ""


def is_default() -> bool:
    return current(MAIN) == DESKTOP


def _aliases() -> dict[str, str]:
    """alias → nom canonique, d'après mime/aliases de la base shared-mime-info (dossiers XDG)."""
    table: dict[str, str] = {}
    for base in reversed([GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())):
        try:
            with open(os.path.join(base, "mime", "aliases"), encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) == 2:
                        table[parts[0]] = parts[1]      # le dossier le plus prioritaire lu en dernier
        except OSError:
            pass
    return table


def _targets() -> list[str]:
    """Nos types, et le nom canonique de ceux qui sont des alias (sans doublon, dans l'ordre)."""
    aliases = _aliases()
    out: list[str] = []
    for mime in AUDIO_TYPES:
        for t in (mime, aliases.get(mime)):
            if t and t not in out:
                out.append(t)
    return out


def set_default(on: bool) -> bool:
    """Fait (ou défait) d'Ithaca le lecteur par défaut des fichiers audio. True si c'est fait."""
    try:
        if on:
            app = _app(DESKTOP)
            if app is None:
                return False
            before = dict(settings["previous_audio_apps"])
            for t in _targets():
                cur = current(t)
                if cur and cur != DESKTOP:
                    before[t] = cur
                set_default_app(app, t)
            settings["previous_audio_apps"] = before
        else:
            before = settings["previous_audio_apps"]
            for t in _targets():
                other = _app(before.get(t, ""))
                if other is not None and other.get_id() != DESKTOP:
                    set_default_app(other, t)
                else:
                    Gio.AppInfo.reset_type_associations(t)
                    _forget_us(t)
            settings["previous_audio_apps"] = {}
    except GLib.Error as e:
        print(f"{APP_ID}: lecteur par défaut :", e.message)
        return False
    return True


def _mimeapps_files() -> list:
    config = GLib.get_user_config_dir()
    names = ["mimeapps.list"] + [d.lower() + "-mimeapps.list"
                                 for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]
    return [os.path.join(config, n) for n in names]


def set_default_app(app: Gio.AppInfo, ctype: str):
    """`app` par défaut pour `ctype`, alias compris (repris de Nausicaa / Odysseus)."""
    app.set_as_default_for_type(ctype)
    files = _mimeapps_files()
    aliases = set()
    for path in files:
        parser = configparser.RawConfigParser(strict=False, delimiters=("=",), interpolation=None)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            continue
        if parser.has_section("Default Applications"):
            aliases.update(k for k in parser.options("Default Applications")
                           if k != ctype and Gio.content_type_equals(k, ctype))
    for alias in sorted(aliases):
        try:
            app.set_as_default_for_type(alias)
        except GLib.Error:
            pass
    for path in files[1:]:                   # « <bureau>-mimeapps.list », lu avant mimeapps.list
        _drop_default_lines(path, ctype)


def _forget_us(ctype: str):
    """Coupé sans application d'avant : nos lignes pour ce type et ses alias retirées partout."""
    for path in _mimeapps_files():
        _drop_default_lines(path, ctype, only=DESKTOP)


def _drop_default_lines(path: str, ctype: str, only: str = ""):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return
    out, section, changed = [], "", False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif section == "Default Applications" and "=" in stripped and not stripped.startswith("#"):
            key, value = (x.strip() for x in stripped.split("=", 1))
            if (key == ctype or Gio.content_type_equals(key, ctype)) and (not only or value.split(";")[0] == only):
                changed = True
                continue
        out.append(line)
    if changed:
        tmp = path + ".tmp-defaultapp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(out)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
