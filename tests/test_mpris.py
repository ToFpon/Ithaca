"""MPRIS par un vrai client D-Bus (sauté sans bus de session) :

    dbus-run-session -- python3.12 -m unittest tests/test_mpris.py
"""

import os
import shutil
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="ithaca-mpris-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gi.repository import Gio, GLib  # noqa: E402

import media  # noqa: E402
from ithaca.backend import mpris  # noqa: E402
from ithaca.backend.controller import Controller  # noqa: E402
from ithaca.settings import settings  # noqa: E402
from test_backend import wait  # noqa: E402


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "pas de bus de session")
class MprisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = media.library(os.path.join(TMP, "lib"))

    def setUp(self):
        settings.data.update({"repeat": "consecutive", "shuffle": False, "volume": 1.0, "muted": False})
        self.c = Controller(os.path.join(TMP, "q.json"))
        self.raised = []
        self.m = mpris.Mpris(self.c, lambda: self.raised.append(1), lambda: None)
        self.m.start()
        self.assertTrue(wait(lambda: self.m.conn is not None, 5))
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.player = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None, mpris.BUS_NAME, mpris.PATH,
                                             mpris.PLAYER, None)
        self.props = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None, mpris.BUS_NAME, mpris.PATH,
                                            "org.freedesktop.DBus.Properties", None)
        self.changes = []
        self.props.connect("g-signal", lambda _p, _s, name, params: self.changes.append(params.unpack()))
        done = []
        self.c.add_paths([self.lib["album_a"]], on_done=lambda *a: done.append(1))
        self.assertTrue(wait(lambda: done, 20))

    def tearDown(self):
        self.m.stop()
        self.c.shutdown()

    def call(self, method, args=None):
        self.player.call(method, args, Gio.DBusCallFlags.NONE, 2000, None, None)

    def get(self, prop):
        out = []
        self.props.call("Get", GLib.Variant("(ss)", (mpris.PLAYER, prop)), Gio.DBusCallFlags.NONE, 2000, None,
                        lambda p, res: out.append(p.call_finish(res).unpack()[0]))
        self.assertTrue(wait(lambda: out, 3))
        return out[0]

    def test_play_pause_next_metadata(self):
        self.call("PlayPause")
        self.assertTrue(wait(lambda: self.c.playing, 5))
        self.assertEqual(self.get("PlaybackStatus"), "Playing")
        md = self.get("Metadata")
        self.assertEqual(md["xesam:title"], "Ouverture")
        self.assertEqual(md["xesam:artist"], ["Les Testeurs"])
        self.assertTrue(md["mpris:artUrl"].startswith("file://"))
        self.call("Next")
        self.assertTrue(wait(lambda: self.c.queue.pos == 1, 3))
        self.assertTrue(wait(lambda: any(c[1].get("Metadata", {}).get("xesam:title") == "Traversée"
                                         for c in self.changes if c[0] == mpris.PLAYER), 3),
                        "PropertiesChanged annonce le nouveau morceau")
        self.call("Pause")
        self.assertTrue(wait(lambda: not self.c.playing, 3))
        self.assertEqual(self.get("PlaybackStatus"), "Paused")

    def set(self, prop, value):
        out = []
        self.props.call("Set", GLib.Variant("(ssv)", (mpris.PLAYER, prop, value)), Gio.DBusCallFlags.NONE, 2000,
                        None, lambda p, res: out.append(p.call_finish(res)))
        self.assertTrue(wait(lambda: out, 3))

    def test_set_properties(self):
        self.set("Volume", GLib.Variant("d", 0.4))
        self.assertTrue(wait(lambda: abs(settings["volume"] - 0.4) < 0.001, 3))
        self.set("LoopStatus", GLib.Variant("s", "Playlist"))
        self.assertTrue(wait(lambda: self.c.queue.repeat == "all", 3))
        self.set("Shuffle", GLib.Variant("b", True))
        self.assertTrue(wait(lambda: self.c.queue.shuffled, 3))
        self.c.set_shuffled(False)
        settings["volume"] = 1.0

    def test_seek_and_raise(self):
        self.call("Play")
        self.assertTrue(wait(lambda: self.c.playing, 5))
        self.call("SetPosition", GLib.Variant("(ox)", (self.m.metadata()["mpris:trackid"].unpack(), 1_200_000)))
        self.assertTrue(wait(lambda: abs(self.c.player.position() - 1.2) < 0.25, 3))
        root = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None, mpris.BUS_NAME,
                                              mpris.PATH, mpris.ROOT, None)
        root.call("Raise", None, Gio.DBusCallFlags.NONE, 2000, None, None)
        self.assertTrue(wait(lambda: self.raised, 3))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
