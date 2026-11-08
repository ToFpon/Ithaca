"""Forme d'onde d'un morceau, comme Amberol : le fichier est décodé aussi vite que possible
(uridecodebin ! level, sans synchronisation sur l'horloge), une crête par quart de seconde pour
chaque canal ; le résultat est gardé dans ~/.cache/ithaca/waveforms/<empreinte>.json (chemin,
taille et date du fichier : un fichier modifié est recalculé).

Dans la boucle GLib : GStreamer décode dans ses fils, les messages du bus arrivent ici.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from .song import Song  # noqa: E402
from ..settings import CACHE_DIR  # noqa: E402

WAVEFORM_DIR = os.path.join(CACHE_DIR, "waveforms")
INTERVAL_NS = 250_000_000
PIPELINE = ("uridecodebin name=src ! audioconvert ! audio/x-raw,channels=2 ! "
            f"level name=level interval={INTERVAL_NS} post-messages=true ! fakesink name=sink sync=false qos=false")


def cache_path(song: Song) -> str:
    return os.path.join(WAVEFORM_DIR, song.key() + ".json")


def db_to_linear(db: float) -> float:
    return 0.0 if db <= -100 else min(1.0, pow(10.0, db / 20.0))


class WaveformGenerator:
    """Un calcul à la fois : demander un autre morceau abandonne le précédent."""

    def __init__(self):
        self.pipeline = None
        self.song: Optional[Song] = None
        self.peaks: list[tuple[float, float]] = []
        self.callback: Optional[Callable] = None

    def request(self, song: Song, callback: Callable[[Song, Optional[list]], None]):
        """callback(song, crêtes [(gauche, droite)…] entre 0 et 1, ou None si impossible)."""
        self.cancel()
        try:
            with open(cache_path(song), encoding="utf-8") as fh:
                peaks = [tuple(p) for p in json.load(fh)]
            callback(song, peaks)
            return
        except (OSError, ValueError, TypeError):
            pass
        try:
            pipeline = Gst.parse_launch(PIPELINE)
        except GLib.Error:
            callback(song, None)
            return
        pipeline.get_by_name("src").set_property("uri", song.uri)
        self.pipeline, self.song, self.peaks, self.callback = pipeline, song, [], callback
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message, pipeline)
        pipeline.set_state(Gst.State.PLAYING)

    def cancel(self):
        if self.pipeline is not None:
            self._stop(self.pipeline)
        self.pipeline = self.song = self.callback = None
        self.peaks = []

    def _stop(self, pipeline):
        pipeline.set_state(Gst.State.NULL)
        bus = pipeline.get_bus()
        bus.remove_signal_watch()

    def _on_message(self, _bus, msg, pipeline):
        if pipeline is not self.pipeline:
            return
        t = msg.type
        if t == Gst.MessageType.ELEMENT:
            s = msg.get_structure()
            if s is not None and s.get_name() == "level":
                peak = s.get_value("peak")
                if peak:
                    left = db_to_linear(peak[0])
                    right = db_to_linear(peak[1] if len(peak) > 1 else peak[0])
                    self.peaks.append((round(left, 4), round(right, 4)))
        elif t in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            song, peaks, callback = self.song, self.peaks, self.callback
            self._stop(pipeline)
            self.pipeline = self.song = self.callback = None
            ok = t == Gst.MessageType.EOS and peaks
            if ok:
                try:
                    os.makedirs(WAVEFORM_DIR, exist_ok=True)
                    tmp = cache_path(song) + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(peaks, fh)
                    os.replace(tmp, cache_path(song))
                except OSError:
                    pass
            if callback:
                callback(song, peaks if ok else None)

