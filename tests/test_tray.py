"""Icône de la barre système (StatusNotifierItem + dbusmenu) face à une fausse barre
(org.kde.StatusNotifierWatcher), et arrêt propre d'Ithaca sur Ctrl+C. Sauté sans bus de session :

    dbus-run-session -- python3.12 -m unittest discover -s tests -p "test_tray.py"
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp(prefix="ithaca-tray-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gi.repository import Gio, GLib  # noqa: E402

import media  # noqa: E402
from ithaca.backend import tray as tray_mod  # noqa: E402
from ithaca.backend.controller import Controller  # noqa: E402
from test_backend import wait  # noqa: E402

WATCHER_XML = """<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
</interface></node>"""


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "pas de bus de session")
class TrayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = media.library(os.path.join(TMP, "lib"))
        cls.registered = []
        info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]

        def acquired(conn, _name):
            def call(_c, _s, _p, _i, method, params, inv):
                cls.registered.append(params.unpack()[0])
                inv.return_value(None)
            conn.register_object("/StatusNotifierWatcher", info, call, None, None)
            cls.watcher_ready = True
        cls.watcher_ready = False
        cls.owner = Gio.bus_own_name(Gio.BusType.SESSION, tray_mod.WATCHER, Gio.BusNameOwnerFlags.NONE,
                                     acquired, None, None)
        assert wait(lambda: cls.watcher_ready, 5)

    def setUp(self):
        self.c = Controller(os.path.join(TMP, "q.json"))
        done = []
        self.c.add_paths([self.lib["album_a"]], on_done=lambda *a: done.append(1))
        self.assertTrue(wait(lambda: done, 20))
        self.shown, self.quit = [], []
        self.t = tray_mod.Tray(self.c, lambda: self.shown.append(1), lambda: self.quit.append(1))
        self.t.show()
        self.assertTrue(wait(lambda: self.t.registered, 5), "icône enregistrée auprès de la barre")
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        flags = Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
        self.menu = Gio.DBusProxy.new_sync(bus, flags, None, self.t.name, tray_mod.MENU_PATH,
                                           "com.canonical.dbusmenu", None)
        self.item = Gio.DBusProxy.new_sync(bus, flags, None, self.t.name, tray_mod.ITEM_PATH,
                                           "org.kde.StatusNotifierItem", None)

    def tearDown(self):
        self.t.hide()
        self.c.shutdown()

    def call(self, proxy, method, args=None):
        out = []
        proxy.call(method, args, Gio.DBusCallFlags.NONE, 2000, None, lambda p, r: out.append(p.call_finish(r)))
        self.assertTrue(wait(lambda: out, 3), method)
        return out[0].unpack() if out[0] is not None else None

    def labels(self):
        rev, (root, props, children) = self.call(self.menu, "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
        return {i: p.get("label", p.get("type")) for i, p, _c in children}

    def test_menu_and_actions(self):
        self.assertIn(self.t.name, self.registered)
        labels = self.labels()
        self.assertEqual(labels[tray_mod.SONG], "Ouverture — Les Testeurs")
        self.assertEqual(labels[tray_mod.SEP], "separator")
        self.assertIn(tray_mod.QUIT, labels)
        self.call(self.menu, "Event", GLib.Variant("(isvu)", (tray_mod.PLAY, "clicked", GLib.Variant("s", ""), 0)))
        self.assertTrue(wait(lambda: self.c.playing, 5), "Lire depuis le menu")
        self.call(self.menu, "Event", GLib.Variant("(isvu)", (tray_mod.NEXT, "clicked", GLib.Variant("s", ""), 0)))
        self.assertTrue(wait(lambda: self.c.queue.pos == 1, 3))
        self.call(self.item, "Activate", GLib.Variant("(ii)", (0, 0)))
        self.assertTrue(wait(lambda: self.shown, 3), "clic sur l'icône : fenêtre")
        self.call(self.menu, "Event", GLib.Variant("(isvu)", (tray_mod.QUIT, "clicked", GLib.Variant("s", ""), 0)))
        self.assertTrue(wait(lambda: self.quit, 3), "Quitter")

    def test_hide(self):
        self.t.hide()
        self.assertFalse(self.t.active)
        self.t.show()
        self.assertTrue(wait(lambda: self.t.registered, 5))


@unittest.skipUnless(shutil.which("xvfb-run"), "xvfb-run absent")
class SigintTest(unittest.TestCase):
    def test_ctrl_c_quits_cleanly(self):
        lib = media.library(os.path.join(TMP, "lib2"))
        env = dict(os.environ)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        # sortie dans un fichier : un tuyau non lu se remplissait et bloquait Ithaca (signal jamais traité)
        log_path = os.path.join(TMP, "ithaca.log")
        log = open(log_path, "wb")
        p = subprocess.Popen(["xvfb-run", "-a", sys.executable, os.path.join(ROOT, "bin", "ithaca"), lib["album_a"]],
                             env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        # dossier XDG réellement transmis (test_backend, importé ici, redéfinit les siens à l'import)
        qfile = os.path.join(env["XDG_DATA_HOME"], "ithaca", "queue.json")
        if os.path.exists(qfile):
            os.remove(qfile)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not os.path.exists(qfile):
            time.sleep(0.2)
        time.sleep(1.0)
        # l'Ithaca lancé par xvfb-run : enfant de son groupe de processus, pas un autre resté ouvert
        # (le motif commence par l'interpréteur : la ligne de commande de xvfb-run contient aussi
        # « python…bin/ithaca », et c'est lui qui recevait le signal)
        child = subprocess.run(["pgrep", "-g", str(p.pid), "-f", "^[^ ]*python[0-9.]* .*bin/ithaca"],
                               capture_output=True, text=True).stdout.split()
        self.assertTrue(child, "Ithaca lancé")
        pid = int(child[0])
        os.kill(pid, signal.SIGINT)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and os.path.exists(f"/proc/{pid}"):
            time.sleep(0.1)
        stopped = not os.path.exists(f"/proc/{pid}")
        os.killpg(p.pid, signal.SIGTERM)                 # xvfb-run et son Xvfb
        p.wait(timeout=10)
        log.close()
        with open(log_path, "rb") as fh:
            err = fh.read()
        self.assertTrue(stopped, "Ithaca ne s'est pas arrêté sur Ctrl+C")
        self.assertNotIn(b"Traceback", err, err.decode(errors="replace")[-800:])
        self.assertNotIn(b"KeyboardInterrupt", err)
        self.assertTrue(os.path.exists(qfile), err.decode(errors="replace")[-1500:])
        with open(qfile) as fh:
            self.assertEqual(len(json.load(fh)["songs"]), 3, "file enregistrée en quittant")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
