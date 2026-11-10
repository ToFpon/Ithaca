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
    icy_url = ""                    # en-tête « icy-url » envoyé avec le flux (site de la station)

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass                     # le lecteur coupe le flux en s'arrêtant : normal

    def end_headers(self):
        if self.icy_url and self.path.lower().endswith(".mp3"):
            self.send_header("icy-url", self.icy_url)
            self.send_header("icy-name", "Radio Ithaque")
        super().end_headers()


def image(path, w, h, rgba=0x3366ccff, alpha=False):
    """Image de test (couleur unie ; alpha : fond transparent avec un carré coloré au centre)."""
    from gi.repository import GdkPixbuf
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, alpha, 8, w, h)
    pix.fill(0x00000000 if alpha else rgba)
    if alpha:
        pix.new_subpixbuf(w // 4, h // 4, w // 2, h // 2).fill(rgba)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pix.savev(path, "png" if path.endswith(".png") else "jpeg", [], [])
    return path


class LogoTest(unittest.TestCase):
    """Logos des webradios : lecture des listes, mise au carré, icône d'un site."""

    def test_extinf_attributes(self):
        d, attrs, title = pl.parse_extinf('-1 tvg-id="fip" tvg-logo="http://x/l,1.png" group-title="Jazz, Soul",FIP')
        self.assertEqual((d, title), (0.0, "FIP"))
        self.assertEqual(attrs["tvg-logo"], "http://x/l,1.png", "virgule dans un attribut")
        self.assertEqual(attrs["group-title"], "Jazz, Soul")
        self.assertEqual(pl.parse_extinf("123,Artiste - Titre"), (123.0, {}, "Artiste - Titre"))

    def test_logo_in_playlists(self):
        folder = tempfile.mkdtemp(dir=TMP)
        m3u = write(os.path.join(folder, "r.m3u"), '#EXTM3U\n#EXTINF:-1 tvg-logo="https://r.fr/logo.png",Radio\n'
                    "http://r.fr/live.mp3\n#EXTINF:-1 tvg-logo=\"logo-local.png\",Sans\nhttp://s.fr/live\n")
        entries = pl.parse(m3u)
        self.assertEqual(entries[0].logo, "https://r.fr/logo.png")
        self.assertEqual(entries[1].logo, "", "logo sans adresse : ignoré")
        from ithaca.backend.song import Song
        radio = Song("http://r.fr/live.mp3", title="Radio", logo="https://r.fr/logo.png")
        for ext in (".m3u", ".xspf"):
            path = pl.save([radio], os.path.join(folder, "out" + ext))
            self.assertEqual(pl.parse(path)[0].logo, "https://r.fr/logo.png", "logo gardé en " + ext)

    def test_squared(self):
        from gi.repository import GdkPixbuf
        from ithaca.backend import logos

        def load(png):
            loader = GdkPixbuf.PixbufLoader()
            loader.write(png)
            loader.close()
            return loader.get_pixbuf()
        folder = tempfile.mkdtemp(dir=TMP)
        wide = load(logos.squared_png(open(image(os.path.join(folder, "w.png"), 400, 100, alpha=True), "rb").read(), 32))
        self.assertEqual(wide.get_width(), wide.get_height(), "logo large : carré, pas rogné")
        self.assertEqual(wide.get_width(), 460, "un peu d'air autour")
        self.assertFalse(wide.get_has_alpha(), "fond opaque")
        self.assertEqual(bytes(wide.get_pixels()[:3]), b"\xff\xff\xff", "transparent : fond blanc")
        sq = load(logos.squared_png(open(image(os.path.join(folder, "s.jpg"), 900, 900), "rb").read(), 32))
        self.assertEqual(sq.get_width(), logos.MAX_SIDE, "réduit à MAX_SIDE, sans marge (déjà carré)")
        tiny = open(image(os.path.join(folder, "t.png"), 16, 16), "rb").read()
        self.assertIsNone(logos.squared_png(tiny, logos.MIN_SITE_SIZE), "favicon trop petit refusé")
        self.assertIsNone(logos.squared_png(b"<html>pas une image</html>", 32))

    def test_site_icon_urls(self):
        from ithaca.backend.logos import site_icon_urls
        page = (b'<html><head><link rel="icon" href="/f32.png" sizes="32x32">'
                b'<link rel="apple-touch-icon" href="touch.png"><link rel="stylesheet" href="s.css"></head>')
        self.assertEqual(site_icon_urls(page, "https://radio.fr/live/"),
                         ["https://radio.fr/live/touch.png", "https://radio.fr/f32.png", "https://radio.fr/favicon.ico"])
        self.assertEqual(site_icon_urls(b"", "http://r.fr"), ["http://r.fr/favicon.ico"])


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


class StreamLogoTest(unittest.TestCase):
    """Logo d'une webradio : donné par la liste (tvg-logo), sinon icône du site annoncé par le flux
    (en-tête icy-url) ; arrive après coup (événement « cover »), retenu avec la file, sans réseau ensuite."""

    def serve(self, icy_url=""):
        root = tempfile.mkdtemp(dir=TMP)            # à part : une image dans l'album en changerait la pochette
        shutil.copy(LIB["a1"], os.path.join(root, "live.mp3"))
        image(os.path.join(root, "logo.png"), 300, 120, rgba=0xcc3333ff)
        image(os.path.join(root, "touch.png"), 180, 180, rgba=0x33aa55ff)
        with open(os.path.join(root, "index.html"), "w") as fh:
            fh.write('<html><head><link rel="apple-touch-icon" href="/touch.png"></head></html>')
        handler_cls = type("H", (Handler,), {"icy_url": icy_url})
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: handler_cls(*a, directory=root, **k))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}", handler_cls

    def play(self, playlist, queue_file):
        c = Controller(queue_file)
        covers = []
        c.connect(lambda event, *a: covers.append(a[0]) if event == "cover" else None)
        done = []
        c.add_paths([playlist], play=True, on_done=lambda *a: done.append(1))
        self.assertTrue(wait(lambda: done, 10))
        return c, covers

    def test_logo_from_playlist(self):
        base, _h = self.serve()
        m3u = write(os.path.join(TMP, "logo.m3u"), f'#EXTM3U\n#EXTINF:-1 tvg-logo="{base}/logo.png",Radio Logo\n'
                    f"{base}/live.mp3\n")
        qf = os.path.join(TMP, "q-logo.json")
        c, covers = self.play(m3u, qf)
        try:
            self.assertTrue(wait(lambda: covers, 10), "logo arrivé")
            song = c.queue.items[0]
            self.assertIs(covers[0], song)
            self.assertTrue(os.path.isfile(song.cover) and song.cover.endswith(".png"))
            c.save()
        finally:
            c.player.stop()
            c.shutdown()
        c2 = Controller(qf)
        ok = []
        c2.restore_saved(lambda: ok.append(1))
        self.assertTrue(wait(lambda: ok, 10))
        self.assertEqual(c2.queue.items[0].cover, song.cover, "logo repris du cache, sans réseau")
        self.assertEqual(c2.queue.items[0].logo, f"{base}/logo.png")
        c2.shutdown()

    def test_site_icon_from_icy(self):
        base, _h = self.serve(icy_url="")
        base2, _ = self.serve(icy_url=base + "/")         # le flux annonce le site servi par le premier
        m3u = write(os.path.join(TMP, "icy.m3u"), f"#EXTM3U\n#EXTINF:-1,Radio Site\n{base2}/live.mp3\n")
        c, covers = self.play(m3u, os.path.join(TMP, "q-icy.json"))
        try:
            self.assertTrue(wait(lambda: c.queue.items and c.queue.items[0].site, 10), "site annoncé (icy-url)")
            self.assertTrue(wait(lambda: covers, 10), "icône du site arrivée")
            from gi.repository import GdkPixbuf
            pix = GdkPixbuf.Pixbuf.new_from_file(c.queue.items[0].cover)
            self.assertEqual(bytes(pix.get_pixels()[:3]), b"\x33\xaa\x55", "apple-touch-icon choisie")
        finally:
            c.player.stop()
            c.shutdown()

    def test_bad_logo_falls_back_to_nothing(self):
        base, _h = self.serve()
        m3u = write(os.path.join(TMP, "bad.m3u"), f'#EXTM3U\n#EXTINF:-1 tvg-logo="{base}/index.html",R\n'
                    f"{base}/live.mp3\n")
        c, covers = self.play(m3u, os.path.join(TMP, "q-bad.json"))
        try:
            wait(lambda: covers, 3)
            self.assertEqual(covers, [], "une page HTML n'est pas un logo")
            self.assertEqual(c.queue.items[0].cover, "")
        finally:
            c.player.stop()
            c.shutdown()


if __name__ == "__main__":
    unittest.main()
