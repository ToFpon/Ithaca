"""Morceaux : étiquettes lues par GStreamer (GstPbutils.Discoverer), pochettes, dossiers.

Pochette : celle intégrée au fichier (écrite une fois dans ~/.cache/ithaca/covers/<empreinte>,
partagée par tout l'album), sinon une image du dossier (cover.jpg, folder.png…). Song.cover est
toujours un chemin de fichier : il sert aussi à MPRIS (mpris:artUrl).

Chargement : load_paths() parcourt fichiers et dossiers dans un fil (ordre naturel des noms, comme
Nautilus), lit les étiquettes et renvoie les morceaux par paquets dans la boucle GLib.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import GLib, Gst, GstPbutils  # noqa: E402

from ..settings import CACHE_DIR  # noqa: E402

Gst.init(None)

AUDIO_EXTS = (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav", ".wv", ".ape",
              ".mpc", ".aif", ".aiff", ".wma", ".mka", ".spx", ".tta", ".alac", ".dsf")
COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall", "thumb")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
COVERS_DIR = os.path.join(CACHE_DIR, "covers")
BATCH = 50


@dataclass
class Song:
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0          # secondes
    track: int = 0
    disc: int = 0
    cover: str = ""                # chemin d'une image, vide si aucune
    track_gain: Optional[float] = None
    album_gain: Optional[float] = None
    track_peak: Optional[float] = None
    album_peak: Optional[float] = None

    @property
    def uri(self) -> str:
        return Gst.filename_to_uri(self.path)

    @property
    def display_title(self) -> str:
        return self.title or os.path.splitext(os.path.basename(self.path))[0]

    def key(self) -> str:
        """Empreinte du fichier (chemin, taille, date) : cache de la forme d'onde."""
        try:
            st = os.stat(self.path)
            raw = f"{self.path}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            raw = self.path
        return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()


def is_audio(path: str) -> bool:
    return path.lower().endswith(AUDIO_EXTS)


def name_key(name: str):
    return GLib.utf8_collate_key_for_filename(name, -1)


def folder_cover(folder: str, _cache: dict = {}) -> str:
    """Image de pochette du dossier (cover.jpg, folder.png…), sinon une image seule du dossier."""
    if folder in _cache:
        return _cache[folder]
    found = ""
    try:
        images = [e for e in os.listdir(folder) if e.lower().endswith(IMAGE_EXTS)]
    except OSError:
        images = []
    by_stem = {os.path.splitext(e)[0].lower(): e for e in images}
    for stem in COVER_NAMES:
        if stem in by_stem:
            found = os.path.join(folder, by_stem[stem])
            break
    else:
        if len(images) == 1:
            found = os.path.join(folder, images[0])
    _cache[folder] = found
    return found


def _save_cover(sample) -> str:
    buf = sample.get_buffer()
    ok, info = buf.map(Gst.MapFlags.READ)
    if not ok:
        return ""
    try:
        data = bytes(info.data)
    finally:
        buf.unmap(info)
    if not data:
        return ""
    digest = hashlib.sha1(data).hexdigest()
    path = os.path.join(COVERS_DIR, digest)
    if not os.path.exists(path):
        try:
            os.makedirs(COVERS_DIR, exist_ok=True)
            tmp = path + f".{threading.get_ident()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError:
            return ""
    return path


def _merged_tags(info):
    tags = None
    for stream in info.get_stream_list() or []:
        t = stream.get_tags()
        if t is not None:
            tags = t if tags is None else tags.merge(t, Gst.TagMergeMode.KEEP)
    return tags


def read_song(path: str, discoverer=None) -> Optional[Song]:
    """Étiquettes d'un fichier ; None s'il n'est pas lisible comme audio."""
    d = discoverer or GstPbutils.Discoverer.new(5 * Gst.SECOND)
    try:
        info = d.discover_uri(Gst.filename_to_uri(path))
    except GLib.Error:
        return None
    if not info.get_audio_streams():
        return None
    song = Song(path)
    dur = info.get_duration()
    song.duration = dur / Gst.SECOND if dur and dur != Gst.CLOCK_TIME_NONE else 0.0
    tags = _merged_tags(info)
    if tags is not None:
        def text(tag):
            ok, value = tags.get_string(tag)
            return value.strip() if ok and value else ""

        def number(tag):
            ok, value = tags.get_uint(tag)
            return int(value) if ok else 0

        def double(tag):
            ok, value = tags.get_double(tag)
            return float(value) if ok else None
        song.title = text(Gst.TAG_TITLE)
        song.artist = text(Gst.TAG_ARTIST) or text(Gst.TAG_ALBUM_ARTIST)
        song.album = text(Gst.TAG_ALBUM)
        song.track = number(Gst.TAG_TRACK_NUMBER)
        song.disc = number(Gst.TAG_ALBUM_VOLUME_NUMBER)
        song.track_gain, song.album_gain = double(Gst.TAG_TRACK_GAIN), double(Gst.TAG_ALBUM_GAIN)
        song.track_peak, song.album_peak = double(Gst.TAG_TRACK_PEAK), double(Gst.TAG_ALBUM_PEAK)
        for tag in (Gst.TAG_IMAGE, Gst.TAG_PREVIEW_IMAGE):
            ok, sample = tags.get_sample(tag)
            if ok and sample is not None:
                song.cover = _save_cover(sample)
                if song.cover:
                    break
    if not song.cover:
        song.cover = folder_cover(os.path.dirname(path))
    return song


def expand(paths: list[str]) -> list[str]:
    """Fichiers audio des chemins donnés (dossiers parcourus, ordre naturel des noms)."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=name_key)
                for f in sorted(files, key=name_key):
                    if is_audio(f) and not f.startswith("."):
                        out.append(os.path.join(root, f))
        elif os.path.isfile(p):
            out.append(p)
    return out


def load_paths(paths: list[str], on_songs: Callable[[list[Song]], None],
               on_done: Optional[Callable[[int, int], None]] = None,
               cancel: Optional[threading.Event] = None) -> threading.Thread:
    """Lit les morceaux dans un fil ; on_songs(paquet) puis on_done(lus, ignorés) dans la boucle GLib.
    Un fichier donné explicitement mais illisible est compté comme ignoré."""
    def post(fn, *args):
        GLib.idle_add(lambda: (fn(*args), False)[1])

    def work():
        d = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        batch, read, skipped = [], 0, 0
        for path in expand(paths):
            if cancel is not None and cancel.is_set():
                return
            song = read_song(path, d)
            if song is None:
                skipped += 1
                continue
            read += 1
            batch.append(song)
            if len(batch) >= BATCH:
                post(on_songs, batch)
                batch = []
        if batch:
            post(on_songs, batch)
        if on_done:
            post(on_done, read, skipped)
    t = threading.Thread(target=work, name="ithaca-scan", daemon=True)
    t.start()
    return t
