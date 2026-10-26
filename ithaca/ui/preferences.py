"""Préférences (Adw.PreferencesWindow : vraie fenêtre, le thème du système s'y applique)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..i18n import LANGUAGE_NAMES, tr  # noqa: E402
from ..settings import download_dir, settings  # noqa: E402
from .common import choose_files, choose_folder, combo_row  # noqa: E402


def apply_theme():
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(settings["theme"], Adw.ColorScheme.DEFAULT))


def _switch(title: str, key: str, subtitle: str = "") -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, active=bool(settings[key]))
    if subtitle:
        row.set_subtitle(subtitle)
    row.connect("notify::active", lambda r, *_: settings.__setitem__(key, r.get_active()))
    return row


def _choice(title: str, key: str, labels: list[str], values: list, subtitle: str = "") -> Adw.ComboRow:
    cur = settings[key]
    row = combo_row(title, labels, values.index(cur) if cur in values else 0, subtitle)
    row.connect("notify::selected", lambda r, *_: settings.__setitem__(key, values[r.get_selected()]))
    return row


def _spin(title: str, key: str, lower: int, upper: int, step: int = 1, subtitle: str = "") -> Adw.SpinRow:
    adj = Gtk.Adjustment(lower=lower, upper=upper, step_increment=step, page_increment=step * 10,
                         value=max(lower, min(upper, int(settings[key]))))
    row = Adw.SpinRow(title=title, adjustment=adj, numeric=True)
    if subtitle:
        row.set_subtitle(subtitle)
    row.connect("notify::value", lambda r, *_: settings.__setitem__(key, int(r.get_value())))
    return row


def _text(title: str, key: str, password: bool = False) -> Adw.EntryRow:
    row = (Adw.PasswordEntryRow if password else Adw.EntryRow)(title=title, text=settings[key] or "")
    row.connect("changed", lambda r: settings.__setitem__(key, r.get_text()))
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self):
        super().__init__(title=tr("Préférences"), search_enabled=True, default_width=780, default_height=680)
        self.add(self._general())
        self.add(self._downloads())
        self.add(self._videos())
        self.add(self._connection())
        self.add(self._features())

    # -- Général ---------------------------------------------------------------------
    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Général"), icon_name="preferences-system-symbolic", name="general")
        look = Adw.PreferencesGroup(title=tr("Apparence"))
        theme = _choice(tr("Style"), "theme", [tr("Comme le système"), tr("Clair"), tr("Sombre")],
                        ["system", "light", "dark"])
        theme.connect("notify::selected", lambda *_: apply_theme())
        look.add(theme)
        codes = list(LANGUAGE_NAMES)
        look.add(_choice(tr("Langue"), "language",
                         [tr("Comme le système")] + [LANGUAGE_NAMES[c] for c in codes[1:]], codes,
                         tr("Appliquée au prochain démarrage")))
        page.add(look)
        return page

    # -- Téléchargements -------------------------------------------------------------
    def _downloads(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Téléchargements"), icon_name="folder-download-symbolic",
                                   name="downloads")
        where = Adw.PreferencesGroup(title=tr("Emplacement"))
        self.folder_row = Adw.ActionRow(title=tr("Dossier de téléchargement"), subtitle=download_dir(),
                                        subtitle_selectable=True)
        browse = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Choisir le dossier"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        browse.connect("clicked", self._choose_folder)
        self.folder_row.add_suffix(browse)
        self.folder_row.set_activatable_widget(browse)
        where.add(self.folder_row)
        where.add(_switch(tr("Toujours demander où enregistrer"), "ask_dir"))
        page.add(where)

        speed = Adw.PreferencesGroup(title=tr("Vitesse"))
        speed.add(_spin(tr("Téléchargements simultanés"), "simultaneous", 1, 20,
                        subtitle=tr("Les suivants attendent leur tour")))
        speed.add(_spin(tr("Connexions par fichier"), "segments", 1, 32,
                        subtitle=tr("Plus de connexions accélèrent les serveurs lents (16 au plus par serveur)")))
        limit_on = _switch(tr("Limiter la vitesse"), "speed_limit_enabled")
        limit = _spin(tr("Vitesse maximale (Ko/s)"), "speed_limit", 10, 10_000_000, 50,
                      tr("Pour tous les téléchargements ensemble"))
        limit_on.bind_property("active", limit, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        speed.add(limit_on)
        speed.add(limit)
        page.add(speed)
        return page

    def _choose_folder(self, button):
        def done(path):
            settings["download_dir"] = path
            self.folder_row.set_subtitle(download_dir())
        choose_folder(button, tr("Dossier de téléchargement"), download_dir(), done)

    # -- Vidéos ------------------------------------------------------------------------
    def _videos(self) -> Adw.PreferencesPage:
        from ..backend import videos
        page = Adw.PreferencesPage(title=tr("Vidéos"), icon_name="camera-video-symbolic", name="videos")
        defaults = Adw.PreferencesGroup(title=tr("Choix proposés"),
                                        description=tr("Préselectionnés dans la fenêtre de choix, qui retient "
                                                       "aussi vos derniers choix"))
        defaults.add(_choice(tr("Type"), "video_mode", [tr("Vidéo"), tr("Audio seul")], ["video", "audio"]))
        heights = [0, 2160, 1440, 1080, 720, 480, 360]
        defaults.add(_choice(tr("Qualité maximale"), "video_height",
                             [tr("Meilleure")] + [f"{h}p" for h in heights[1:]], heights))
        defaults.add(_choice(tr("Format vidéo"), "video_container", ["MP4", "MKV"], list(videos.CONTAINERS),
                             tr("MP4 se lit partout ; MKV garde les pistes d'origine")))
        defaults.add(_choice(tr("Format audio"), "audio_format", ["MP3", "M4A", "Opus", "FLAC", "WAV"],
                             list(videos.AUDIO_FORMATS)))
        page.add(defaults)

        yt = Adw.PreferencesGroup(title=tr("YouTube"))
        names = ["Firefox", "Chrome", "Chromium", "Brave", "Edge", "Opera", "Vivaldi"]
        yt.add(_choice(tr("Cookies du navigateur"), "browser_cookies", [tr("Aucun")] + names,
                       [""] + list(videos.BROWSERS),
                       tr("Si YouTube demande de confirmer que vous n'êtes pas un robot : le navigateur où "
                          "vous êtes connecté à YouTube")))
        page.add(yt)

        comps = Adw.PreferencesGroup(title=tr("Composants"),
                                     description=tr("Installés dans votre dossier personnel depuis leurs pages "
                                                    "GitHub officielles, somme de contrôle vérifiée"))
        self.tool_rows = {}
        from ..backend import tools
        for tid, tool in tools.TOOLS.items():
            row = Adw.ActionRow(title=tool.name)
            self.tool_rows[tid] = row
            comps.add(row)
        self.update_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.update_btn.connect("clicked", self._update_tools)
        actions = Adw.ActionRow(title=tr("Mises à jour"))
        actions.add_suffix(self.update_btn)
        comps.add(actions)
        comps.add(_switch(tr("Mettre à jour automatiquement"), "tools_auto_update",
                          tr("Une vérification par jour ; YouTube change souvent et yt-dlp doit suivre")))
        page.add(comps)
        self._sync_tools()
        return page

    def _sync_tools(self):
        """Versions lues dans un fil (yt-dlp met environ une seconde à démarrer)."""
        import threading

        from gi.repository import GLib

        from ..backend import tools

        def work():
            found = {tid: tools.version(tid) for tid in self.tool_rows}
            GLib.idle_add(lambda: (show(found), False)[1])

        def show(found):
            for tid, row in self.tool_rows.items():
                v = found.get(tid, "")
                row.set_subtitle(tr("Version {0}").format(v) if v else tr("Non installé"))
        for row in self.tool_rows.values():
            row.set_subtitle("…")
        threading.Thread(target=work, daemon=True).start()
        missing = tools.missing()
        self.update_btn.set_label(tr("Installer") if missing else tr("Rechercher"))
        self.update_btn.set_sensitive(True)

    def _update_tools(self, button):
        from ..backend import tools
        from .tools_dialog import check_updates_async, run_install
        missing = tools.missing()
        if missing:
            run_install(self, missing, self._sync_tools)
            return
        button.set_sensitive(False)
        button.set_label(tr("Recherche…"))

        def done(ids):
            if ids:
                run_install(self, ids, self._sync_tools)
            else:
                self.add_toast(Adw.Toast.new(tr("yt-dlp et Deno sont à jour")))
            self._sync_tools()
        check_updates_async(done)

    # -- Connexion -------------------------------------------------------------------
    def _connection(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Connexion"), icon_name="network-server-symbolic", name="connection")
        auth = Adw.PreferencesGroup(title=tr("Authentification"),
                                    description=tr("Identifiants envoyés aux serveurs HTTP et FTP qui en demandent"))
        on = _switch(tr("Utiliser l'authentification"), "auth_enabled")
        user = _text(tr("Nom d'utilisateur"), "auth_user")
        password = _text(tr("Mot de passe"), "auth_password", password=True)
        for row in (user, password):
            on.bind_property("active", row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        auth.add(on)
        auth.add(user)
        auth.add(password)
        page.add(auth)

        cookies = Adw.PreferencesGroup(title=tr("Cookies"),
                                       description=tr("Fichier cookies.txt (format Netscape) exporté du "
                                                      "navigateur, pour les sites où il faut être connecté"))
        self.cookies_row = Adw.ActionRow(title=tr("Fichier de cookies"), subtitle_selectable=True)
        pick = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=tr("Choisir le fichier"),
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        pick.connect("clicked", self._choose_cookies)
        self.cookies_clear = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text=tr("Ne plus utiliser"),
                                        valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.cookies_clear.connect("clicked", lambda *_: self._set_cookies(""))
        self.cookies_row.add_suffix(self.cookies_clear)
        self.cookies_row.add_suffix(pick)
        cookies.add(self.cookies_row)
        self._set_cookies(settings["cookies_file"])
        page.add(cookies)
        return page

    def _set_cookies(self, path: str):
        settings["cookies_file"] = path
        self.cookies_row.set_subtitle(path or tr("Aucun"))
        self.cookies_clear.set_visible(bool(path))

    def _choose_cookies(self, button):
        choose_files(button, tr("Fichier de cookies"), "", lambda paths: self._set_cookies(paths[0]),
                     multiple=False, filters=[(tr("Fichiers texte"), ["*.txt"]), (tr("Tous les fichiers"), ["*"])])

    # -- Fonctionnalités ---------------------------------------------------------------
    def _features(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Fonctionnalités"), icon_name="application-x-addon-symbolic",
                                   name="features")
        group = Adw.PreferencesGroup(description=tr("Chaque fonctionnalité s'active ou se coupe tout de "
                                                    "suite, sans redémarrer Ithaca"))
        for f in features.FEATURES:
            row = Adw.SwitchRow(title=f.title, subtitle=f.description, active=features.enabled(f.id))
            row.add_prefix(Gtk.Image(icon_name=f.icon))
            missing = features.missing(f.id)
            if missing:
                row.set_subtitle(f.description + "\n" + tr("Manquant : {0}").format(", ".join(missing)))
            row.connect("notify::active", lambda r, *_, fid=f.id: features.set_enabled(fid, r.get_active()))
            group.add(row)
        page.add(group)
        return page
