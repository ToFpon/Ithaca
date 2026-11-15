"""Fenêtre de l'égaliseur (demande de Tof) : interrupteur (= fonctionnalité « equalizer »), préréglages,
10 curseurs verticaux de -12 à +12 dB. Tout s'applique en direct (Controller._apply_equalizer, sur le
réglage eq_bands) ; toucher un curseur fait passer le préréglage en « Personnalisé »."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..backend.equalizer import BANDS, CUSTOM, MAX_DB, MIN_DB, PRESETS, clean, preset_of  # noqa: E402
from ..i18n import tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import DialogWindow  # noqa: E402

SAVE_DELAY_MS = 250          # glisser un curseur : un seul enregistrement à la fin, pas un par pixel


def preset_names() -> dict[str, str]:
    return {"flat": tr("Plat"), "rock": tr("Rock"), "pop": tr("Pop"), "jazz": tr("Jazz"),
            "classical": tr("Classique"), "electronic": tr("Électro"), "bass": tr("Graves renforcés"),
            "treble": tr("Aigus renforcés"), "vocal": tr("Voix"), "loudness": tr("Loudness"),
            CUSTOM: tr("Personnalisé")}


def band_label(hz: int) -> str:
    return f"{hz / 1000:.1f}k".replace(".0k", "k") if hz >= 1000 else str(hz)


class EqualizerWindow(DialogWindow):
    def __init__(self):
        super().__init__(title=tr("Égaliseur"), content_width=600, content_height=440)
        self.set_modal(False)                        # régler en écoutant : le lecteur reste utilisable
        self._syncing = False
        self._save_timer = 0
        self.ids = list(PRESETS) + [CUSTOM]
        names = preset_names()

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text=tr("Activer l'égaliseur"))
        self.switch.connect("notify::active", self._on_switch)
        header.pack_start(self.switch)
        tv.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=18,
                       margin_start=18, margin_end=18)
        top = Gtk.Box(spacing=12)
        top.append(Gtk.Label(label=tr("Préréglage"), css_classes=["heading"]))
        self.presets = Gtk.DropDown.new_from_strings([names[i] for i in self.ids])
        self.presets.connect("notify::selected", self._on_preset)
        top.append(self.presets)
        reset = Gtk.Button(label=tr("Réinitialiser"), hexpand=True, halign=Gtk.Align.END)
        reset.connect("clicked", lambda *_: self.apply_preset("flat"))
        top.append(reset)
        body.append(top)

        self.sliders_box = Gtk.Box(spacing=6, homogeneous=True, vexpand=True)
        self.scales: list[Gtk.Scale] = []
        self.values: list[Gtk.Label] = []
        for i, hz in enumerate(BANDS):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            value = Gtk.Label(css_classes=["caption", "numeric"])
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, MIN_DB, MAX_DB, 0.5)
            scale.set_inverted(True)                 # +12 en haut
            scale.set_draw_value(False)
            scale.set_has_origin(False)              # pas de remplissage depuis -12 : le zéro est au milieu
            scale.set_vexpand(True)
            scale.set_halign(Gtk.Align.CENTER)
            scale.add_mark(0.0, Gtk.PositionType.RIGHT, None)
            scale.set_tooltip_text(f"{band_label(hz)} Hz")
            scale.connect("value-changed", self._on_band, i)
            col.append(value)
            col.append(scale)
            col.append(Gtk.Label(label=band_label(hz), css_classes=["caption", "dim-label", "numeric"]))
            self.sliders_box.append(col)
            self.scales.append(scale)
            self.values.append(value)
        body.append(self.sliders_box)
        tv.set_content(body)
        self.set_content(tv)

        self._load()
        self._listener = self._on_setting
        settings.connect(self._listener)
        self.connect("closed", self._on_closed)

    # -- état --------------------------------------------------------------------------------
    def bands(self) -> list[float]:
        return [round(s.get_value() * 2) / 2 for s in self.scales]

    def _load(self):
        self._syncing = True
        try:
            on = features.enabled("equalizer")
            self.switch.set_active(on)
            for scale, gain in zip(self.scales, clean(settings["eq_bands"])):
                scale.set_value(gain)
            self._sync_labels()
            self.presets.set_selected(self.ids.index(preset_of(self.bands())))
            self.sliders_box.set_sensitive(on)
            self.presets.set_sensitive(on)
        finally:
            self._syncing = False

    def _sync_labels(self):
        for label, gain in zip(self.values, self.bands()):
            label.set_label("0" if gain == 0 else f"{gain:+.0f}" if gain == int(gain) else f"{gain:+.1f}")

    def apply_preset(self, pid: str):
        if pid not in PRESETS:
            return
        self._syncing = True
        try:
            for scale, gain in zip(self.scales, PRESETS[pid]):
                scale.set_value(gain)
            self.presets.set_selected(self.ids.index(pid))
            self._sync_labels()
        finally:
            self._syncing = False
        self._save_now()

    # -- réactions ---------------------------------------------------------------------------
    def _on_switch(self, switch, _pspec):
        if self._syncing:
            return
        features.set_enabled("equalizer", switch.get_active())

    def _on_preset(self, dropdown, _pspec):
        if not self._syncing:
            self.apply_preset(self.ids[dropdown.get_selected()])     # « Personnalisé » : rien à faire

    def _on_band(self, _scale, _index):
        if self._syncing:
            return
        self._sync_labels()
        self._syncing = True
        try:
            self.presets.set_selected(self.ids.index(preset_of(self.bands())))
        finally:
            self._syncing = False
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(SAVE_DELAY_MS, self._save_timeout)

    def _save_timeout(self):
        self._save_timer = 0
        self._save_now()
        return False

    def _save_now(self):
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
        bands = self.bands()
        settings["eq_bands"] = [] if all(g == 0 for g in bands) else bands

    def _on_setting(self, key, _value):
        if key == "features":                        # coupé / activé ailleurs (Préférences)
            self._load()

    def _on_closed(self, *_):
        if self._save_timer:
            self._save_now()
        settings.disconnect(self._listener)
