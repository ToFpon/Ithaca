"""Lecteur de musique par défaut (backend/defaultapp.py) sur un dossier personnel jetable : un faux
lecteur installé (« fakeplayer », avant Ithaca), activer → Ithaca pour tous les types audio, dossiers et
listes de lecture intacts ; couper → le faux lecteur revient. Joué dans un processus à part : GIO lit
les dossiers XDG une fois pour toutes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = textwrap.dedent("""
    import sys
    from gi.repository import Gio
    from ithaca.backend import defaultapp
    from ithaca.settings import settings

    def default(mime):
        app = Gio.AppInfo.get_default_for_type(mime, False)
        return app.get_id() if app else ""

    step = sys.argv[1]
    if step == "check-before":
        assert defaultapp.installed()
        assert not defaultapp.is_default()
        assert default("audio/mpeg") == "fakeplayer.desktop", default("audio/mpeg")
    elif step == "on":
        assert defaultapp.set_default(True)
        assert defaultapp.is_default()
        assert settings["previous_audio_apps"].get("audio/flac") == "fakeplayer.desktop"
    elif step == "check-on":                    # relu par un autre processus : écrit dans mimeapps.list
        assert defaultapp.is_default()
        for mime in defaultapp.AUDIO_TYPES:
            assert default(mime) == "io.github.ithaca.Ithaca.desktop", mime
        assert default("inode/directory") == "fakefiles.desktop", "dossiers : jamais touchés"
        assert default("audio/x-mpegurl") == "fakeplayer.desktop", "listes de lecture : jamais touchées"
    elif step == "off":
        assert defaultapp.set_default(False)
        assert not defaultapp.is_default()
        assert settings["previous_audio_apps"] == {}
    elif step == "check-off":
        for mime in defaultapp.AUDIO_TYPES:
            assert default(mime) == "fakeplayer.desktop", (mime, default(mime))
        assert default("inode/directory") == "fakefiles.desktop"
    print("ok", step)
""")

DESKTOP = """[Desktop Entry]
Type=Application
Name={name}
Exec=true %U
MimeType={mimes}
"""


class DefaultPlayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ithaca-defaultapp-")
        self.config = os.path.join(self.tmp, "config")
        apps = os.path.join(self.tmp, "data", "applications")
        os.makedirs(apps)
        os.makedirs(self.config)
        sys.path.insert(0, ROOT)
        from ithaca.backend import defaultapp
        # base MIME avec des alias, comme shared-mime-info d'Ubuntu 24.04 : GIO cherche l'application par
        # défaut sous le nom CANONIQUE (retour de Tof : interrupteur éteint à la réouverture)
        canonical = {"audio/x-wav": "audio/vnd.wave", "audio/x-flac": "audio/flac", "audio/x-m4a": "audio/mp4"}
        os.makedirs(os.path.join(self.tmp, "sys", "mime"))
        with open(os.path.join(self.tmp, "sys", "mime", "aliases"), "w", encoding="utf-8") as fh:
            fh.write("".join(f"{a} {c}\n" for a, c in canonical.items()))
        audio = ";".join(defaultapp.AUDIO_TYPES) + ";audio/vnd.wave;audio/x-mpegurl;"
        files = {"fakeplayer.desktop": DESKTOP.format(name="Fake player", mimes=audio),
                 "fakefiles.desktop": DESKTOP.format(name="Fake files", mimes="inode/directory;"),
                 "io.github.ithaca.Ithaca.desktop": DESKTOP.format(name="Ithaca", mimes=audio + "inode/directory;")}
        for name, text in files.items():
            with open(os.path.join(apps, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(self.config, "mimeapps.list"), "w", encoding="utf-8") as fh:
            fh.write("[Default Applications]\ninode/directory=fakefiles.desktop\n"
                     + "".join(f"{canonical.get(m, m)}=fakeplayer.desktop\n" for m in defaultapp.AUDIO_TYPES)
                     + "audio/x-mpegurl=fakeplayer.desktop\n")
        # Zorin : « <bureau>-mimeapps.list » est lu AVANT mimeapps.list (leçon de Nausicaa / Odysseus) ;
        # le dossier jetable n'est pas une vraie session : XDG_CURRENT_DESKTOP posé à la main
        with open(os.path.join(self.config, "zorin-mimeapps.list"), "w", encoding="utf-8") as fh:
            fh.write("[Default Applications]\naudio/mpeg=fakeplayer.desktop\naudio/vnd.wave=fakeplayer.desktop\n"
                     "inode/directory=fakefiles.desktop\n")
        self.env = dict(os.environ, XDG_CONFIG_HOME=self.config, XDG_DATA_HOME=os.path.join(self.tmp, "data"),
                        XDG_DATA_DIRS=os.path.join(self.tmp, "sys"), XDG_CONFIG_DIRS=os.path.join(self.tmp, "no"),
                        HOME=self.tmp, PYTHONPATH=ROOT, LANGUAGE="fr", XDG_CURRENT_DESKTOP="zorin:GNOME")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_step(self, step):
        r = subprocess.run([sys.executable, "-c", SCRIPT, step], env=self.env, capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_types_match_desktop_file(self):
        """Tous les types audio du vrai .desktop, aucun type de liste de lecture ni de dossier."""
        from ithaca.backend import defaultapp
        with open(os.path.join(ROOT, "data", "io.github.ithaca.Ithaca.desktop"), encoding="utf-8") as fh:
            line = next(l for l in fh if l.startswith("MimeType="))
        declared = [m for m in line.strip()[len("MimeType="):].split(";") if m]
        playlists = {"audio/x-mpegurl", "audio/mpegurl", "application/xspf+xml", "audio/x-scpls"}
        self.assertEqual(set(defaultapp.AUDIO_TYPES),
                         {m for m in declared if m.startswith("audio/") and m not in playlists})

    def test_on_then_off(self):
        for step in ("check-before", "on", "check-on", "off", "check-off"):
            self.run_step(step)

    def test_previous_player_uninstalled(self):
        """Ancien lecteur désinstallé entre-temps : notre association est retirée (le système choisit)."""
        self.run_step("on")
        os.remove(os.path.join(self.tmp, "data", "applications", "fakeplayer.desktop"))
        self.run_step("off")
        with open(os.path.join(self.config, "mimeapps.list"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("audio/mpeg=io.github.ithaca.Ithaca.desktop", text)
        self.assertIn("inode/directory=fakefiles.desktop", text)


if __name__ == "__main__":
    unittest.main()
