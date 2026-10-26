"""Préférences persistantes (JSON dans ~/.config/ithaca)."""

from __future__ import annotations

import json
import os
from typing import Any

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "ithaca")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "ithaca")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "ithaca")

DEFAULTS: dict[str, Any] = {
    "theme": "system",              # system, light, dark
    "language": "system",           # system, fr, en, de (appliqué au prochain démarrage)
    # Téléchargements
    "download_dir": "",             # vide = dossier Téléchargements de l'utilisateur (XDG)
    "ask_dir": False,               # demander le dossier à chaque téléchargement
    "simultaneous": 5,              # téléchargements actifs en même temps (les autres attendent)
    "segments": 5,                  # connexions par fichier, 5 comme Varia (aria2 : split / max-connection-per-server)
    "speed_limit_enabled": False,
    "speed_limit": 1000,            # Ko/s (1 Ko = 1000 octets, comme GNOME), toutes les connexions confondues
    # Connexion
    "auth_enabled": False,          # authentification HTTP / FTP envoyée avec chaque téléchargement
    "auth_user": "",
    "auth_password": "",
    "cookies_file": "",             # cookies.txt (format Netscape), envoyé aux serveurs
    # Vidéos (yt-dlp)
    "video_mode": "video",          # video ou audio (choix proposé par défaut dans la fenêtre)
    "video_height": 0,              # 0 = meilleure qualité, sinon hauteur maximale (1080, 720…)
    "video_container": "mp4",       # mp4 (lecture partout) ou mkv
    "audio_format": "mp3",          # mp3, m4a, opus, flac, wav
    "browser_cookies": "",          # navigateur dont yt-dlp lit les cookies (YouTube « pas un robot »)
    "tools_auto_update": True,      # yt-dlp et Deno tenus à jour (vérification une fois par jour)
    "tools_last_check": 0,
    # Fenêtre
    "window_width": 900,
    "window_height": 620,
    "features": {},                 # fonctionnalités activées / désactivées (voir features.py)
}


# Anciennes valeurs par défaut (0.1.0 écrivait toutes les valeurs dans settings.json) : retirées du
# fichier pour que les nouvelles valeurs par défaut s'appliquent à qui ne les a jamais changées.
OLD_DEFAULTS = {"segments": 16, "speed_limit": 1024}
VERSION = 2


class Settings:
    """Seules les valeurs choisies par l'utilisateur sont enregistrées : une valeur par défaut
    corrigée dans une version suivante s'applique à tous ceux qui n'y ont pas touché."""

    def __init__(self, path: str = CONFIG_FILE):
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass
        if self.data and self.data.get("version") != VERSION:
            self.data = {k: v for k, v in self.data.items()
                         if k in DEFAULTS and v != DEFAULTS[k] and OLD_DEFAULTS.get(k, object()) != v}
            self.data["version"] = VERSION
            self.save()
        self._listeners = []

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        if self.data.get(key, DEFAULTS.get(key)) == value:
            return
        self.data[key] = value
        self.data["version"] = VERSION
        self.save()
        for cb in list(self._listeners):
            cb(key, value)

    def connect(self, cb):
        self._listeners.append(cb)

    def disconnect(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            # 0600 : le fichier peut contenir le mot de passe d'authentification
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass


settings = Settings()


def download_dir() -> str:
    """Dossier de téléchargement choisi, sinon celui de l'utilisateur (Téléchargements, XDG)."""
    chosen = settings["download_dir"]
    if chosen and os.path.isdir(chosen):
        return chosen
    from gi.repository import GLib
    xdg = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    if xdg and os.path.isdir(xdg):
        return xdg
    return os.path.expanduser("~")
