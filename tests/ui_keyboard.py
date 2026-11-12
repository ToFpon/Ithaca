#!/usr/bin/env python3
"""Fenêtre des raccourcis par de vrais claviers simulés (setxkbmap + xdotool), dispositions us, de, fr
(repris de Nausicaa).

    xvfb-run -a python3.12 tests/ui_keyboard.py        # doit finir par « Aucune erreur. »

Ctrl+? : « ? » est Maj+/ en QWERTY, Maj+ß en QWERTZ, Maj+, en AZERTY. Dans Ithaca ≤ 1.3.1, Ctrl+? n'était
branché nulle part (GTK ne l'attache seul qu'à un help-overlay.ui des ressources) : retour de Tof. Vérifié
avec le raccourci de GTK ET, raccourci réduit à F1, avec le filet de la fenêtre seul (MainWindow._help_key).
F1 aussi.
"""
import os
import shutil
import subprocess
import sys
import tempfile

CASES = [("us", "ctrl+shift+slash"), ("de", "ctrl+shift+ssharp"), ("fr", "ctrl+shift+comma"),
         ("us", "F1"), ("fr", "F1")]


def one(layout: str, keys: str, no_accel: bool) -> bool:
    """Un lancement d'Ithaca : la touche ouvre-t-elle la fenêtre des raccourcis ?"""
    tmp = tempfile.mkdtemp(prefix="ithaca-kb-")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.environ[var] = os.path.join(tmp, var)
    os.environ["ITHACA_AUDIO_SINK"] = "fakesink sync=true"
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    from ithaca.app import IthacaApp

    subprocess.run(["setxkbmap", layout], check=True)
    app = IthacaApp(os.path.join(tmp, "queue.json"))
    result = {}

    def check():
        result["ok"] = any(isinstance(w, Gtk.ShortcutsWindow) and w.get_visible()
                           for w in Gtk.Window.list_toplevels())
        app._quit()
        return False

    def press():
        subprocess.run(["xdotool", "key", keys], check=True, timeout=10)
        GLib.timeout_add(800, check)
        return False

    def go(_app, _win):
        if result.get("started"):
            return
        result["started"] = True
        if no_accel:                     # GTK qui ne reconnaît pas Ctrl+? : seul le filet reste
            app.set_accels_for_action("win.show-help-overlay", ["F1"])
        GLib.timeout_add(1500, press)

    app.connect("window-added", go)
    app.run([sys.argv[0]])
    shutil.rmtree(tmp, ignore_errors=True)
    return bool(result.get("ok"))


if __name__ == "__main__":
    if len(sys.argv) == 4:                                       # un cas, dans son propre processus
        sys.exit(0 if one(sys.argv[1], sys.argv[2], sys.argv[3] == "1") else 1)
    if not (os.environ.get("DISPLAY") and shutil.which("xdotool") and shutil.which("setxkbmap")):
        print("(pas de DISPLAY, xdotool ou setxkbmap : test sauté)")
        sys.exit(0)
    errors = []
    for layout, keys in CASES:
        modes = (False, True) if keys.startswith("ctrl") else (False,)
        for no_accel in modes:
            rc = subprocess.run([sys.executable, os.path.abspath(__file__), layout, keys, "1" if no_accel else "0"],
                                capture_output=True, timeout=60).returncode
            label = f"{layout} {keys}" + (" (filet seul)" if no_accel else "")
            print(("✓ " if rc == 0 else "✗ ") + label + " : fenêtre des raccourcis", flush=True)
            if rc != 0:
                errors.append("ÉCHEC : " + label)
    subprocess.run(["setxkbmap", "us"])
    print("\n".join(errors) if errors else "\nAucune erreur.")
    sys.exit(1 if errors else 0)
