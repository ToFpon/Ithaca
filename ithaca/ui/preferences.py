"""Préférences (Adw.PreferencesWindow : vraie fenêtre, le thème du système s'y applique)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..i18n import LANGUAGE_NAMES, ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import combo_row  # noqa: E402


def apply_theme():
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(settings["theme"], Adw.ColorScheme.DEFAULT))


def _choice(title: str, key: str, labels: list[str], values: list, subtitle: str = "") -> Adw.ComboRow:
    cur = settings[key]
    row = combo_row(title, labels, values.index(cur) if cur in values else 0, subtitle)
    row.connect("notify::selected", lambda r, *_: settings.__setitem__(key, values[r.get_selected()]))
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self):
        super().__init__(title=tr("Préférences"), search_enabled=False, default_width=620, default_height=620)
        self.add(self._general())
        self.add(self._features())

    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Général"), icon_name="preferences-system-symbolic", name="general")
        look = Adw.PreferencesGroup(title=tr("Apparence"))
        theme = _choice(tr("Style"), "theme", [tr("Comme le système"), tr("Clair"), tr("Sombre")],
                        ["system", "light", "dark"])
        theme.connect("notify::selected", lambda *_: apply_theme())
        look.add(theme)
        codes = list(LANGUAGE_NAMES)
        look.add(_choice(tr("Langue"), "language", [tr("Comme le système")] + [LANGUAGE_NAMES[c] for c in codes[1:]],
                         codes, tr("Appliquée au prochain démarrage")))
        # gauchers / droitiers (idée de Tof, repris de Nausicaa) ; appliqué tout de suite
        look.add(_choice(tr("File de lecture"), "playlist_side", [tr("À gauche"), tr("À droite")],
                         ["start", "end"], tr("Côté de la fenêtre où s'ouvre la file (F9)")))
        page.add(look)
        play = Adw.PreferencesGroup(title=tr("Lecture"))
        play.add(_choice(tr("ReplayGain"), "replaygain", [tr("Album"), tr("Morceau"), tr("Désactivé")],
                         ["album", "track", "off"],
                         tr("Volume homogène d'un morceau à l'autre, d'après les étiquettes des fichiers")))
        # repris de Nausicaa (demande de Tof)
        play.add(_choice(tr("Volume maximal"), "volume_max", ["100 %", "150 %"], [100, 150],
                         tr("Au-delà de 100 %, le son est amplifié (comme VLC) : utile pour un "
                            "enregistrement trop faible")))
        play.add(_choice(tr("Saut avec ← / →"), "seek_step",
                         [ntr("{0} seconde", "{0} secondes", n).format(n) for n in (5, 10, 30)], [5, 10, 30],
                         tr("Maj+← / → dans la fenêtre, ← / → sur la barre en forme d'onde")))
        play.add(_choice(tr("Cran de molette"), "wheel_step", ["2 %", "5 %", "10 %"], [2, 5, 10],
                         tr("Volume ajouté ou retiré à chaque cran de la molette")))
        page.add(play)
        return page

    def _features(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Fonctionnalités"), icon_name="application-x-addon-symbolic",
                                   name="features")
        group = Adw.PreferencesGroup(description=tr("Chaque fonctionnalité s'active ou se coupe tout de "
                                                    "suite, sans redémarrer Ithaca"))
        self.feature_rows = {}
        for f in features.FEATURES:
            row = Adw.SwitchRow(title=f.title, subtitle=f.description, active=features.enabled(f.id))
            row.add_prefix(Gtk.Image(icon_name=f.icon))
            row.connect("notify::active", lambda r, *_, fid=f.id: features.set_enabled(fid, r.get_active()))
            self.feature_rows[f.id] = row
            group.add(row)
        page.add(group)
        return page
