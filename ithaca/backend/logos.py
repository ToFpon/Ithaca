"""Logos des webradios (demande de Tof : une webradio n'avait que l'icône de musique).

Sources, dans l'ordre :
- le logo donné par la liste : `tvg-logo="…"` des #EXTINF (.m3u), `<image>` (.xspf) ;
- sinon le site de la station, s'il est annoncé par le flux (étiquette ICY « icy-url ») : sa grande
  icône (`apple-touch-icon`, `icon` de la page), à défaut `/favicon.ico`.

Téléchargé dans un fil (un seul à la fois, file d'attente), jamais plus de MAX_BYTES, contrôlé
(une vraie image, d'au moins MIN_SIZE px), puis posé au centre d'un carré opaque (`squared_png`) —
la pochette est recadrée en carré, un logo large aurait été rogné — et gardé en PNG dans
~/.cache/ithaca/logos/<sha1 de l'adresse>.png : Song.cover reste un chemin (MPRIS compris), et un
logo déjà vu revient sans réseau.
"""

from __future__ import annotations

import hashlib
import html.parser
import os
import queue
import threading
import urllib.parse
import urllib.request
from typing import Callable, Optional

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from .. import VERSION  # noqa: E402
from ..settings import CACHE_DIR  # noqa: E402

LOGOS_DIR = os.path.join(CACHE_DIR, "logos")
MAX_BYTES = 3_000_000          # logo ou page d'accueil : au-delà, on abandonne
TIMEOUT = 10                   # secondes
MIN_SIZE = 32                  # logo donné par la liste
MIN_SITE_SIZE = 48             # icône du site : un favicon de 16 px serait flou en grand
MAX_SIDE = 512                 # logo gardé en 512 px au plus (affiché en 256)
USER_AGENT = f"Ithaca/{VERSION} (music player)"


def cache_path(url: str) -> str:
    return os.path.join(LOGOS_DIR, hashlib.sha1(url.encode("utf-8", "surrogateescape")).hexdigest() + ".png")


def cached(url: str) -> str:
    """Logo déjà téléchargé pour cette adresse, sinon ""."""
    if not url:
        return ""
    path = cache_path(url)
    return path if os.path.isfile(path) else ""


# -- réseau (dans le fil) ---------------------------------------------------------------------
def _get(url: str) -> tuple[bytes, str]:
    """Contenu (MAX_BYTES au plus) et adresse finale (après redirections)."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("adresse non HTTP")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("trop gros")
        return data, resp.geturl()


def _border(pix) -> tuple[float, tuple[int, int, int]]:
    """Part transparente du tour de l'image, et couleur moyenne de sa partie opaque."""
    w, h, n, stride = pix.get_width(), pix.get_height(), pix.get_n_channels(), pix.get_rowstride()
    data = pix.get_pixels()
    points = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    clear, total, rgb = 0, 0, [0, 0, 0]
    for x, y in points:
        i = y * stride + x * n
        if n == 4 and data[i + 3] < 200:
            clear += 1
        else:
            total += 1
            for c in range(3):
                rgb[c] += data[i + c]
    color = tuple(v // total for v in rgb) if total else (255, 255, 255)
    return clear / max(len(points), 1), color


def squared_png(data: bytes, min_size: int) -> Optional[bytes]:
    """Image contrôlée, réduite à MAX_SIDE au plus et rendue carrée sans être rognée (la pochette est
    recadrée en carré) : posée au centre d'un fond opaque — la couleur de son propre bord, ou blanc
    pour un logo transparent (souvent du texte sombre, illisible sur un thème sombre). None si ce
    n'est pas une image ou si elle est trop petite."""
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    pix = loader.get_pixbuf()
    if pix is None:
        return None
    w, h = pix.get_width(), pix.get_height()
    if max(w, h) < min_size:
        return None
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1.0:
        w, h = max(1, round(w * scale)), max(1, round(h * scale))
        pix = pix.scale_simple(w, h, GdkPixbuf.InterpType.HYPER)
    clear, (r, g, b) = _border(pix)
    transparent = clear > 0.5
    if transparent:
        r = g = b = 255
    side = max(w, h)
    if transparent or w != h:
        side = round(side * 1.15)                    # un peu d'air autour du logo
    square = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, side, side)
    square.fill((r << 24) | (g << 16) | (b << 8) | 0xff)
    pix.composite(square, (side - w) // 2, (side - h) // 2, w, h, (side - w) // 2, (side - h) // 2,
                  1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
    ok, buf = square.save_to_bufferv("png", [], [])
    return bytes(buf) if ok else None


def _store(url: str, png: bytes) -> str:
    os.makedirs(LOGOS_DIR, exist_ok=True)
    path = cache_path(url)
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)
    return path


