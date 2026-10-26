"""Moteur de téléchargement (sans GTK) : vrai aria2, serveur HTTP local.

    python3.12 -m unittest discover -s tests
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp(prefix="ithaca-test-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gi.repository import GLib  # noqa: E402

from ithaca.backend import downloads as dl  # noqa: E402
from ithaca.settings import settings  # noqa: E402
from server import Server  # noqa: E402


def wait(cond, timeout=20.0) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def pump(seconds: float):
    wait(lambda: False, seconds)


def sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Base(unittest.TestCase):
    rate = 0
    auth = ""

    def setUp(self):
        self.root = tempfile.mkdtemp(dir=TMP, prefix="srv-")
        self.out = tempfile.mkdtemp(dir=TMP, prefix="out-")
        self.state = os.path.join(tempfile.mkdtemp(dir=TMP, prefix="state-"), "downloads.json")
        self.server = Server(self.root, rate=self.rate, auth=self.auth)
        self.events = []
        self.managers = []
        settings.data.update({"segments": 4, "simultaneous": 5, "speed_limit_enabled": False,
                              "auth_enabled": False, "auth_user": "", "auth_password": "",
                              "cookies_file": ""})

    def tearDown(self):
        for m in self.managers:
            m.shutdown()
        self.server.close()

    def make(self, name: str, size: int) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as fh:
            fh.write(os.urandom(size))
        return path

    def manager(self) -> dl.Manager:
        m = dl.Manager(self.state)
        m.connect(lambda ev, d: self.events.append((ev, d)))
        m.start()
        self.managers.append(m)
        self.assertTrue(wait(lambda: m.engine_ready or m.engine_error, 10), "aria2 démarré")
        self.assertEqual(m.engine_error, "")
        return m


class EngineTest(Base):
    def test_download_complete(self):
        src = self.make("fichier.bin", 3_000_000)
        m = self.manager()
        d = m.add(self.server.url("fichier.bin"), self.out)
        self.assertTrue(wait(lambda: d.state == "complete"), d.state + " " + d.error)
        self.assertEqual(d.name, "fichier.bin")
        self.assertEqual(sha(d.path), sha(src))
        self.assertFalse(os.path.exists(d.path + ".aria2"))
        self.assertIn(("completed", d), self.events)
        self.assertEqual(d.fraction, 1.0)

    def test_existing_name_never_overwritten(self):
        src = self.make("photo.jpg", 200_000)
        precious = os.path.join(self.out, "photo.jpg")
        with open(precious, "w") as fh:
            fh.write("précieux")
        m = self.manager()
        d = m.add(self.server.url("photo.jpg"), self.out)
        self.assertTrue(wait(lambda: d.state == "complete"), d.state + " " + d.error)
        self.assertEqual(d.name, "photo (2).jpg")
        self.assertEqual(open(precious).read(), "précieux")
        self.assertEqual(sha(d.path), sha(src))
        # deux fois de suite : « (3) »
        d2 = m.add(self.server.url("photo.jpg"), self.out)
        self.assertTrue(wait(lambda: d2.state == "complete"))
        self.assertEqual(d2.name, "photo (3).jpg")

    def test_content_disposition_utf8(self):
        src = self.make("x.bin", 50_000)
        m = self.manager()
        d = m.add(self.server.url("x.bin") + "?as=" + "%C3%89t%C3%A9%20%C3%A0%20Paris.bin", self.out)
        self.assertTrue(wait(lambda: d.state == "complete"), d.error)
        self.assertEqual(d.name, "Été à Paris.bin")
        self.assertEqual(sha(d.path), sha(src))

    def test_not_found_then_retry(self):
        m = self.manager()
        d = m.add(self.server.url("absent.bin"), self.out)
        self.assertTrue(wait(lambda: d.state == "error"))
        self.assertTrue(d.error)
        self.assertIn(("failed", d), self.events)
        self.make("absent.bin", 10_000)
        m.retry(d)
        self.assertTrue(wait(lambda: d.state == "complete"), d.state + " " + d.error)

    def test_cancel_removes_partial_only(self):
        self.server.set_rate(100_000)
        self.make("lent.bin", 3_000_000)
        m = self.manager()
        d = m.add(self.server.url("lent.bin"), self.out)
        self.assertTrue(wait(lambda: d.done > 50_000))
        path = d.path
        self.assertTrue(os.path.exists(path + ".aria2"))
        m.remove(d)
        self.assertTrue(wait(lambda: not os.path.exists(path) and not os.path.exists(path + ".aria2"), 8))
        self.assertNotIn(d, m.downloads)
        # sans fichier de contrôle : jamais supprimé
        other = os.path.join(self.out, "autre.bin")
        with open(other, "w") as fh:
            fh.write("à moi")
        self.assertFalse(dl.remove_partial(other))
        self.assertTrue(os.path.exists(other))

    def test_remove_complete_keeps_file(self):
        self.make("garde.bin", 20_000)
        m = self.manager()
        d = m.add(self.server.url("garde.bin"), self.out)
        self.assertTrue(wait(lambda: d.state == "complete"))
        m.remove(d)
        pump(1)
        self.assertTrue(os.path.exists(d.path))

    def test_pause_resume(self):
        self.server.set_rate(150_000)
        src = self.make("pause.bin", 1_000_000)
        m = self.manager()
        d = m.add(self.server.url("pause.bin"), self.out)
        self.assertTrue(wait(lambda: d.done > 100_000))
        m.pause(d)
        self.assertEqual(d.state, "paused")
        pump(1.5)
        done = d.done
        pump(1.5)
        self.assertEqual(d.state, "paused")
        self.assertEqual(d.done, done, "rien ne bouge en pause")
        self.server.set_rate(0)
        m.resume(d)
        self.assertTrue(wait(lambda: d.state == "complete"), d.state)
        self.assertEqual(sha(d.path), sha(src))

    def test_added_paused(self):
        self.make("plus-tard.bin", 10_000)
        m = self.manager()
        d = m.add(self.server.url("plus-tard.bin"), self.out, paused=True)
        pump(1.5)
        self.assertEqual(d.state, "paused")
        m.resume(d)
        self.assertTrue(wait(lambda: d.state == "complete"))

    def test_resume_after_restart(self):
        """Ithaca fermé en plein téléchargement : au lancement suivant, il reprend où il en était."""
        self.server.set_rate(200_000)
        src = self.make("long.bin", 4_000_000)
        m = self.manager()
        d = m.add(self.server.url("long.bin"), self.out)
        self.assertTrue(wait(lambda: d.done > 600_000 and d.name))
        m.shutdown()
        self.managers.remove(m)
        self.server.handler.requests.clear()
        self.server.set_rate(0)
        m2 = self.manager()
        self.assertEqual(len(m2.downloads), 1)
        d2 = m2.downloads[0]
        self.assertEqual(d2.id, d.id)
        self.assertTrue(wait(lambda: d2.state == "complete"), d2.state + " " + d2.error)
        self.assertEqual(d2.name, "long.bin", "même fichier, pas « long (2).bin »")
        self.assertEqual(sha(d2.path), sha(src))
        self.assertFalse(os.path.exists(os.path.join(self.out, "long (2).bin")))

    def test_history_kept(self):
        self.make("histoire.bin", 10_000)
        m = self.manager()
        d = m.add(self.server.url("histoire.bin"), self.out)
        self.assertTrue(wait(lambda: d.state == "complete"))
        m.shutdown()
        self.managers.remove(m)
        m2 = self.manager()
        self.assertEqual([(x.name, x.state) for x in m2.downloads], [("histoire.bin", "complete")])
        m2.clear_finished()
        pump(1)
        self.assertEqual(m2.downloads, [])
        self.assertTrue(os.path.exists(d.path), "effacer la liste ne supprime pas le fichier")

    def test_live_global_options(self):
        m = self.manager()
        settings["speed_limit"] = 300
        settings["speed_limit_enabled"] = True
        settings["simultaneous"] = 2
        pump(1)
        opts = []
        m.engine.submit(lambda rpc: opts.append(rpc.call("aria2.getGlobalOption")))
        self.assertTrue(wait(lambda: opts))
        self.assertEqual(opts[0]["max-overall-download-limit"], str(300 * 1000))
        self.assertEqual(opts[0]["max-concurrent-downloads"], "2")
        settings["speed_limit_enabled"] = False
        settings["simultaneous"] = 5


class AuthTest(Base):
    auth = "tof:sésame"

    def test_auth(self):
        src = self.make("prive.bin", 30_000)
        m = self.manager()
        d = m.add(self.server.url("prive.bin"), self.out)
        self.assertTrue(wait(lambda: d.state == "error"), "refusé sans identifiants")
        settings.data.update({"auth_enabled": True, "auth_user": "tof", "auth_password": "sésame"})
        m.retry(d)
        self.assertTrue(wait(lambda: d.state == "complete"), d.error)
        self.assertEqual(sha(d.path), sha(src))


class HelpersTest(unittest.TestCase):
    def test_free_name(self):
        folder = tempfile.mkdtemp(dir=TMP)
        self.assertEqual(dl.free_name(folder, "a.tar.gz"), "a.tar.gz")
        open(os.path.join(folder, "a.tar.gz"), "w").close()
        self.assertEqual(dl.free_name(folder, "a.tar.gz"), "a (2).tar.gz")
        open(os.path.join(folder, "a (2).tar.gz.aria2"), "w").close()
        self.assertEqual(dl.free_name(folder, "a.tar.gz"), "a (3).tar.gz")
        self.assertEqual(dl.free_name(folder, "README", {"README"}), "README (2)")
        self.assertEqual(dl.free_name(folder, ".bashrc", {".bashrc"}), ".bashrc (2)")

    def test_urls(self):
        self.assertTrue(dl.is_supported_url("https://exemple.fr/a.iso"))
        self.assertTrue(dl.is_supported_url(" ftp://exemple.fr/a "))
        self.assertFalse(dl.is_supported_url("file:///etc/passwd"))
        self.assertFalse(dl.is_supported_url("bonjour"))
        self.assertEqual(dl.name_from_url("https://x.fr/dossier/%C3%A9t%C3%A9.zip?x=1"), "été.zip")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
