"""Listes de lecture (.m3u, .m3u8, .xspf, .pls) et webradios (flux HTTP local), sans GTK."""

import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import media  # noqa: E402
from test_backend import load, wait  # noqa: E402     (définit aussi les dossiers XDG jetables)
from ithaca.backend import playlists as pl  # noqa: E402
from ithaca.backend.controller import Controller  # noqa: E402
from ithaca.backend.song import read_song  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ithaca-pl-")
LIB = {}


def setUpModule():
    LIB.update(media.library(os.path.join(TMP, "lib")))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
    return path


class ParseTest(unittest.TestCase):
    def test_m3u_forms(self):
        root = LIB["root"]
        a1 = LIB["a1"]
        rel = os.path.relpath(LIB["b1"], root)
        text = ("#EXTM3U\r\n#EXTINF:123,Les Testeurs - Ouverture\r\n" + a1 + "\r\n"
                + rel.replace("/", "\\") + "\r\n"                              # chemin relatif façon Windows
                + "file://" + urllib.parse.quote(LIB["b2"]) + "\r\n"
                + "#EXTINF:-1,Radio Ithaque\r\nhttp://radio.example/flux.mp3\r\n"
                + "C:\\Musique\\absent.mp3\r\n# commentaire\r\n")
        p = write(os.path.join(root, "liste.m3u"), text, "cp1252")
        entries = pl.parse(p)
        self.assertEqual([e.location for e in entries], [a1, LIB["b1"], LIB["b2"], "http://radio.example/flux.mp3"])
        self.assertEqual(entries[0].title, "Les Testeurs - Ouverture")
        self.assertEqual(entries[0].duration, 123)
        self.assertEqual(entries[3].title, "Radio Ithaque")

    def test_pls(self):
        p = write(os.path.join(TMP, "radio.pls"),
                  "[playlist]\nNumberOfEntries=2\nFile1=http://a.example/x\nTitle1=Station A\nLength1=-1\n"
                  "File2=" + os.path.relpath(LIB["a2"], TMP) + "\n")
        entries = pl.parse(p)
        self.assertEqual([e.location for e in entries], ["http://a.example/x", LIB["a2"]])
        self.assertEqual(entries[0].title, "Station A")

    def test_xspf(self):
        folder = os.path.join(TMP, "xs")
        rel = urllib.parse.quote(os.path.relpath(LIB["a2"], folder))
        p = write(os.path.join(folder, "l.xspf"),
                  '<?xml version="1.0"?><playlist version="1" xmlns="http://xspf.org/ns/0/"><trackList>'
                  f'<track><location>{rel}</location><title>Traversée</title><creator>Les Testeurs</creator>'
                  '<duration>2000</duration></track>'
                  '<track><location>https://radio.example/live</location><title>Live</title></track>'
                  '</trackList></playlist>')
        entries = pl.parse(p)
        self.assertEqual([e.location for e in entries], [LIB["a2"], "https://radio.example/live"])
        self.assertEqual(entries[0].title, "Les Testeurs - Traversée")
        self.assertEqual(entries[0].duration, 2.0)

    def test_broken(self):
        self.assertEqual(pl.parse(write(os.path.join(TMP, "abime.xspf"), "<playlist><track>")), [])
        self.assertEqual(pl.parse(os.path.join(TMP, "absente.m3u")), [])


