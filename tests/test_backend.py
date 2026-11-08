"""Moteur (sans GTK) : étiquettes, pochettes, parcours des dossiers, file de lecture, lecteur
GStreamer (sortie factice synchronisée), enchaînement, ReplayGain, forme d'onde, persistance.

    python3.12 -m unittest discover -s tests
"""

import os
import random
import shutil
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp(prefix="ithaca-test-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gi.repository import GLib  # noqa: E402

import media  # noqa: E402
from ithaca.backend import player as player_mod  # noqa: E402
from ithaca.backend import song as song_mod  # noqa: E402
from ithaca.backend.controller import Controller  # noqa: E402
from ithaca.backend.playqueue import PlayQueue, load_saved  # noqa: E402
from ithaca.backend.waveform import WaveformGenerator, cache_path  # noqa: E402
from ithaca.settings import settings  # noqa: E402

LIB = {}


def setUpModule():
    LIB.update(media.library(os.path.join(TMP, "lib")))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def wait(cond, timeout=15.0) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def load(paths):
    out = []
    done = []
    song_mod.load_paths(paths, out.extend, lambda r, s: done.append((r, s)))
    assert wait(lambda: done, 30)
    return out, done[0]


class SongTest(unittest.TestCase):
    def test_tags_and_embedded_cover(self):
        s = song_mod.read_song(LIB["a1"])
        self.assertEqual((s.title, s.artist, s.album, s.track), ("Ouverture", "Les Testeurs", "Premier album", 1))
        self.assertAlmostEqual(s.duration, 2.0, delta=0.15)
        self.assertTrue(s.cover.startswith(song_mod.COVERS_DIR) and os.path.getsize(s.cover) > 1000)
        self.assertEqual(s.track_gain, -6.0)
        self.assertEqual(s.album_gain, -3.0)
        s2 = song_mod.read_song(LIB["a2"])
        self.assertEqual(s.cover, s2.cover, "même pochette : un seul fichier en cache")

    def test_folder_cover(self):
        s = song_mod.read_song(LIB["b1"])
        self.assertEqual(s.cover, os.path.join(LIB["album_b"], "cover.jpg"))
        self.assertEqual(s.title, "Matin")

    def test_untagged(self):
        s = song_mod.read_song(LIB["bare"])
        self.assertEqual(s.title, "")
        self.assertEqual(s.display_title, "sans étiquette")

    def test_not_audio(self):
        self.assertIsNone(song_mod.read_song(LIB["fake"]))

    def test_expand_natural_order(self):
        paths = song_mod.expand([LIB["album_a"]])
        names = [os.path.basename(p) for p in paths]
        self.assertEqual(names, ["01 - Ouverture.mp3", "02 - Traversée.mp3", "10 - Retour.mp3"])
        everything = song_mod.expand([LIB["root"]])
        self.assertNotIn(os.path.join(LIB["root"], "notes.txt"), everything)

    def test_load_paths_skips_unreadable(self):
        songs, (read, skipped) = load([LIB["root"]])
        self.assertEqual(read, 6)
        self.assertEqual(skipped, 1)
        self.assertNotIn(LIB["fake"], [s.path for s in songs])


def fake_songs(n):
    return [song_mod.Song(f"/x/{i}.mp3", title=str(i), duration=10) for i in range(n)]


class QueueTest(unittest.TestCase):
    def test_next_previous_repeat(self):
        q = PlayQueue()
        q.add(fake_songs(3))
        q.set_current(0)
        self.assertEqual(q.next_index(), 1)
        q.set_current(2)
        self.assertIsNone(q.next_index(), "fin de la file")
        q.set_repeat("all")
        self.assertEqual(q.next_index(), 0)
        q.set_repeat("one")
        self.assertEqual(q.next_index(), 2)
        q.set_current(0)
        q.set_repeat("all")
        self.assertEqual(q.previous_index(), 2)
        q.set_repeat("consecutive")
        self.assertEqual(q.previous_index(), 0)

    def test_no_duplicates(self):
        q = PlayQueue()
        songs = fake_songs(3)
        q.add(songs)
        self.assertEqual(q.add([songs[0]]), [])
        self.assertEqual(len(q), 3)

    def test_shuffle_keeps_current_first_and_unshuffle_restores(self):
        q = PlayQueue(random.Random(4))
        songs = fake_songs(20)
        q.add(songs)
        q.set_current(7)
        q.set_shuffled(True)
        self.assertIs(q.current, songs[7])
        self.assertEqual(q.pos, 0)
        self.assertEqual(sorted(s.title for s in q.items), sorted(s.title for s in songs))
        self.assertNotEqual(q.items[1:], songs[:7] + songs[8:])
        q.set_shuffled(False)
        self.assertEqual(q.items, songs)
        self.assertEqual(q.pos, 7)

    def test_remove_current_and_others(self):
        q = PlayQueue()
        s = fake_songs(5)
        q.add(s)
        q.set_current(2)
        q.remove([s[0]])
        self.assertIs(q.current, s[2])
        self.assertEqual(q.pos, 1)
        q.remove([s[2]])
        self.assertIs(q.current, s[3], "le suivant prend la place")
        q.remove([s[3], s[4]])
        self.assertIs(q.current, s[1])
        q.remove([s[1]])
        self.assertIsNone(q.current)

    def test_move(self):
        q = PlayQueue()
        s = fake_songs(4)
        q.add(s)
        q.set_current(1)
        q.move(s[3], 0)
        self.assertEqual(q.items, [s[3], s[0], s[1], s[2]])
        self.assertIs(q.current, s[1])


class ReplayGainTest(unittest.TestCase):
    def test_factor(self):
        s = song_mod.Song("/x", track_gain=-6.0, album_gain=-3.0, track_peak=0.5)
        self.assertAlmostEqual(player_mod.replaygain_factor(s, "track"), 10 ** (-6 / 20), places=4)
        self.assertAlmostEqual(player_mod.replaygain_factor(s, "album"), 10 ** (-3 / 20), places=4)
        self.assertEqual(player_mod.replaygain_factor(s, "off"), 1.0)
        loud = song_mod.Song("/x", track_gain=+12.0, track_peak=0.8)
        self.assertAlmostEqual(player_mod.replaygain_factor(loud, "track"), 1 / 0.8, places=4,
                               msg="la crête limite le gain : pas de saturation")
        only_album = song_mod.Song("/x", album_gain=-2.0)
        self.assertAlmostEqual(player_mod.replaygain_factor(only_album, "track"), 10 ** (-2 / 20), places=4)
        self.assertEqual(player_mod.replaygain_factor(song_mod.Song("/x"), "album"), 1.0)


class PlayerTest(unittest.TestCase):
    def setUp(self):
        self.qfile = os.path.join(tempfile.mkdtemp(dir=TMP), "queue.json")
        settings.data.update({"repeat": "consecutive", "shuffle": False, "replaygain": "album", "volume": 1.0})
        self.c = Controller(self.qfile)
        self.events = []
        self.c.connect(lambda ev, *a: self.events.append((ev, a)))

    def tearDown(self):
        self.c.shutdown()

    def add(self, paths, play=False):
        done = []
        self.c.add_paths(paths, play=play, on_done=lambda r, s: done.append(1))
        self.assertTrue(wait(lambda: done, 30))

    def test_plays_through_album_gapless(self):
        """Lecture de l'album entier, enchaînée sans arrêt (about-to-finish), jusqu'à la fin."""
        self.add([LIB["album_a"]], play=True)
        self.assertTrue(wait(lambda: self.c.playing, 5))
        started = time.monotonic()
        self.assertTrue(wait(lambda: self.c.queue.pos == 2, 15), "troisième morceau atteint")
        self.assertTrue(wait(lambda: ("ended", ()) in self.events, 10), "fin de la file")
        self.assertFalse(self.c.playing)
        self.assertLess(time.monotonic() - started, 8.0)
        self.assertNotIn("error", [e for e, _ in self.events])

    def test_pause_seek_next_previous(self):
        self.add([LIB["album_a"]], play=True)
        self.assertTrue(wait(lambda: self.c.player.position() > 0.3, 5))
        self.c.pause()
        pos = self.c.player.position()
        time.sleep(0.4)
        wait(lambda: False, 0.2)
        self.assertAlmostEqual(self.c.player.position(), pos, delta=0.05)
        self.c.seek(1.5)
        self.assertTrue(wait(lambda: abs(self.c.player.position() - 1.5) < 0.2, 3))
        self.c.next()
        self.assertEqual(self.c.queue.pos, 1)
        self.assertFalse(self.c.playing, "en pause, Suivant reste en pause")
        self.c.previous()
        self.assertEqual(self.c.queue.pos, 0)

    def test_previous_restarts_song(self):
        self.add([LIB["album_a"]])
        self.c.play_index(1)
        self.c.player.seek(1.0)
        wait(lambda: False, 0.3)
        self.c.player.on_position(0)
        # au-delà de 3 s seulement : ici on reste sur le morceau précédent
        self.c.previous()
        self.assertEqual(self.c.queue.pos, 0)

    def test_repeat_one_loops(self):
        settings["repeat"] = "one"
        self.c.queue.set_repeat("one")
        self.add([LIB["b1"]], play=True)
        starts = []
        self.c.player.on_song_started = lambda s: (starts.append(s), self.c._on_song_started(s))
        self.assertTrue(wait(lambda: len(starts) >= 1, 8), "le morceau recommence")
        self.assertTrue(self.c.playing)

    def test_unreadable_is_skipped(self):
        self.add([LIB["b1"], LIB["b2"]])
        bad = self.c.queue.items[0]
        os.rename(bad.path, bad.path + ".moved")
        try:
            self.c.play_index(0)
            self.assertTrue(wait(lambda: any(e == "error" for e, _ in self.events), 5))
            self.assertTrue(wait(lambda: self.c.queue.pos == 1 and self.c.playing, 5), "morceau suivant joué")
        finally:
            os.rename(bad.path + ".moved", bad.path)

    def test_replaygain_applied_live(self):
        self.add([LIB["a1"]], play=True)
        self.assertTrue(wait(lambda: self.c.playing, 5))
        rg = self.c.player.rg
        self.assertAlmostEqual(rg.get_property("volume"), 10 ** (-3 / 20), places=3)
        settings["replaygain"] = "track"
        self.assertAlmostEqual(rg.get_property("volume"), 10 ** (-6 / 20), places=3)
        settings["replaygain"] = "off"
        self.assertEqual(rg.get_property("volume"), 1.0)
        settings["replaygain"] = "album"

    def test_volume(self):
        self.c.set_volume(0.5)
        self.assertAlmostEqual(self.c.player.playbin.get_property("volume"), 0.125, places=3)
        self.c.set_muted(True)
        self.assertTrue(self.c.player.playbin.get_property("mute"))
        self.c.set_muted(False)
        self.c.set_volume(1.0)

    def test_save_and_restore(self):
        self.add([LIB["album_a"], LIB["album_b"]])
        self.c.set_shuffled(True)
        self.c.play_index(3)
        self.c.seek(1.0)
        wait(lambda: abs(self.c.player.position() - 1.0) < 0.2, 3)
        current = self.c.song.path
        order = [s.path for s in self.c.queue.items]
        self.c.save()
        self.c.shutdown()
        data = load_saved(self.qfile)
        self.assertEqual(data["current"], current)
        c2 = Controller(self.qfile)
        done = []
        self.assertTrue(c2.restore_saved(lambda: done.append(1)))
        self.assertTrue(wait(lambda: done, 20))
        try:
            self.assertEqual([s.path for s in c2.queue.items], order, "ordre mélangé retrouvé")
            self.assertEqual(c2.song.path, current)
            self.assertTrue(c2.queue.shuffled)
            self.assertFalse(c2.playing, "restauré en pause")
            self.assertTrue(wait(lambda: abs(c2.player.position() - 1.0) < 0.3, 5), c2.player.position())
        finally:
            c2.shutdown()
        self.c = Controller(self.qfile)          # pour tearDown


class WaveformTest(unittest.TestCase):
    def test_generate_and_cache(self):
        s = song_mod.read_song(LIB["b1"])
        got = []
        gen = WaveformGenerator()
        gen.request(s, lambda song, peaks: got.append(peaks))
        self.assertTrue(wait(lambda: got, 15))
        peaks = got[0]
        self.assertIsNotNone(peaks)
        self.assertGreaterEqual(len(peaks), 7, "une crête par quart de seconde (2 s)")
        self.assertTrue(all(0 <= l <= 1 and 0 <= r <= 1 for l, r in peaks), peaks[:3])
        self.assertAlmostEqual(max(l for l, _r in peaks), 0.125, delta=0.01, msg="sinus de ffmpeg : -18 dB")
        self.assertTrue(os.path.exists(cache_path(s)))
        got.clear()
        gen.request(s, lambda song, peaks: got.append(peaks))
        self.assertEqual(got, [peaks], "relu du cache, tout de suite")

    def test_cancel_and_error(self):
        gen = WaveformGenerator()
        got = []
        gen.request(song_mod.read_song(LIB["a1"]), lambda *a: got.append("a"))
        gen.request(song_mod.Song(LIB["fake"]), lambda song, peaks: got.append(peaks))
        self.assertTrue(wait(lambda: got, 10))
        self.assertEqual(got, [None], "le premier calcul est abandonné, le second échoue proprement")


if __name__ == "__main__":
    unittest.main()
