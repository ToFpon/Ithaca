"""Lecture avec GStreamer (playbin), dans la boucle GLib.

- Audio seulement : le drapeau vidéo de playbin est coupé (sinon une pochette intégrée à un MP3,
  vue comme un flux vidéo, ouvrait une fenêtre).
- Sans blanc entre deux morceaux : au signal « about-to-finish » (fil de GStreamer), l'adresse du
  suivant, préparée d'avance dans la boucle GLib, est donnée à playbin ; le changement effectif
  arrive par le message « stream-start ».
- ReplayGain : un élément « volume » en audio-filter, réglé par nous d'après les étiquettes du
  morceau (album, morceau ou rien), en direct, sans interrompre la lecture ; crête respectée.
- Sortie : ITHACA_AUDIO_SINK (« fakesink sync=true » dans les tests), sinon celle du système.
"""

from __future__ import annotations

import math
import os
from typing import Callable, Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstAudio", "1.0")
from gi.repository import GLib, Gst, GstAudio  # noqa: E402

from .song import Song  # noqa: E402

Gst.init(None)

PLAY_FLAG_AUDIO = 1 << 1
PLAY_FLAG_SOFT_VOLUME = 1 << 4
TICK_MS = 200
PREAMP_DB = 0.0


def replaygain_factor(song: Optional[Song], mode: str) -> float:
    """Facteur de volume ReplayGain (1.0 : rien) ; repli album ↔ morceau quand l'un manque, et la
    crête n'est jamais dépassée (pas de saturation)."""
    if song is None or mode not in ("album", "track"):
        return 1.0
    if mode == "album":
        gain = song.album_gain if song.album_gain is not None else song.track_gain
        peak = song.album_peak if song.album_peak is not None else song.track_peak
    else:
        gain = song.track_gain if song.track_gain is not None else song.album_gain
        peak = song.track_peak if song.track_peak is not None else song.album_peak
    if gain is None:
        return 1.0
    factor = math.pow(10.0, (gain + PREAMP_DB) / 20.0)
    if peak and peak > 0:
        factor = min(factor, 1.0 / peak)
    return max(0.0, min(factor, 10.0))


