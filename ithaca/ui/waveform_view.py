"""Barre de progression en forme d'onde (comme Amberol). Couleurs du thème : partie jouée en
couleur d'accentuation, reste en couleur du texte estompée. Clic ou glisser pour se déplacer,
← / → au clavier (5 s). Sans forme d'onde (pas encore calculée, ou fonctionnalité coupée) : une
rangée de petites barres régulières, qui sert de barre de progression ordinaire."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GObject, Graphene, Gtk  # noqa: E402

BAR = 2             # largeur d'une barre (px)
GAP = 2             # espace entre deux barres
HEIGHT = 48
FLAT = 0.08         # hauteur relative des barres sans forme d'onde
STEP = 5.0          # secondes par flèche du clavier


class WaveformView(Gtk.Widget):
    __gtype_name__ = "IthacaWaveformView"
    __gsignals__ = {"seek": (GObject.SignalFlags.RUN_FIRST, None, (float,))}

    def __init__(self):
        super().__init__(focusable=True, hexpand=True, height_request=HEIGHT,
                         css_classes=["waveform"], tooltip_text="")
        self.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        self.duration = 0.0
        self.position = 0.0
        self.peaks: list[float] = []
        self.song_path = ""                 # morceau affiché (la fenêtre ne recalcule qu'au changement)
        self._hover = -1.0              # fraction survolée, -1 : aucune
        self._drag_from = 0.0
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda g, x, y: setattr(self, "_drag_from", x))
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda c, x, y: self._set_hover(x))
        motion.connect("leave", lambda c: self._set_hover(-1))
        self.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- données ----------------------------------------------------------------------------
    def set_peaks(self, peaks):
        """Crêtes [(gauche, droite)…] entre 0 et 1 (None : aucune) ; mises à l'échelle de la plus forte."""
        if not peaks:
            self.peaks = []
        else:
            mono = [(left + right) / 2 for left, right in peaks]
            top = max(mono) or 1.0
            self.peaks = [m / top for m in mono]
        self.queue_draw()

    def set_position(self, position: float, duration: float = None):
        if duration is not None:
            self.duration = max(0.0, duration)
        self.position = max(0.0, position)
        self.update_property([Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_MAX],
                             [self.position, self.duration or 1.0])
        self.queue_draw()

    # -- dessin ----------------------------------------------------------------------------
    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 120, 256, -1, -1
        return HEIGHT, HEIGHT, -1, -1

    def _bars(self, width: int) -> list[float]:
        n = max(1, (width + GAP) // (BAR + GAP))
        if not self.peaks:
            return [FLAT] * n
        out = []
        per = len(self.peaks) / n
        for i in range(n):
            a, b = int(i * per), max(int((i + 1) * per), int(i * per) + 1)
            chunk = self.peaks[a:min(b, len(self.peaks))] or [0.0]
            out.append(max(FLAT, sum(chunk) / len(chunk)))
        return out

    def _colors(self):
        fg = self.get_color()
        ok, accent = self.get_style_context().lookup_color("accent_color")
        if not ok:
            accent = fg
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = fg.red, fg.green, fg.blue, fg.alpha * 0.25
        hover = Gdk.RGBA()
        hover.red, hover.green, hover.blue, hover.alpha = accent.red, accent.green, accent.blue, 0.55
        return accent, dim, hover

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        played, rest, hover = self._colors()
        frac = self.position / self.duration if self.duration > 0 else 0.0
        bars = self._bars(width)
        x = 0
        for value in bars:
            h = max(2.0, value * height)
            rect = Graphene.Rect().init(x, (height - h) / 2, BAR, h)
            center = (x + BAR / 2) / width
            if center <= frac:
                color = played
            elif self._hover >= 0 and center <= self._hover:
                color = hover
            else:
                color = rest
            snapshot.append_color(color, rect)
            x += BAR + GAP

    # -- souris et clavier -------------------------------------------------------------------
    def _set_hover(self, x: float):
        width = self.get_width()
        self._hover = -1.0 if x < 0 or width <= 0 else max(0.0, min(1.0, x / width))
        if self._hover >= 0 and self.duration > 0:
            from ..format import duration
            self.set_tooltip_text(duration(self._hover * self.duration))
        self.queue_draw()

    def _seek_to(self, x: float):
        width = self.get_width()
        if width > 0 and self.duration > 0:
            seconds = max(0.0, min(1.0, x / width)) * self.duration
            self.set_position(seconds)
            self.emit("seek", seconds)

    def _on_click(self, gesture, n_press, x, y):
        self.grab_focus()
        self._seek_to(x)

    def _on_drag_update(self, gesture, dx, dy):
        self._set_hover(self._drag_from + dx)

    def _on_drag_end(self, gesture, dx, dy):
        if abs(dx) > 2:
            self._seek_to(self._drag_from + dx)

    def _on_key(self, _ctrl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and self.duration > 0:
            delta = STEP if keyval == Gdk.KEY_Right else -STEP
            target = max(0.0, min(self.duration, self.position + delta))
            self.set_position(target)
            self.emit("seek", target)
            return True
        return False
