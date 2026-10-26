"""Vidéos (sans GTK) : installation des programmes gérés, vrai yt-dlp sur des vidéos locales
générées par ffmpeg (extracteur générique : fichier direct, page à plusieurs vidéos = playlist).

yt-dlp et Deno réels : ITHACA_TEST_TOOLS (dossier contenant yt-dlp et deno), sinon
~/.cache/ithaca-test-tools, installés par tools.install au premier passage (réseau) ; sans eux,
les tests de téléchargement sont sautés.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="ithaca-vtest-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")
TOOLS_DIR = os.environ.get("ITHACA_TEST_TOOLS") or os.path.expanduser("~/.cache/ithaca-test-tools")
os.environ["ITHACA_BIN_DIR"] = TOOLS_DIR
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ithaca.backend import downloads as dl  # noqa: E402
from ithaca.backend import tools, videos  # noqa: E402
from ithaca.settings import settings  # noqa: E402
from server import Server  # noqa: E402
from test_engine import wait  # noqa: E402

MEDIA = os.path.join(TMP, "media")


def setUpModule():
    os.makedirs(MEDIA)
    for name, size, rate, secs in (("clip1.mp4", "640x360", 440, 4), ("clip2.mp4", "320x240", 660, 3),
                                   ("long.mp4", "1280x720", 330, 40)):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25",
                        "-f", "lavfi", "-i", f"sine=frequency={rate}", "-t", str(secs), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(MEDIA, name)], check=True)
    with open(os.path.join(MEDIA, "page.html"), "w") as fh:
        fh.write('<html><head><title>Ma page vidéo</title></head><body>'
                 '<video src="clip1.mp4"></video><video src="clip2.mp4"></video></body></html>')
    if not tools.installed("yt-dlp"):
        try:
            tools.install("yt-dlp")
            tools.install("deno")
        except tools.ToolError:
            pass


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def streams(path):
    """Empreinte des pistes elles-mêmes (le conteneur change : métadonnées ajoutées par yt-dlp)."""
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-map", "0:v", "-map", "0:a", "-c", "copy",
                           "-f", "streamhash", "-hash", "sha256", "-"], capture_output=True, text=True).stdout


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class InstallTest(unittest.TestCase):
    """Installation vérifiée par la somme publiée, depuis un « GitHub » local."""

    def setUp(self):
        self.pub = tempfile.mkdtemp(dir=TMP)
        self.bin = tempfile.mkdtemp(dir=TMP)
        self.old_bin, self.old_tools = tools.BIN_DIR, dict(tools.TOOLS)
        tools.BIN_DIR = self.bin
        self.server = Server(self.pub)
        self.script = b"#!/bin/sh\necho 2099.01.01\n"
        with open(os.path.join(self.pub, "yt-dlp_linux"), "wb") as fh:
            fh.write(self.script)
        self.publish(hashlib.sha256(self.script).hexdigest())
        tools.TOOLS["yt-dlp"] = tools.Tool("yt-dlp", "yt-dlp", self.server.url("yt-dlp_linux"),
                                           self.server.url("SHA2-256SUMS"), "yt-dlp_linux")

    def publish(self, digest):
        with open(os.path.join(self.pub, "SHA2-256SUMS"), "w") as fh:
            fh.write(f"{'0' * 64}  yt-dlp_linux_aarch64\n{digest}  yt-dlp_linux\n")

    def tearDown(self):
        tools.BIN_DIR = self.old_bin
        tools.TOOLS.clear()
        tools.TOOLS.update(self.old_tools)
        self.server.close()

    def test_install_and_update(self):
        seen = []
        self.assertEqual(tools.install("yt-dlp", progress=lambda a, b: seen.append(a)), "2099.01.01")
        self.assertTrue(seen)
        self.assertEqual(tools.path("yt-dlp"), os.path.join(self.bin, "yt-dlp"))
        self.assertFalse(tools.update_available("yt-dlp"))
        self.publish("f" * 64)
        self.assertTrue(tools.update_available("yt-dlp"), "nouvelle somme publiée = mise à jour")

    def test_bad_checksum_refused(self):
        self.publish("a" * 64)
        with self.assertRaises(tools.ToolError):
            tools.install("yt-dlp")
        self.assertFalse(tools.installed("yt-dlp"))
        self.assertEqual(os.listdir(self.bin), [], "rien d'installé, aucun reste")

    def test_bad_checksum_keeps_old(self):
        tools.install("yt-dlp")
        self.publish("a" * 64)
        with self.assertRaises(tools.ToolError):
            tools.install("yt-dlp")
        self.assertEqual(tools.version("yt-dlp"), "2099.01.01", "l'ancienne version reste")


class HelpersTest(unittest.TestCase):
    def test_looks_like_video(self):
        self.assertTrue(videos.looks_like_video("https://www.youtube.com/watch?v=x"))
        self.assertTrue(videos.looks_like_video("https://youtu.be/x"))
        self.assertTrue(videos.looks_like_video("https://music.youtube.com/watch?v=x"))
        self.assertFalse(videos.looks_like_video("https://notyoutube.com/a.iso"))
        self.assertFalse(videos.looks_like_video("https://releases.ubuntu.com/a.iso"))

    def test_format_args(self):
        a = videos.format_args({"mode": "audio", "audio_format": "mp3"})
        self.assertIn("-x", a)
        self.assertIn("mp3", a)
        v = videos.format_args({"mode": "video", "height": 720, "container": "mkv", "subs": ["fr"]})
        self.assertIn("[height<=720]", v[1])
        self.assertIn("--embed-subs", v)

    def test_summarize(self):
        info = {"title": "T", "formats": [{"height": 1080, "vcodec": "avc1"}, {"height": 720, "vcodec": "avc1"},
                                           {"vcodec": "none", "acodec": "opus"}],
                "subtitles": {"fr": [{"name": "Français"}], "live_chat": []}}
        s = videos.summarize(info)
        self.assertEqual(s["heights"], [1080, 720])
        self.assertEqual(s["subtitles"], {"fr": "Français"})
        self.assertTrue(s["has_video"])
        self.assertFalse(videos.summarize({"formats": [{"vcodec": "none"}]})["has_video"])


@unittest.skipUnless(tools.installed("yt-dlp") or os.path.isdir(TOOLS_DIR), "yt-dlp absent")
class VideoTest(unittest.TestCase):
    def setUp(self):
        if not tools.installed("yt-dlp"):
            self.skipTest("yt-dlp absent (pas de réseau ?)")
        self.server = Server(MEDIA)
        self.out = tempfile.mkdtemp(dir=TMP, prefix="out-")
        self.state = os.path.join(tempfile.mkdtemp(dir=TMP), "downloads.json")
        self.events = []
        self.managers = []
        settings.data.update({"simultaneous": 5, "browser_cookies": "", "cookies_file": ""})

    def tearDown(self):
        for m in self.managers:
            m.shutdown()
        self.server.close()

    def manager(self):
        m = dl.Manager(self.state)
        m.connect(lambda ev, d: self.events.append((ev, d)))
        m.start()
        self.managers.append(m)
        return m

    def opts(self, **kw):
        o = videos.default_options()
        o.update(kw)
        return o

    def test_probe(self):
        info = videos.summarize(videos.probe(self.server.url("clip1.mp4")))
        self.assertEqual(info["title"], "clip1")
        self.assertFalse(info["playlist"])
        pl = videos.summarize(videos.probe(self.server.url("page.html")))
        self.assertTrue(pl["playlist"])
        self.assertEqual(pl["count"], 2)
        self.assertEqual(pl["title"], "Ma page vidéo")

    def test_probe_error(self):
        with self.assertRaises(videos.VideoError):
            videos.probe(self.server.url("absent.mp4"))

    def test_video(self):
        m = self.manager()
        d = m.add_video(self.server.url("clip1.mp4"), self.opts(title="clip1"), self.out)
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 60), d.state)
        self.assertEqual(d.state, "complete", d.error)
        self.assertEqual(d.name, "clip1.mp4")
        self.assertEqual(streams(d.path), streams(os.path.join(MEDIA, "clip1.mp4")))
        self.assertEqual(sorted(os.listdir(self.out)), ["clip1.mp4"], "dossier caché retiré")
        self.assertIn(("completed", d), self.events)

    def test_audio_mp3(self):
        m = self.manager()
        d = m.add_video(self.server.url("clip2.mp4"), self.opts(mode="audio", audio_format="mp3"), self.out)
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 60), d.state)
        self.assertEqual(d.state, "complete", d.error)
        self.assertEqual(d.name, "clip2.mp3")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                                d.path], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe, ["mp3"])

    def test_never_overwrites(self):
        precious = os.path.join(self.out, "clip1.mp4")
        with open(precious, "w") as fh:
            fh.write("précieux")
        m = self.manager()
        d = m.add_video(self.server.url("clip1.mp4"), self.opts(), self.out)
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 60), d.state)
        self.assertEqual(d.state, "complete", d.error)
        self.assertEqual(d.name, "clip1 (2).mp4")
        self.assertEqual(open(precious).read(), "précieux")

    def test_playlist_subfolder(self):
        m = self.manager()
        d = m.add_video(self.server.url("page.html"), self.opts(playlist=True, title="Ma page vidéo"), self.out)
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 90), d.state)
        self.assertEqual(d.state, "complete", d.error)
        self.assertEqual(d.name, "Ma page vidéo")
        files = sorted(os.listdir(d.path))
        self.assertEqual(len(files), 2, files)
        self.assertTrue(files[0].startswith("001 - ") and files[1].startswith("002 - "), files)
        self.assertEqual(d.item_count, 2)

    def test_cancel_removes_workdir_only(self):
        self.server.set_rate(100_000)
        mine = os.path.join(self.out, "à moi.txt")
        with open(mine, "w") as fh:
            fh.write("x")
        m = self.manager()
        d = m.add_video(self.server.url("long.mp4"), self.opts(), self.out)
        self.assertTrue(wait(lambda: d.done > 50_000, 30), d.state + " " + d.error)
        w = videos.workdir(d)
        self.assertTrue(os.path.isdir(w))
        m.remove(d)
        self.assertTrue(wait(lambda: not os.path.exists(w), 10))
        self.assertEqual(os.listdir(self.out), ["à moi.txt"])

    def test_pause_resume_and_restart(self):
        """Pause (yt-dlp arrêté, .part gardé), reprise, puis Ithaca fermé et relancé : fini, intact."""
        self.server.set_rate(150_000)
        m = self.manager()
        d = m.add_video(self.server.url("long.mp4"), self.opts(), self.out)
        self.assertTrue(wait(lambda: d.done > 100_000, 30), d.state + " " + d.error)
        m.pause(d)
        self.assertTrue(wait(lambda: d.id not in m.videos.jobs, 10), "yt-dlp arrêté")
        self.assertEqual(d.state, "paused")
        parts = [f for f in os.listdir(videos.workdir(d)) if f.endswith(".part")]
        self.assertTrue(parts, "fichier partiel gardé")
        m.resume(d)
        self.assertTrue(wait(lambda: d.done > 300_000, 30))
        m.shutdown()
        self.managers.remove(m)
        self.server.set_rate(0)
        m2 = self.manager()
        d2 = m2.downloads[0]
        self.assertTrue(wait(lambda: d2.state in ("complete", "error"), 60), d2.state)
        self.assertEqual(d2.state, "complete", d2.error)
        self.assertEqual(streams(d2.path), streams(os.path.join(MEDIA, "long.mp4")), "reprise intacte")
        self.assertTrue(any(r for r in self.server.handler.requests), "relu depuis le serveur")

    def test_error(self):
        m = self.manager()
        d = m.add_video(self.server.url("absent.mp4"), self.opts(), self.out)
        self.assertTrue(wait(lambda: d.state in ("complete", "error"), 60), d.state)
        self.assertEqual(d.state, "error")
        self.assertTrue(d.error)
        self.assertFalse(os.path.exists(videos.workdir(d)) and os.listdir(videos.workdir(d)))


if __name__ == "__main__":
    unittest.main()
