"""Durées affichées."""

from __future__ import annotations

from .i18n import ntr


def duration(seconds: float) -> str:
    """« 3:07 », « 1:02:45 »."""
    s = int(max(0, seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def long_duration(seconds: float) -> str:
    """Durée totale de la file : « 42 minutes », « 2 h 05 »."""
    minutes = int(round(max(0, seconds) / 60))
    if minutes < 60:
        return ntr("{0} minute", "{0} minutes", minutes).format(minutes)
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d}"
