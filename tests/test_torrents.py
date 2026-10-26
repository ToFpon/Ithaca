"""Torrents (sans GTK) : vraies sessions libtorrent sur 127.0.0.1 (pair source, pair qui
télécharge depuis Ithaca pour le ratio), chiffrement exigé des deux côtés, sans DHT ni UPnP."""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="ithaca-ttest-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")
os.environ["ITHACA_TORRENT_LISTEN"] = "127.0.0.1:0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ithaca.backend import downloads as dl  # noqa: E402
from ithaca.backend import torrents  # noqa: E402
from ithaca.settings import settings  # noqa: E402
from test_engine import pump, wait  # noqa: E402

lt = torrents.lt


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def session(seed=False):
    return lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False, "enable_lsd": False,
                       "enable_upnp": False, "enable_natpmp": False, "allow_multiple_connections_per_ip": True,
                       "out_enc_policy": int(lt.enc_policy.forced), "in_enc_policy": int(lt.enc_policy.forced),
                       "min_reconnect_time": 1})


def make_torrent(path: str, root: str) -> bytes:
    fs = lt.file_storage()
    lt.add_files(fs, path)
    ct = lt.create_torrent(fs)
    lt.set_piece_hashes(ct, root)
    return lt.bencode(ct.generate())


@unittest.skipUnless(torrents.available(), "python3-libtorrent absent")
class TorrentTest(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp(dir=TMP, prefix="src-")
        self.out = tempfile.mkdtemp(dir=TMP, prefix="out-")
        self.state = os.path.join(tempfile.mkdtemp(dir=TMP), "downloads.json")
        album = os.path.join(self.src, "Album")
        os.makedirs(os.path.join(album, "CD1"))
        self.files = {"CD1/01.flac": 700_000, "CD1/02.flac": 500_000, "pochette.jpg": 90_000}
        for rel, size in self.files.items():
            with open(os.path.join(album, rel), "wb") as fh:
                fh.write(os.urandom(size))
        self.data = make_torrent(album, self.src)
        self.seeder = session()
        p = lt.add_torrent_params()
        p.ti = lt.torrent_info(lt.bdecode(self.data))
        p.save_path = self.src
        p.flags |= lt.torrent_flags.seed_mode
        self.seed_handle = self.seeder.add_torrent(p)
        self.managers = []
        self.events = []
        settings.data.update({"seed_mode": "never", "seed_ratio": 1.0, "simultaneous": 5,
                              "torrent_encryption": True})

    def tearDown(self):
        for m in self.managers:
            m.shutdown()
        self.seeder.pause()

    def manager(self):
        m = dl.Manager(self.state)
        m.connect(lambda ev, d: self.events.append((ev, d)))
        m.start()
        self.managers.append(m)
        return m

    def seeder_port(self):
        return self.seeder.listen_port()

    def add(self, m, wanted=None, root="Album", names=None):
        p = m.torrents.prepare_file(self.data, "test.torrent")
        wanted = {f.index for f in p.files} if wanted is None else wanted
        d = m.add_torrent(p, self.out, wanted, root, names or {})
        pump(0.3)
        m.torrents.connect_peer(d, "127.0.0.1", self.seeder_port())
        return d, p

    def test_files_skip_pad(self):
        m = self.manager()
        p = m.torrents.prepare_file(self.data)
        paths = sorted(f.path for f in p.files)
        self.assertEqual(paths, ["Album/CD1/01.flac", "Album/CD1/02.flac", "Album/pochette.jpg"])
        self.assertTrue(p.multi)
        self.assertEqual(p.total, sum(self.files.values()))

    def test_download_rename_and_select(self):
        m = self.manager()
        p = m.torrents.prepare_file(self.data)
        by = {f.path: f.index for f in p.files}
        wanted = {by["Album/CD1/01.flac"], by["Album/pochette.jpg"]}
        d = m.add_torrent(p, self.out, wanted, "Mon album", {by["Album/pochette.jpg"]: "cover.jpg"})
        pump(0.3)
        m.torrents.connect_peer(d, "127.0.0.1", self.seeder_port())
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 40), d.state)
        self.assertEqual(d.state, "complete", d.error)
        self.assertEqual(d.name, "Mon album")
        got = os.path.join(self.out, "Mon album")
        self.assertEqual(sha(os.path.join(got, "CD1", "01.flac")), sha(os.path.join(self.src, "Album/CD1/01.flac")))
        self.assertEqual(sha(os.path.join(got, "cover.jpg")), sha(os.path.join(self.src, "Album/pochette.jpg")))
        self.assertFalse(os.path.exists(os.path.join(got, "CD1", "02.flac")), "fichier décoché absent")
        self.assertIn(("completed", d), self.events)
        self.assertNotIn(d.id, m.torrents.handles, "partage « jamais » : retiré de la session")

    def test_magnet(self):
        m = self.manager()
        ih = lt.torrent_info(lt.bdecode(self.data)).info_hashes().v1
        ready = []
        p = m.torrents.prepare_magnet(f"magnet:?xt=urn:btih:{ih}&dn=Album", ready.append, None)
        m.torrents.connect_peer(p, "127.0.0.1", self.seeder_port())
        self.assertTrue(wait(lambda: ready, 20), "informations reçues")
        self.assertEqual(os.listdir(self.out), [], "rien écrit avant le choix")
        d = m.add_torrent(p, self.out, {f.index for f in p.files}, "Album", {})
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 40), d.state)
        self.assertEqual(d.state, "complete", d.error)
        self.assertEqual(sha(os.path.join(self.out, "Album", "CD1", "02.flac")),
                         sha(os.path.join(self.src, "Album/CD1/02.flac")))

    def test_magnet_discard(self):
        m = self.manager()
        ih = lt.torrent_info(lt.bdecode(self.data)).info_hashes().v1
        p = m.torrents.prepare_magnet(f"magnet:?xt=urn:btih:{ih}", lambda p: None, None)
        m.torrents.discard(p)
        self.assertEqual(m.torrents.pending, [])
        self.assertEqual(m.downloads, [])

    def test_cancel_deletes_only_torrent_files(self):
        self.seed_handle.set_upload_limit(200_000)
        mine = os.path.join(self.out, "à moi.txt")
        with open(mine, "w") as fh:
            fh.write("x")
        m = self.manager()
        d, _p = self.add(m)
        self.assertTrue(wait(lambda: d.done > 100_000, 30), d.state)
        m.remove(d)
        self.assertTrue(wait(lambda: not os.path.exists(os.path.join(self.out, "Album")), 10))
        self.assertEqual(os.listdir(self.out), ["à moi.txt"])

    def test_pause_resume_restart(self):
        self.seed_handle.set_upload_limit(300_000)
        m = self.manager()
        d, _p = self.add(m)
        self.assertTrue(wait(lambda: d.done > 200_000, 30), d.state)
        m.pause(d)
        pump(1.5)
        self.assertEqual(d.state, "paused")
        done = d.done
        pump(1.0)
        self.assertEqual(d.done, done, "rien ne bouge en pause")
        m.resume(d)
        # la pause coupe les connexions ; sans tracker ni DHT (tests), on redonne le pair
        pump(0.5)
        m.torrents.connect_peer(d, "127.0.0.1", self.seeder_port())
        self.assertTrue(wait(lambda: d.done > done + 50_000, 20), d.state)
        m.shutdown()
        self.managers.remove(m)
        self.seed_handle.set_upload_limit(0)
        m2 = self.manager()
        d2 = m2.downloads[0]
        self.assertIn(d2.id, m2.torrents.handles, "relancé depuis les données de reprise")
        m2.torrents.connect_peer(d2, "127.0.0.1", self.seeder_port())
        self.assertTrue(wait(lambda: d2.state in ("complete", "error"), 40), d2.state)
        self.assertEqual(d2.state, "complete", d2.error)
        for rel in self.files:
            self.assertEqual(sha(os.path.join(self.out, "Album", rel)), sha(os.path.join(self.src, "Album", rel)))

    def test_seed_until_ratio(self):
        """Partage jusqu'au ratio 1,0 : un autre pair télécharge depuis Ithaca, puis le partage s'arrête."""
        settings.data["seed_mode"] = "ratio"
        m = self.manager()
        d, _p = self.add(m)
        self.assertTrue(wait(lambda: d.state == "seeding", 40), d.state)
        self.seeder.remove_torrent(self.seed_handle)           # la source s'en va : seul Ithaca a le fichier
        leech = session()
        p = lt.add_torrent_params()
        p.ti = lt.torrent_info(lt.bdecode(self.data))
        p.save_path = tempfile.mkdtemp(dir=TMP)
        h = leech.add_torrent(p)
        port = m.torrents.session.listen_port()
        h.connect_peer(("127.0.0.1", port))
        self.assertTrue(wait(lambda: d.state == "complete", 60), f"{d.state} {d.uploaded}")
        self.assertGreaterEqual(torrents.ratio(d), 1.0)
        self.assertEqual(sum(1 for e, x in self.events if e == "completed" and x is d), 1)
        leech.pause()

    def test_renamed_paths(self):
        ti = lt.torrent_info(lt.bdecode(self.data))
        fs = ti.files()
        idx = {fs.file_path(i): i for i in range(fs.num_files())}
        r = torrents.renamed_paths(ti, "Neuf", {idx["Album/CD1/01.flac"]: "un.flac"})
        self.assertEqual(r[idx["Album/CD1/01.flac"]], "Neuf/CD1/un.flac")
        self.assertEqual(r[idx["Album/pochette.jpg"]], "Neuf/pochette.jpg")
        self.assertEqual(torrents.renamed_paths(ti, "Album", {}), {})


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
