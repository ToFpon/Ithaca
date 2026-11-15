"""Égaliseur 10 bandes (demande de Tof) : celui de GStreamer (`equalizer-10bands`, gst-plugins-good,
déjà requis), placé avant ReplayGain dans l'audio-filter de playbin, réglé en direct.

Gains de -12 à +12 dB par bande. Jamais de saturation (même règle que ReplayGain) : le volume général
baisse de la plus forte hausse (`headroom`) — une bande à +6 dB → -6 dB pour tout le reste.
Préréglages : identifiants stables (enregistrés dans les réglages), noms traduits par l'interface.
"""

from __future__ import annotations

BANDS = (29, 59, 119, 237, 474, 947, 1889, 3770, 7523, 15011)     # Hz, celles d'equalizer-10bands
MIN_DB, MAX_DB = -12.0, 12.0

PRESETS: dict[str, tuple[float, ...]] = {
    "flat": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "rock": (5, 4, 3, 1, -1, -1, 1, 3, 4, 5),
    "pop": (-1, 0, 2, 4, 4, 2, 0, -1, -1, -1),
    "jazz": (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "classical": (3, 2, 1, 0, 0, 0, -1, -1, 0, 2),
    "electronic": (6, 5, 2, 0, -1, 1, 0, 2, 4, 5),
    "bass": (7, 6, 4, 2, 0, 0, 0, 0, 0, 0),
    "treble": (0, 0, 0, 0, 0, 1, 3, 5, 6, 7),
    "vocal": (-2, -2, -1, 1, 3, 4, 3, 1, 0, -1),
    "loudness": (6, 4, 1, 0, -1, -1, 0, 1, 3, 4),
}
CUSTOM = "custom"


def clean(bands) -> list[float]:
    """10 gains utilisables, quoi que contienne le réglage (retouché à la main, vieille version) :
    nombres seulement, bornés ; sinon plat."""
    if not isinstance(bands, (list, tuple)) or len(bands) != len(BANDS):
        return [0.0] * len(BANDS)
    out = []
    for g in bands:
        if isinstance(g, bool) or not isinstance(g, (int, float)) or g != g:      # g != g : NaN
            return [0.0] * len(BANDS)
        out.append(max(MIN_DB, min(MAX_DB, float(g))))
    return out


def preset_of(bands) -> str:
    """Préréglage qui correspond exactement à ces gains, sinon « custom »."""
    b = clean(bands)
    for pid, gains in PRESETS.items():
        if all(abs(x - y) < 1e-6 for x, y in zip(b, gains)):
            return pid
    return CUSTOM


def headroom(bands) -> float:
    """Baisse générale (dB) qui évite toute saturation : la plus forte hausse, 0 si aucune."""
    return max([0.0] + clean(bands))