class WriteTest(unittest.TestCase):
    def songs(self):
        return [read_song(LIB[k]) for k in ("a1", "a10", "b1")]

    def test_roundtrip_relative(self):
        for ext in (".m3u", ".m3u8", ".xspf"):
            folder = tempfile.mkdtemp(dir=TMP)
            path = pl.save(self.songs(), os.path.join(folder, "Ma liste" + ext))
            text = read(path)
            self.assertNotIn(LIB["root"], text, f"{ext} : chemins relatifs")
            self.assertEqual([e.location for e in pl.parse(path)], [LIB["a1"], LIB["a10"], LIB["b1"]], ext)
            if ext != ".xspf":
                self.assertIn("#EXTINF:2,Les Testeurs - Ouverture", text)

    def test_moved_with_music_still_works(self):
        """Liste copiée avec la musique ailleurs (clé USB) : elle marche toujours."""
        folder = os.path.join(LIB["root"])
        path = pl.save(self.songs(), os.path.join(folder, "voyage.m3u"))
        copy = os.path.join(TMP, "cle", "Musique")
        shutil.copytree(LIB["root"], copy)
        entries = pl.parse(os.path.join(copy, "voyage.m3u"))
        self.assertTrue(all(e.location.startswith(copy) and os.path.isfile(e.location) for e in entries))
        os.remove(path)

    def test_extension_added_never_overwrites(self):
        folder = tempfile.mkdtemp(dir=TMP)
        mine = write(os.path.join(folder, "Ma liste.m3u"), "précieux")
        path = pl.save(self.songs(), os.path.join(folder, "Ma liste"))
        self.assertEqual(os.path.basename(path), "Ma liste (2).m3u")
        self.assertEqual(read(mine), "précieux")

    def test_append_m3u_keeps_content_no_duplicates(self):
        folder = tempfile.mkdtemp(dir=TMP)
        path = write(os.path.join(folder, "l.m3u"), "#EXTM3U\n# à moi\nhttp://radio.example/x\n" + LIB["a1"] + "\n")
        os.chmod(path, 0o600)
        added, present = pl.append(self.songs(), path)
        self.assertEqual((added, present), (2, 1))
        text = read(path)
        self.assertTrue(text.startswith("#EXTM3U\n# à moi\nhttp://radio.example/x\n" + LIB["a1"] + "\n"))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, "droits gardés")
        self.assertEqual(pl.append(self.songs(), path), (0, 3))
        self.assertFalse([f for f in os.listdir(folder) if f.startswith(".ithaca-")], "pas de reste")

    def test_append_xspf(self):
        folder = tempfile.mkdtemp(dir=TMP)
        path = pl.save(self.songs()[:1], os.path.join(folder, "l.xspf"))
        self.assertEqual(pl.append(self.songs(), path), (2, 1))
        self.assertEqual(len(pl.parse(path)), 3)
        self.assertIn('xmlns="http://xspf.org/ns/0/"', read(path))


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class StreamTest(unittest.TestCase):
    """Webradio simulée : un MP3 servi en HTTP, déclaré dans une liste .m3u."""

    def setUp(self):
        root = os.path.dirname(LIB["a1"])
        handler = lambda *a, **k: Handler(*a, directory=root, **k)   # noqa: E731
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/" + urllib.parse.quote(os.path.basename(LIB["a1"]))
        self.list = write(os.path.join(TMP, "radios.m3u"), f"#EXTM3U\n#EXTINF:-1,Radio Ithaque\n{self.url}\n")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_stream_from_playlist(self):
        songs, (read, skipped) = load([self.list])
        self.assertEqual((read, skipped), (1, 0))
        s = songs[0]
        self.assertTrue(s.stream)
        self.assertEqual(s.title, "Radio Ithaque")
        c = Controller(os.path.join(TMP, "q.json"))
        try:
            done = []
            c.add_paths([self.list], play=True, on_done=lambda *a: done.append(1))
            self.assertTrue(wait(lambda: done and c.playing, 10))
            self.assertTrue(wait(lambda: c.song.now_playing == "Ouverture", 10), "titre annoncé par le flux")
            self.assertEqual(c.song.display_title, "Ouverture")
            c.save()
            c.shutdown()
            c2 = Controller(os.path.join(TMP, "q.json"))
            ok = []
            c2.restore_saved(lambda: ok.append(1))
            self.assertTrue(wait(lambda: ok, 10))
            self.assertEqual(c2.queue.items[0].title, "Radio Ithaque", "nom de la station retenu")
            c2.shutdown()
        finally:
            c.player.stop()

    def test_missing_files_counted(self):
        p = write(os.path.join(TMP, "trous.m3u"), LIB["a1"] + "\n/n/existe/pas.mp3\n")
        songs, (read, skipped) = load([p])
        self.assertEqual((read, skipped), (1, 1))


if __name__ == "__main__":
    unittest.main()