class _IconLinks(html.parser.HTMLParser):
    """<link rel="apple-touch-icon" / "icon" href=… sizes=…> d'une page d'accueil."""

    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []        # (préférence, adresse)

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        href = a.get("href", "").strip()
        if not href:
            return
        size = 0
        for part in a.get("sizes", "").lower().split():
            w, _, _h = part.partition("x")
            if w.isdigit():
                size = max(size, int(w))
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            self.icons.append((1000 + (size or 180), href))
        elif "icon" in rel:
            self.icons.append((size or (300 if href.lower().endswith(".svg") else 32), href))


def site_icon_urls(page: bytes, base: str) -> list[str]:
    """Icônes déclarées par la page (les plus grandes d'abord), puis /favicon.ico."""
    parser = _IconLinks()
    try:
        parser.feed(page[:500_000].decode("utf-8", "replace"))
    except Exception:
        pass
    urls = [urllib.parse.urljoin(base, href) for _pref, href in sorted(parser.icons, key=lambda x: -x[0])]
    root = urllib.parse.urlsplit(base)
    urls.append(f"{root.scheme}://{root.netloc}/favicon.ico")
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_logo(url: str) -> str:
    """Logo donné par une liste : chemin en cache, "" si introuvable ou pas une image."""
    if cached(url):
        return cached(url)
    try:
        data, _final = _get(url)
    except Exception:
        return ""
    png = squared_png(data, MIN_SIZE)
    return _store(url, png) if png else ""


def fetch_site_icon(homepage: str) -> str:
    """Icône du site d'une station (« icy-url ») : chemin en cache (clé = le site), sinon ""."""
    if cached(homepage):
        return cached(homepage)
    if "://" not in homepage:
        homepage = "http://" + homepage
    try:
        page, final = _get(homepage)
    except Exception:
        page, final = b"", homepage
    for icon in site_icon_urls(page, final)[:4]:
        try:
            data, _ = _get(icon)
        except Exception:
            continue
        png = squared_png(data, MIN_SITE_SIZE)
        if png:
            return _store(homepage, png)
    return ""


# -- file d'attente ----------------------------------------------------------------------------
class Fetcher:
    """Un fil, une demande à la fois ; la réponse arrive dans la boucle GLib (done(chemin))."""

    def __init__(self):
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._pending: set[tuple[str, str]] = set()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def request(self, kind: str, url: str, done: Callable[[str], None],
                failed: Optional[Callable[[], None]] = None):
        """kind : « logo » (adresse d'une image) ou « site » (page d'accueil de la station).
        done(chemin) ou failed() sont appelés dans la boucle GLib."""
        key = (kind, url)
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            self._queue.put((kind, url, done, failed))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="ithaca-logos", daemon=True)
                self._thread.start()

    def busy(self, kind: str, url: str) -> bool:
        """Demande encore en attente ou en cours."""
        with self._lock:
            return (kind, url) in self._pending

    def _work(self):
        while True:
            try:
                kind, url, done, failed = self._queue.get(timeout=5)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            path = fetch_logo(url) if kind == "logo" else fetch_site_icon(url)
            with self._lock:
                self._pending.discard((kind, url))
            if path:
                GLib.idle_add(lambda p=path, d=done: (d(p), False)[1])
            elif failed is not None:
                GLib.idle_add(lambda f=failed: (f(), False)[1])


fetcher = Fetcher()
