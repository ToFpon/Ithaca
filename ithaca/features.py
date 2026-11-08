"""Fonctionnalités facultatives d'Ithaca, toutes désactivables sans redémarrage.

Chaque fonctionnalité se consulte au moment de l'usage (enabled(id)), jamais à l'import :
le menu et Préférences → Fonctionnalités l'activent ou la coupent tout de suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import tr
from .settings import settings


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    description: str
    icon: str
    default: bool = True


FEATURES = [
    Feature("waveform", tr("Barre en forme d'onde"),
            tr("La barre de progression dessine la forme d'onde du morceau (calculée une fois, gardée en cache)"),
            "media-playback-start-symbolic"),
    Feature("mpris", tr("Contrôle depuis le système"),
            tr("Panneau de GNOME, écran verrouillé et touches multimédia du clavier (MPRIS)"),
            "input-keyboard-symbolic"),
    Feature("background", tr("Lecture en arrière-plan"),
            tr("Fermer la fenêtre pendant la lecture laisse la musique jouer"),
            "window-minimize-symbolic"),
    Feature("recolor", tr("Couleurs de la pochette"),
            tr("Teinter la fenêtre d'après la pochette du morceau, comme Amberol ; sinon les couleurs "
               "du thème du système"),
            "applications-graphics-symbolic", default=False),
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
