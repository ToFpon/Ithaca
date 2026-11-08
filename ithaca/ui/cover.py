"""Pochette du morceau, et recoloration facultative de la fenêtre d'après elle (comme Amberol).

Les couleurs viennent du thème GTK, sauf si la fonctionnalité « Couleurs de la pochette » est
activée : trois couleurs tirées de la pochette teintent alors le fond de la fenêtre (dégradés
transparents posés sur le fond du thème). GTK 4.14 n'a pas les variables CSS d'Amberol
(var(--…), 4.16) : un fournisseur CSS dédié est remplacé à chaque pochette.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

TEXTURE_CACHE_SIZE = 300
_textures: "OrderedDict[tuple, Optional[Gdk.Texture]]" = OrderedDict()


def texture(path: str, size: int) -> Optional[Gdk.Texture]:
    """Texture carrée (recadrée au centre, size px de côté) d'une image, gardée en cache ; None si
    illisible. Carrée comme les pochettes d'Amberol, même pour une image rectangulaire."""
    key = (path, size)
    if key in _textures:
        _textures.move_to_end(key)
        return _textures[key]
    tex = None
    if path:
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
            w, h = pb.get_width(), pb.get_height()
            side = min(w, h)
            pb = pb.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)
            pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
            tex = Gdk.Texture.new_for_pixbuf(pb)
        except GLib.Error:
            tex = None
    _textures[key] = tex
    while len(_textures) > TEXTURE_CACHE_SIZE:
        _textures.popitem(last=False)
    return tex


class CoverView(Gtk.Stack):
    """Grande pochette ; icône de musique quand il n'y en a pas."""

    def __init__(self, size: int = 256):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["cover-view"],
                         transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.size = size
        self.placeholder = Gtk.Image(icon_name="folder-music-symbolic", pixel_size=64,
                                     css_classes=["card", "dim-label", "cover-placeholder"],
                                     width_request=size, height_request=size)
        self.add_named(self.placeholder, "none")
        # Gtk.Image à taille fixe : un Gtk.Picture prenait la taille naturelle de l'image (600 px…)
        self.picture = Gtk.Image(pixel_size=size, css_classes=["cover"])
        self.picture.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_named(self.picture, "cover")
        self.path = ""

    def add_size_setters(self, breakpoint, size: int):
        """Pochette plus petite quand la fenêtre est basse (point de rupture)."""
        breakpoint.add_setter(self.picture, "pixel-size", size)
        breakpoint.add_setter(self.placeholder, "width-request", size)
        breakpoint.add_setter(self.placeholder, "height-request", size)

    def set_cover(self, path: str):
        if path == self.path:
            return
        self.path = path
        tex = texture(path, self.size * 2) if path else None      # ×2 : écrans HiDPI
        if tex is None:
            self.set_visible_child_name("none")
        else:
            self.picture.set_from_paintable(tex)
            self.set_visible_child_name("cover")


# -- recoloration -----------------------------------------------------------------------------
def palette(path: str, count: int = 3) -> list[tuple[int, int, int]]:
    """Couleurs dominantes d'une image (image réduite, pixels groupés par teinte puis moyennés)."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 32, 32, False)
    except GLib.Error:
        return []
    data = pb.get_pixels()
    n, stride = pb.get_n_channels(), pb.get_rowstride()
    pixels = []
    for y in range(pb.get_height()):
        for x in range(pb.get_width()):
            i = y * stride + x * n
            r, g, b = data[i], data[i + 1], data[i + 2]
            if n == 4 and data[i + 3] < 128:
                continue
            pixels.append((r, g, b))
    if not pixels:
        return []
    # découpage médian : on coupe toujours la boîte la plus étendue sur son axe le plus long
    boxes = [pixels]
    while len(boxes) < count:
        boxes.sort(key=_spread, reverse=True)
        box = boxes.pop(0)
        if len(box) < 2:
            boxes.append(box)
            break
        axis = max(range(3), key=lambda a: max(p[a] for p in box) - min(p[a] for p in box))
        box.sort(key=lambda p: p[axis])
        half = len(box) // 2
        boxes += [box[:half], box[half:]]
    colors = [tuple(sum(p[a] for p in box) // len(box) for a in range(3)) for box in boxes if box]
    colors.sort(key=lambda c: -sum(c))
    while len(colors) < count:
        colors.append(colors[-1])
    return colors[:count]


def _spread(box) -> int:
    return max(max(p[a] for p in box) - min(p[a] for p in box) for a in range(3)) * len(box)


class Recolor:
    """Fournisseur CSS posé sur l'écran : fond de la fenêtre teinté d'après la pochette."""

    def __init__(self):
        self.provider = Gtk.CssProvider()
        self.installed = False
        self.colors: list = []

    def apply(self, cover_path: str):
        colors = palette(cover_path) if cover_path else []
        self.colors = colors
        if not colors:
            self.clear()
            return
        layers = []
        for angle, (r, g, b) in zip((127, 217, 336), colors):
            layers.append(f"linear-gradient({angle}deg, rgba({r},{g},{b},0.55), rgba({r},{g},{b},0) 70.71%)")
        css = (".ithaca-window.recolored, .ithaca-window.recolored .playlist-pane { background-image: "
               + ", ".join(layers) + "; }")
        self.provider.load_from_data(css, -1)
        if not self.installed:
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            self.installed = True

    def clear(self):
        self.colors = []
        if self.installed:
            Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.provider)
            self.installed = False
