"""Point d'entrée de l'application Ithaca."""

from __future__ import annotations

import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION, features  # noqa: E402
from .i18n import tr  # noqa: E402
from .settings import settings  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class IthacaApp(Adw.Application):
    """Instance unique : « ithaca fichiers… » lancé une seconde fois les envoie à la première."""

    def __init__(self, queue_file: str = ""):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             tr("Afficher la version"), None)
        self._queue_file = queue_file
        self.controller = None
        self.window = None
        self.mpris = None
        self.tray = None
        self._inhibit = 0
        self._held = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        # Mise en page seulement : les couleurs viennent du thème GTK actif.
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(DATA_DIR, "base.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        icons = os.path.join(DATA_DIR, "icons")
        if os.path.isdir(icons):
            Gtk.IconTheme.get_for_display(display).add_search_path(icons)
        from .ui.preferences import apply_theme
        apply_theme()

        from .backend.controller import Controller
        self.controller = Controller(self._queue_file)
        # Ctrl+C dans le terminal, kill, fermeture de session : on quitte proprement (file enregistrée)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (self._quit(), False)[1])
        for name, cb, accels in (("show-window", self.present, []),
                                 ("preferences", self._prefs, ["<Control>comma"]),
                                 ("about", self._about, []),
                                 ("quit", self._quit, ["<Control>q"])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self._sync_mpris()
        settings.connect(self._on_setting)

    def do_shutdown(self):
        if self.tray is not None:
            self.tray.hide()
        if self.mpris is not None:
            self.mpris.stop()
        if self.controller is not None:
            self.controller.shutdown()
        Adw.Application.do_shutdown(self)

    # -- fenêtre --------------------------------------------------------------------------------
    def _main_window(self):
        if self.window is None:
            from .ui.window import MainWindow
            self.window = MainWindow(self)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))
        return self.window

    def present(self):
        """Fenêtre au premier plan (MPRIS Raise, relance de l'application, lecture en arrière-plan)."""
        win = self._main_window()
        win.set_visible(True)
        win.present()
        if self.tray is not None:
            self.tray.hide()
        self.withdraw_notification("background")
        if self._held:
            self._held = False
            self.release()

    def go_background(self, win):
        """Fermer pendant la lecture (fonctionnalité « Lecture en arrière-plan ») : fenêtre masquée,
        application gardée en vie ; MPRIS ou un nouveau lancement la font réapparaître."""
        win.set_visible(False)
        if not self._held:
            self._held = True
            self.hold()
        # pour revenir ou quitter : icône dans la barre système et notification (choix de Tof)
        if self.get_dbus_connection() is not None:
            if self.tray is None:
                from .backend.tray import Tray
                self.tray = Tray(self.controller, self.present, self._quit)
            self.tray.show()
            n = Gio.Notification.new(tr("Ithaca continue de jouer"))
            song = self.controller.song
            if song is not None:
                from .ui.common import shown
                n.set_body(shown(" — ".join(x for x in (song.display_title, song.artist) if x)))
            n.add_button(tr("Afficher"), "app.show-window")
            n.add_button(tr("Quitter"), "app.quit")
            n.set_default_action("app.show-window")
            self.send_notification("background", n)

    def do_activate(self):
        self.present()

    def do_open(self, files, n, hint):
        paths = [f.get_path() for f in files if f.get_path()]
        self.present()
        if paths:
            self.window.add_paths(paths, play=True)

    def do_command_line(self, cmd):
        opts = cmd.get_options_dict().end().unpack()
        if opts.get("version"):
            cmd.print_literal(f"{APP_NAME} {VERSION}\n")
            return 0
        cwd = cmd.get_cwd() or os.path.expanduser("~")
        paths = []
        for a in cmd.get_arguments()[1:]:
            f = Gio.File.new_for_commandline_arg_and_cwd(a, cwd)
            if f.get_path():
                paths.append(f.get_path())
        self.present()
        if paths:
            self.window.add_paths(paths, play=True)
        return 0

    def _quit(self):
        if self.get_dbus_connection() is not None:
            self.withdraw_notification("background")
        if self.window is not None:
            self.window.destroy()
        if self._held:
            self._held = False
            self.release()
        self.quit()

    # -- mise en veille -------------------------------------------------------------------------
    def sync_inhibit(self, playing: bool):
        """Pas de mise en veille automatique pendant la lecture (comme Amberol)."""
        if playing and not self._inhibit:
            self._inhibit = self.inhibit(self.window, Gtk.ApplicationInhibitFlags.SUSPEND,
                                         tr("Lecture de musique"))
        elif not playing and self._inhibit:
            self.uninhibit(self._inhibit)
            self._inhibit = 0

    # -- MPRIS ----------------------------------------------------------------------------------
    def _sync_mpris(self):
        on = features.enabled("mpris") and self.get_dbus_connection() is not None
        if on and self.mpris is None:
            from .backend.mpris import Mpris
            self.mpris = Mpris(self.controller, self.present, self._quit)
            self.mpris.start()
        elif not on and self.mpris is not None:
            self.mpris.stop()
            self.mpris = None

    def _on_setting(self, key, _value):
        if key == "features":
            self._sync_mpris()

    # -- fenêtres annexes ------------------------------------------------------------------------
    def _prefs(self):
        from .ui.preferences import PreferencesWindow
        d = PreferencesWindow()
        d.set_transient_for(self.window)
        d.present()

    def _about(self):
        about = Adw.AboutWindow(transient_for=self.window, modal=True,
                                application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                                developer_name="Tof",
                                comments=tr("Un petit lecteur de musique pour GNOME, qui joue votre musique "
                                            "et rien d'autre."),
                                website="https://github.com/ToFpon/Ithaca",
                                issue_url="https://github.com/ToFpon/Ithaca/issues",
                                license_type=Gtk.License.GPL_3_0)
        about.add_credit_section(tr("Basé sur"), [tr("Amberol, d'Emmanuele Bassi https://gitlab.gnome.org/World/amberol")])
        # note de musique de l'icône symbolique : thème d'icônes kora (GPL-3.0)
        about.add_credit_section(tr("Icône symbolique"), [tr("kora, de bikass https://github.com/bikass/kora")])
        about.add_acknowledgement_section(tr("Moteurs"), ["GStreamer"])
        # « Nom adresse » : libadwaita en fait un lien
        about.add_acknowledgement_section(tr("Remerciements"),
                                          ["Ponce-De-Leon https://forum.zorin.com/u/Ponce-De-Leon/"])
        about.present()


def _process_name(name: str):
    """Nom du processus pour le noyau : « ithaca » dans le Moniteur système, ps, killall."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, ctypes.c_char_p(name.encode()[:15]), 0, 0, 0)     # 15 = PR_SET_NAME
    except (OSError, AttributeError):
        pass


def main(argv=None) -> int:
    _process_name("ithaca")
    import faulthandler
    faulthandler.enable(all_threads=True)
    GLib.set_prgname(APP_ID)
    app = IthacaApp()
    return app.run(argv if argv is not None else sys.argv)
