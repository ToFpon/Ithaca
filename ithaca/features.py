"""Fonctionnalités facultatives d'Ithaca, toutes désactivables sans redémarrage.

Chaque fonctionnalité se consulte au moment de l'usage (enabled(id)), jamais à l'import :
Préférences → Fonctionnalités l'active ou la coupe tout de suite.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from .i18n import tr
from .settings import settings


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    description: str
    icon: str
    # Programmes nécessaires : chaque groupe est une liste d'alternatives (l'un d'eux suffit).
    tools: tuple = field(default_factory=tuple)
    packages: str = ""          # paquets Debian/Ubuntu à installer si un programme manque
    python: tuple = field(default_factory=tuple)   # modules Python nécessaires
    default: bool = True


FEATURES = [
    Feature("videos", tr("Vidéos et audio"),
            tr("Télécharger des vidéos, de la musique et des playlists (YouTube et plus de mille sites) "
               "avec yt-dlp, installé et tenu à jour par Ithaca"),
            "camera-video-symbolic", tools=(("ffmpeg",),), packages="ffmpeg"),
    Feature("notifications", tr("Notifications"),
            tr("Prévenir quand un téléchargement est terminé ou a échoué, fenêtre fermée ou non"),
            "preferences-system-notifications-symbolic"),
]

BY_ID = {f.id: f for f in FEATURES}


def enabled(fid: str) -> bool:
    f = BY_ID.get(fid)
    if f is None:
        return False
    return bool(settings["features"].get(fid, f.default))


def set_enabled(fid: str, value: bool):
    feats = dict(settings["features"])
    feats[fid] = bool(value)
    settings["features"] = feats


def present(name: str) -> bool:
    """Programme dans le PATH ou module Python (« py:libtorrent »)."""
    if name.startswith("py:"):
        import importlib.util
        return importlib.util.find_spec(name[3:]) is not None
    return shutil.which(name) is not None


def missing(fid: str) -> list[str]:
    f = BY_ID[fid]
    out = [" / ".join(group) for group in f.tools if not any(present(t) for t in group)]
    out += ["python3-" + m for m in f.python if not present("py:" + m)]
    return out


def available(fid: str) -> bool:
    """Activée et utilisable (programmes indispensables présents)."""
    return enabled(fid) and not missing(fid)


def install_command(fid: str) -> str:
    f = BY_ID[fid]
    return f"sudo apt install {f.packages}" if missing(fid) and f.packages else ""
