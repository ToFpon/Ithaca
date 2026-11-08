"""Texte défilant : un Gtk.Label affiché en entier s'il tient, sinon il glisse lentement jusqu'à sa
fin, s'arrête, puis revient (pause de chaque côté), avec un fondu sur les bords. Sans animations
(Paramètres → Accessibilité), il est tronqué par « … » comme avant."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

SPEED = 30.0          # pixels par seconde
PAUSE = 2.0           # secondes d'arrêt au début et à la fin
FADE = 18.0           # largeur du fondu sur les bords (pixels)


def _stop(offset: float, alpha: float) -> Gsk.ColorStop:
    stop = Gsk.ColorStop()
    stop.offset = offset
    color = Gdk.RGBA()
    color.red = color.green = color.blue = 0.0
    color.alpha = alpha
    stop.color = color
    return stop


def scroll_offset(elapsed: float, overflow: float) -> float:
    """Décalage (pixels) à l'instant `elapsed` : pause, aller, pause, retour, et on recommence."""
    if overflow <= 0:
        return 0.0
    travel = overflow / SPEED
    t = elapsed % (2 * PAUSE + 2 * travel)
    if t < PAUSE:
        return 0.0
    t -= PAUSE
    if t < travel:
        return t * SPEED
    t -= travel
    if t < PAUSE:
        return overflow
    t -= PAUSE
    return max(overflow - t * SPEED, 0.0)


class Marquee(Gtk.Widget):
    __gtype_name__ = "IthacaMarquee"

    def __init__(self, css_classes=(), max_natural: int = 420):
        super().__init__(overflow=Gtk.Overflow.HIDDEN)
        self.label = Gtk.Label(css_classes=list(css_classes), single_line_mode=True)
        self.label.set_parent(self)
        self.max_natural = max_natural
        self.offset = 0.0
        self._tick = None
        self._start = None
        self._animate = Gtk.Settings.get_default().get_property("gtk-enable-animations")
        self.label.set_ellipsize(Pango.EllipsizeMode.NONE if self._animate else Pango.EllipsizeMode.END)
        self.connect("unmap", lambda *_: self._stop_ticking())

    # même usage qu'un Gtk.Label
    def set_label(self, text: str):
        if text != self.label.get_label():
            self.label.set_label(text)
            self._start = None
            self.offset = 0.0
            self.queue_allocate()

    def get_label(self) -> str:
        return self.label.get_label()

    def do_dispose(self):
        self._stop_ticking()
        if self.label is not None:
            self.label.unparent()
            self.label = None
        Gtk.Widget.do_dispose(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, for_size):
        minimum, natural, base_min, base_nat = self.label.measure(orientation, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            # aucune largeur minimale : un titre long ne doit jamais élargir la fenêtre
            return 0 if self._animate else minimum, min(natural, self.max_natural), -1, -1
        return minimum, natural, base_min, base_nat

    def overflow(self) -> float:
        if not self._animate:
            return 0.0
        natural = self.label.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        return max(natural - self.get_width(), 0)

    def do_size_allocate(self, width, height, baseline):
        over = self.overflow()
        if over > 0:
            natural = self.label.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            self.label.allocate(natural, height, baseline, None)
            self._start_ticking()
        else:
            self.label.allocate(width, height, baseline, None)      # centré par le Gtk.Label
            self._stop_ticking()
            self.offset = 0.0

    def _start_ticking(self):
        if self._tick is None and self.get_mapped():
            self._tick = self.add_tick_callback(self._on_tick)

    def _stop_ticking(self):
        if self._tick is not None:
            self.remove_tick_callback(self._tick)
            self._tick = None
        self._start = None

    def _on_tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        if self._start is None:
            self._start = now
        offset = scroll_offset(now - self._start, self.overflow())
        if offset != self.offset:
            self.offset = offset
            self.queue_draw()
        return True             # rappelé à chaque image (répétition voulue)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        over = self.overflow()
        if over <= 0:
            self.snapshot_child(self.label, snapshot)
            return
        # fondu : à gauche dès que le texte a commencé à glisser, à droite tant qu'il reste du texte
        left = FADE / width if self.offset > 0.5 else 0.0
        right = FADE / width if self.offset < over - 0.5 else 0.0
        bounds = Graphene.Rect().init(0, 0, width, height)
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(bounds, Graphene.Point().init(0, 0), Graphene.Point().init(width, 0),
                                        [_stop(0.0, 0.0 if left else 1.0), _stop(left, 1.0),
                                         _stop(1.0 - right, 1.0), _stop(1.0, 0.0 if right else 1.0)])
        snapshot.pop()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(-self.offset, 0))
        self.snapshot_child(self.label, snapshot)
        snapshot.restore()
        snapshot.pop()