class Player:
    """Callbacks (boucle GLib) : on_state(playing), on_position(secondes), on_song_started(song),
    on_ended() (fin de la file), on_error(song, message), on_need_next() → Song suivant ou None."""

    def __init__(self):
        self.playbin = Gst.ElementFactory.make("playbin", "ithaca-player")
        self.playbin.set_property("flags", PLAY_FLAG_AUDIO | PLAY_FLAG_SOFT_VOLUME)
        sink = os.environ.get("ITHACA_AUDIO_SINK")
        if sink:
            self.playbin.set_property("audio-sink", Gst.parse_bin_from_description(sink, True))
        self.rg = Gst.ElementFactory.make("volume", "replaygain")
        self.playbin.set_property("audio-filter", self.rg)
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)
        self.playbin.connect("about-to-finish", self._on_about_to_finish)
        self.song: Optional[Song] = None
        self.next_song: Optional[Song] = None       # préparé pour l'enchaînement sans blanc
        self._pending: Optional[Song] = None        # donné à playbin, pas encore commencé
        self.playing = False
        self.replaygain = "album"
        self._tick = 0
        self._seek_after_load: Optional[float] = None
        self.on_state: Callable[[bool], None] = lambda playing: None
        self.on_position: Callable[[float], None] = lambda pos: None
        self.on_song_started: Callable[[Song], None] = lambda song: None
        self.on_ended: Callable[[], None] = lambda: None
        self.on_error: Callable[[Optional[Song], str], None] = lambda song, msg: None
        self.on_stream_title: Callable[[Song], None] = lambda song: None

    # -- commandes ------------------------------------------------------------------------
    def load(self, song: Song, play: bool = True, position: float = 0.0):
        self.playbin.set_state(Gst.State.NULL)
        self.song = song
        self._pending = None
        self.next_song = None
        self._apply_replaygain()
        self.playbin.set_property("uri", song.uri)
        self._seek_after_load = position if position > 0 else None
        self.playbin.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)
        self._set_playing(play)

    def play(self):
        if self.song is None:
            return
        self.playbin.set_state(Gst.State.PLAYING)
        self._set_playing(True)

    def pause(self):
        self.playbin.set_state(Gst.State.PAUSED)
        self._set_playing(False)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def stop(self):
        self.playbin.set_state(Gst.State.NULL)
        self.song = None
        self.next_song = self._pending = None
        self._set_playing(False)
        self.on_position(0.0)

    def seek(self, seconds: float):
        if self.song is None:
            return
        seconds = max(0.0, seconds)
        self.playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                 int(seconds * Gst.SECOND))
        self.on_position(seconds)

    def position(self) -> float:
        ok, pos = self.playbin.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok and pos >= 0 else 0.0

    def duration(self) -> float:
        ok, dur = self.playbin.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            return dur / Gst.SECOND
        return self.song.duration if self.song else 0.0

    def set_volume(self, cubic: float, muted: bool = False):
        """Volume du curseur (échelle cubique, comme GNOME) → linéaire pour playbin."""
        linear = GstAudio.StreamVolume.convert_volume(GstAudio.StreamVolumeFormat.CUBIC,
                                                      GstAudio.StreamVolumeFormat.LINEAR,
                                                      max(0.0, min(1.0, cubic)))
        self.playbin.set_property("volume", linear)
        self.playbin.set_property("mute", muted)

    def set_replaygain(self, mode: str):
        self.replaygain = mode
        self._apply_replaygain()

    def _apply_replaygain(self):
        self.rg.set_property("volume", replaygain_factor(self.song, self.replaygain))

    def shutdown(self):
        self.playbin.set_state(Gst.State.NULL)
        self.playbin.get_bus().remove_signal_watch()
        self._stop_tick()

    # -- état -------------------------------------------------------------------------------
    def _set_playing(self, value: bool):
        if value and not self._tick:
            self._tick = GLib.timeout_add(TICK_MS, self._on_tick)
        elif not value:
            self._stop_tick()
        if value != self.playing:
            self.playing = value
            self.on_state(value)

    def _stop_tick(self):
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _on_tick(self):
        self.on_position(self.position())
        return True

    # -- GStreamer --------------------------------------------------------------------------
    def _on_about_to_finish(self, playbin):
        # fil de GStreamer : seulement lire next_song (préparé dans la boucle GLib) et le donner
        nxt = self.next_song
        if nxt is not None:
            self._pending = nxt
            playbin.set_property("uri", nxt.uri)

    def _on_message(self, _bus, msg):
        t = msg.type
        if t == Gst.MessageType.STREAM_START:
            if self._pending is not None:              # enchaînement sans blanc : le suivant commence
                self.song, self._pending, self.next_song = self._pending, None, None
                self._apply_replaygain()
                self.on_song_started(self.song)
        elif t == Gst.MessageType.TAG and self.song is not None and self.song.stream:
            # webradio : la station annonce le morceau en cours (étiquette ICY « title »)
            ok, title = msg.parse_tag().get_string(Gst.TAG_TITLE)
            if ok and title and title.strip() != self.song.now_playing:
                self.song.now_playing = title.strip()
                self.on_stream_title(self.song)
        elif t == Gst.MessageType.ASYNC_DONE:
            if self._seek_after_load is not None:
                pos, self._seek_after_load = self._seek_after_load, None
                self.seek(pos)
        elif t == Gst.MessageType.EOS:
            self.playbin.set_state(Gst.State.NULL)
            self._set_playing(False)
            self.on_ended()
        elif t == Gst.MessageType.ERROR:
            err, _debug = msg.parse_error()
            song = self._pending or self.song
            self.playbin.set_state(Gst.State.NULL)
            self._pending = None
            self._set_playing(False)
            self.on_error(song, err.message)
