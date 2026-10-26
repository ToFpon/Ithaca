"""Tailles, vitesses et durées affichées (unités décimales, comme GNOME)."""

from __future__ import annotations

from .i18n import decimal, tr

UNITS = [tr("o"), tr("Ko"), tr("Mo"), tr("Go"), tr("To")]


def size(n: float) -> str:
    n = float(max(0, n))
    i = 0
    while n >= 1000 and i < len(UNITS) - 1:
        n /= 1000
        i += 1
    if i == 0:
        return f"{int(n)} {UNITS[0]}"
    return f"{decimal(n, 1 if n < 100 else 0)} {UNITS[i]}"


def speed(n: float) -> str:
    return tr("{0}/s").format(size(n))


def duration(seconds: float) -> str:
    """« 45 s », « 3 min 20 s », « 1 h 05 min », « 2 j 4 h »."""
    s = int(round(max(0, seconds)))
    if s < 60:
        return tr("{0} s").format(s)
    m, s = divmod(s, 60)
    if m < 60:
        return tr("{0} min {1} s").format(m, f"{s:02d}") if m < 10 else tr("{0} min").format(m)
    h, m = divmod(m, 60)
    if h < 24:
        return tr("{0} h {1} min").format(h, f"{m:02d}")
    d, h = divmod(h, 24)
    return tr("{0} j {1} h").format(d, h)
