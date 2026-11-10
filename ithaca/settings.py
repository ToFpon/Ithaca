"""Préférences persistantes (JSON dans ~/.config/ithaca). Seules les valeurs choisies par
l'utilisateur sont écrites : une valeur par défaut corrigée plus tard s'applique à tous ceux qui
n'y ont pas touché."""

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
    # Lecture
    "volume": 1.0,                  # 0..volume_max / 100 (échelle cubique, comme GNOME)
    "volume_max": 100,              # 100 ou 150 % (au-delà de 100 : amplifié, comme VLC)
    "seek_step": 10,                # Maj+← / → et ← / → sur la forme d'onde (secondes)
    "wheel_step": 5,                # un cran de molette sur le volume (%)
    "muted": False,
    "repeat": "consecutive",        # consecutive (s'arrête à la fin), all, one
    "shuffle": False,
    "replaygain": "album",          # album, track, off
    # Fenêtre
    "window_width": 800,
    "window_height": 720,
    "playlist_visible": True,
    "playlist_side": "start",       # file à gauche (start, comme Amberol) ou à droite (end) — gauchers / droitiers
    "features": {},                 # fonctionnalités activées / désactivées (voir features.py)
}


class Settings:
    def __init__(self, path: str = CONFIG_FILE):
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data = {k: v for k, v in loaded.items() if k in DEFAULTS}
        except (OSError, ValueError):
            pass
        self._listeners = []

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        if self[key] == value:
            return
        self.data[key] = value
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
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass


settings = Settings()
