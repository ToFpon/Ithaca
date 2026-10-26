"""Point d'entrée de l'application Ithaca."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION, features  # noqa: E402
from .i18n import tr  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class IthacaApp(Adw.Application):
    """Instance unique (GApplication) : « ithaca URL » lancé une seconde fois envoie le lien à la
    première, sans socket ni port ouvert."""

    def __init__(self, state_file: str = ""):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             tr("Afficher la version"), None)
        self._state_file = state_file
        self.manager = None
        self.window = None

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

        from .backend.downloads import Manager
        self.manager = Manager(self._state_file) if self._state_file else Manager()
        self.manager.connect(self._on_download_event)
        self.manager.start()
        GLib.timeout_add_seconds(20, self._auto_update_tools)

        for name, cb, accels in (("show-window", self.activate, []),
                                 ("preferences", self._prefs, ["<Ctrl>comma"]),
                                 ("about", self._about, []),
                                 ("quit", self._quit, ["<Ctrl>q"])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("win.focus-url", ["<Ctrl>l"])
        # actions des notifications (l'identifiant du téléchargement en paramètre)
        for name in ("open-download", "show-download"):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", self._on_notification_action, name)
            self.add_action(a)

    def _auto_update_tools(self):
        """yt-dlp et Deno : une vérification par jour au plus, mise à jour silencieuse."""
        import time

        from .backend import tools
        from .settings import settings
        if (not settings["tools_auto_update"] or not features.enabled("videos")
                or not any(tools.installed(t) for t in tools.TOOLS)
                or time.time() - settings["tools_last_check"] < 86400):
            return False
        settings["tools_last_check"] = int(time.time())
        from .ui.tools_dialog import check_updates_async, update_silently
        check_updates_async(lambda ids: ids and update_silently(ids))
        return False

    def do_shutdown(self):
        if self.manager is not None:
            self.manager.shutdown()
        Adw.Application.do_shutdown(self)

    def _main_window(self):
        if self.window is None:
            from .ui.window import MainWindow
            self.window = MainWindow(self)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))
        return self.window

    def do_activate(self):
        self._main_window().present()

    def do_open(self, files, n, hint):
        """Activation D-Bus (.desktop en DBusActivatable, « gio open ») : liens reçus comme fichiers."""
        win = self._main_window()
        win.add_urls([f.get_uri() for f in files])
        win.present()

    def do_command_line(self, cmd):
        opts = cmd.get_options_dict().end().unpack()
        if opts.get("version"):
            cmd.print_literal(f"{APP_NAME} {VERSION}\n")
            return 0
        win = self._main_window()
        urls = [a for a in cmd.get_arguments()[1:] if a]
        if urls:
            win.add_urls(urls)
        win.present()
        return 0

    def _quit(self):
        if self.window is not None:
            self.window.close()
        else:
            self.quit()

    def _prefs(self):
        from .ui.preferences import PreferencesWindow
        d = PreferencesWindow()
        d.set_transient_for(self.window)
        d.present()

    def _about(self):
        about = Adw.AboutWindow(transient_for=self.window, modal=True,
                                application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                                developer_name="Tof",
                                comments=tr("Gestionnaire de téléchargements pour GNOME : fichiers, "
                                            "vidéos et torrents."),
                                website="https://github.com/ToFpon/Ithaca",
                                issue_url="https://github.com/ToFpon/Ithaca/issues",
                                license_type=Gtk.License.GPL_3_0)
        about.add_credit_section(tr("Basé sur"), [tr("Varia, de Giant Pink Robots https://github.com/giantpinkrobots/varia")])
        about.add_acknowledgement_section(tr("Moteurs"), ["aria2", "yt-dlp", "Deno", "FFmpeg"])
        about.present()

    # -- notifications -----------------------------------------------------------------
    def _on_download_event(self, event, d):
        if event not in ("completed", "failed") or not features.enabled("notifications"):
            return
        if self.get_dbus_connection() is None:
            return                       # pas de bus de session (xvfb) : notifications impossibles
        win = self.window
        if win is not None and win.is_active():
            return                       # la fenêtre est sous les yeux : la carte suffit
        from .ui.common import shown
        if event == "completed":
            n = Gio.Notification.new(tr("Téléchargement terminé"))
            n.set_body(shown(d.display_name))
            n.add_button_with_target(tr("Afficher dans le dossier"), "app.show-download",
                                     GLib.Variant.new_string(d.id))
            n.set_default_action_and_target("app.open-download", GLib.Variant.new_string(d.id))
        else:
            n = Gio.Notification.new(tr("Échec du téléchargement"))
            from .ui.row import error_text
            reason = error_text(d)
            n.set_body(shown(d.display_name + (" — " + reason if reason else "")))
            n.set_default_action("app.show-window")
        self.send_notification("download-" + d.id, n)

    def _on_notification_action(self, _action, param, name):
        win = self._main_window()
        win.present()
        win.activate_action("win.dl-open" if name == "open-download" else "win.dl-show", param)


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
