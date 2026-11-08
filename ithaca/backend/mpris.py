"""MPRIS 2 (org.mpris.MediaPlayer2) : contrôle depuis le panneau de GNOME / Zorin, l'écran
verrouillé et les touches multimédia du clavier (gsd-media-keys passe par MPRIS).

Nom de bus : org.mpris.MediaPlayer2.ithaca. Les propriétés passent par les fonctions get / set de
register_object (elles marchent en Python, vérifié) ; les changements sont annoncés par le signal
PropertiesChanged, la position par Seeked (seulement lors d'un saut, comme le veut la spécification).
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from gi.repository import Gio, GLib

from .. import APP_ID, APP_NAME

BUS_NAME = "org.mpris.MediaPlayer2.ithaca"
PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"

XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/>
    <method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition"><arg direction="in" name="TrackId" type="o"/><arg direction="in" name="Position" type="x"/></method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>"""

LOOP = {"consecutive": "None", "all": "Playlist", "one": "Track"}
MIME = ["audio/mpeg", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/x-opus+ogg",
        "audio/mp4", "audio/x-m4a", "audio/aac", "audio/x-wav", "audio/x-wavpack", "audio/x-ape"]


class Mpris:
    """controller : backend.controller.Controller ; raise_cb / quit_cb : fenêtre et application."""

    def __init__(self, controller, raise_cb: Callable[[], None], quit_cb: Callable[[], None]):
        self.c = controller
        self.raise_cb = raise_cb
        self.quit_cb = quit_cb
        self.conn: Optional[Gio.DBusConnection] = None
        self._owner = 0
        self._ids: list[int] = []
        self._last: dict = {}
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)

    # -- bus ---------------------------------------------------------------------------------
    def start(self):
        if self._owner:
            return
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       self._on_bus, None, None)
        self.c.connect(self._on_event)

    def stop(self):
        self.c.disconnect(self._on_event)
        if self.conn is not None:
            for i in self._ids:
                self.conn.unregister_object(i)
        self._ids = []
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0
        self.conn = None

    def _on_bus(self, conn, _name):
        self.conn = conn
        for iface in self.node.interfaces:
            self._ids.append(conn.register_object(PATH, iface, self._on_call, self._on_get, self._on_set))
        self._last = self._player_props()

    # -- propriétés ----------------------------------------------------------------------------
    def _root_props(self) -> dict:
        return {"CanQuit": GLib.Variant("b", True), "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False), "Identity": GLib.Variant("s", APP_NAME),
                "DesktopEntry": GLib.Variant("s", APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", ["file"]),
                "SupportedMimeTypes": GLib.Variant("as", MIME)}

    def metadata(self) -> dict:
        song = self.c.song
        if song is None:
            return {"mpris:trackid": GLib.Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack")}
        track_id = "/io/github/ithaca/Ithaca/Track/" + song.key()[:16]
        md = {"mpris:trackid": GLib.Variant("o", track_id),
              "xesam:title": GLib.Variant("s", song.display_title),
              "xesam:url": GLib.Variant("s", song.uri),
              "mpris:length": GLib.Variant("x", int(song.duration * 1_000_000))}
        if song.artist:
            md["xesam:artist"] = GLib.Variant("as", [song.artist])
        if song.album:
            md["xesam:album"] = GLib.Variant("s", song.album)
        if song.track:
            md["xesam:trackNumber"] = GLib.Variant("i", song.track)
        if song.cover and os.path.exists(song.cover):
            md["mpris:artUrl"] = GLib.Variant("s", Gio.File.new_for_path(song.cover).get_uri())
        return md

    def _status(self) -> str:
        if self.c.playing:
            return "Playing"
        return "Paused" if self.c.player.song is not None else "Stopped"

    def _player_props(self) -> dict:
        from ..settings import settings
        has = len(self.c.queue) > 0
        return {"PlaybackStatus": GLib.Variant("s", self._status()),
                "LoopStatus": GLib.Variant("s", LOOP.get(self.c.queue.repeat, "None")),
                "Rate": GLib.Variant("d", 1.0), "MinimumRate": GLib.Variant("d", 1.0),
                "MaximumRate": GLib.Variant("d", 1.0),
                "Shuffle": GLib.Variant("b", self.c.queue.shuffled),
                "Metadata": GLib.Variant("a{sv}", self.metadata()),
                "Volume": GLib.Variant("d", 0.0 if settings["muted"] else float(settings["volume"])),
                "CanGoNext": GLib.Variant("b", has), "CanGoPrevious": GLib.Variant("b", has),
                "CanPlay": GLib.Variant("b", has), "CanPause": GLib.Variant("b", has),
                "CanSeek": GLib.Variant("b", self.c.song is not None),
                "CanControl": GLib.Variant("b", True)}

    def _on_get(self, _conn, _sender, _path, iface, prop):
        if iface == ROOT:
            return self._root_props().get(prop)
        if prop == "Position":
            return GLib.Variant("x", int(self.c.player.position() * 1_000_000))
        return self._player_props().get(prop)

    def _on_set(self, _conn, _sender, _path, _iface, prop, value):
        v = value.unpack()
        if prop == "Volume":
            self.c.set_muted(False)
            self.c.set_volume(max(0.0, min(1.0, float(v))))
        elif prop == "Shuffle":
            self.c.set_shuffled(bool(v))
        elif prop == "LoopStatus":
            mode = {"None": "consecutive", "Playlist": "all", "Track": "one"}.get(v)
            if mode:
                from ..settings import settings
                self.c.queue.set_repeat(mode)
                settings["repeat"] = mode
        return True

    # -- méthodes ------------------------------------------------------------------------------
    def _on_call(self, _conn, _sender, _path, iface, method, params, inv):
        args = params.unpack()
        if iface == ROOT:
            GLib.idle_add(lambda: ((self.raise_cb if method == "Raise" else self.quit_cb)(), False)[1])
        elif method == "PlayPause":
            self.c.toggle()
        elif method == "Play":
            self.c.play()
        elif method in ("Pause", "Stop"):
            self.c.pause()
        elif method == "Next":
            self.c.next()
        elif method == "Previous":
            self.c.previous()
        elif method == "Seek":
            pos = self.c.player.position() + args[0] / 1_000_000
            if pos > self.c.player.duration():
                self.c.next()
            else:
                self.c.seek(max(0.0, pos))
                self._seeked()
        elif method == "SetPosition":
            song = self.c.song
            if song is not None and args[0] == self.metadata()["mpris:trackid"].unpack():
                self.c.seek(args[1] / 1_000_000)
                self._seeked()
        elif method == "OpenUri":
            f = Gio.File.new_for_uri(args[0])
            if f.get_path():
                self.c.add_paths([f.get_path()], play=True)
        inv.return_value(None)

    # -- annonces --------------------------------------------------------------------------------
    def _seeked(self):
        if self.conn is not None:
            self.conn.emit_signal(None, PATH, PLAYER, "Seeked",
                                  GLib.Variant("(x)", (int(self.c.player.position() * 1_000_000),)))

    def _on_event(self, event, *args):
        if self.conn is None or event == "position":
            return
        props = self._player_props()
        changed = {k: v for k, v in props.items() if self._last.get(k) != v}
        self._last = props
        if changed:
            self.conn.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                  GLib.Variant("(sa{sv}as)", (PLAYER, changed, [])))
