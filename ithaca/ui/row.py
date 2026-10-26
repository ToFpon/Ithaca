"""Carte d'un téléchargement dans la liste (disposition de Varia)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..backend.downloads import AUTO_RETRIES, Download, http_status  # noqa: E402
from ..i18n import decimal, tr  # noqa: E402
from .common import popup_menu, shown  # noqa: E402

STATE_ICONS = {"complete": "object-select-symbolic", "error": "dialog-error-symbolic",
               "paused": "media-playback-pause-symbolic", "waiting": "content-loading-symbolic"}


# Codes de sortie d'aria2 (man aria2c, « EXIT STATUS ») : son message, en anglais, sinon
ERRORS = {"2": tr("délai dépassé"), "3": tr("fichier introuvable sur le serveur"), "6": tr("problème de réseau"),
          "8": tr("le serveur ne permet pas de reprendre"), "9": tr("plus assez d'espace sur le disque"),
          "16": tr("impossible de créer le fichier"), "17": tr("erreur d'écriture du fichier"),
          "18": tr("impossible de créer le dossier"), "19": tr("serveur introuvable (nom de domaine)"),
          "22": tr("réponse du serveur inattendue"), "23": tr("trop de redirections"),
          "24": tr("identifiants refusés par le serveur"), "29": tr("serveur surchargé, réessayez plus tard"),
          "32": tr("somme de contrôle incorrecte")}


def error_text(d: Download) -> str:
    if http_status(d.error) == 429:
        return tr("le serveur refuse trop de requêtes, réessayez dans quelques minutes")
    return ERRORS.get(d.error_code, d.error)


def status_text(d: Download) -> str:
    """Seconde ligne de la carte : où en est le téléchargement."""
    if d.state == "complete":
        return tr("{0} — Terminé").format(fmt.size(d.total or d.done))
    if d.state == "error":
        reason = error_text(d)
        return tr("Échec : {0}").format(reason) if reason else tr("Échec")
    progress = (tr("{0} sur {1}").format(fmt.size(d.done), fmt.size(d.total)) if d.total
                else fmt.size(d.done))
    if d.state == "paused":
        return tr("En pause — {0}").format(progress) if d.done else tr("En pause")
    if d.state == "waiting" and d.auto_retries:
        return tr("Nouvelle tentative ({0}/{1})…").format(d.auto_retries, AUTO_RETRIES)
    if d.state == "waiting":
        return tr("En attente — {0}").format(progress) if d.done else tr("En attente")
    parts = [progress, fmt.speed(d.speed)]
    if d.eta >= 0:
        parts.append(tr("{0} restantes").format(fmt.duration(d.eta)))
    return " — ".join(parts)


def percent_text(d: Download) -> str:
    return tr("{0} %").format(decimal(d.fraction * 100, 0)) if d.total or d.state == "complete" else ""


class DownloadRow(Gtk.ListBoxRow):
    def __init__(self, d: Download):
        super().__init__(activatable=False, selectable=False)
        self.download = d
        self.card = card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                   css_classes=["card", "download-card"])
        top = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        line = Gtk.Box(spacing=6)
        self.spinner = Gtk.Spinner()
        self.icon = Gtk.Image(css_classes=["dim-label", "state-icon"])
        self.percent = Gtk.Label(css_classes=["dim-label", "numeric"])
        self.name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE,
                              css_classes=["download-name"])
        for w in (self.spinner, self.icon, self.percent, self.name):
            line.append(w)
        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "numeric"])
        text.append(line)
        text.append(self.status)
        top.append(text)

        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.info_btn = Gtk.Button(icon_name="help-about-symbolic", tooltip_text=tr("Détails du téléchargement"),
                                   css_classes=["circular", "flat"], valign=Gtk.Align.CENTER)
        self.info_btn.set_action_name("win.dl-details")
        self.main_btn = Gtk.Button(css_classes=["circular"], valign=Gtk.Align.CENTER)
        self.close_btn = Gtk.Button(css_classes=["circular"], valign=Gtk.Align.CENTER)
        for b in (self.info_btn, self.main_btn, self.close_btn):
            b.set_action_target_value(GLib.Variant.new_string(d.id))
            buttons.append(b)
        top.append(buttons)
        card.append(top)
        self.bar = Gtk.ProgressBar(css_classes=["download-progress"])
        card.append(self.bar)
        self.set_child(card)

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_click)
        self.add_controller(click)
        self.update()

    def update(self):
        d = self.download
        self.name.set_label(shown(d.display_name))
        self.set_tooltip_text(shown(d.path or d.url))
        self.status.set_label(shown(status_text(d)))
        self.percent.set_label(percent_text(d))
        self.percent.set_visible(bool(self.percent.get_label()) and d.state != "complete")
        running = d.state == "active"
        self.spinner.set_visible(running)
        self.spinner.set_spinning(running)
        self.icon.set_visible(not running)
        self.icon.set_from_icon_name(STATE_ICONS.get(d.state, "content-loading-symbolic"))
        self.bar.set_fraction(d.fraction)
        self.bar.set_visible(d.state not in ("complete", "error"))
        for cls, on in (("error", d.state == "error"), ("complete", d.state == "complete")):
            (self.card.add_css_class if on else self.card.remove_css_class)(cls)

        if d.state == "complete":
            main = ("document-open-symbolic", tr("Ouvrir"), "win.dl-open")
            close = ("window-close-symbolic", tr("Retirer de la liste"), "win.dl-remove", False)
        elif d.state == "error":
            main = ("view-refresh-symbolic", tr("Réessayer"), "win.dl-retry")
            close = ("window-close-symbolic", tr("Retirer de la liste"), "win.dl-remove", False)
        elif d.state == "paused":
            main = ("media-playback-start-symbolic", tr("Reprendre"), "win.dl-resume")
            close = ("media-playback-stop-symbolic", tr("Annuler le téléchargement"), "win.dl-remove", True)
        else:
            main = ("media-playback-pause-symbolic", tr("Mettre en pause"), "win.dl-pause")
            close = ("media-playback-stop-symbolic", tr("Annuler le téléchargement"), "win.dl-remove", True)
        self.main_btn.set_icon_name(main[0])
        self.main_btn.set_tooltip_text(main[1])
        self.main_btn.set_action_name(main[2])
        self.close_btn.set_icon_name(close[0])
        self.close_btn.set_tooltip_text(close[1])
        self.close_btn.set_action_name(close[2])
        (self.close_btn.add_css_class if close[3] else self.close_btn.remove_css_class)("destructive-action")
        (self.close_btn.remove_css_class if close[3] else self.close_btn.add_css_class)("flat")

    # -- souris ------------------------------------------------------------------
    def _on_click(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        win = self.get_root()
        target = GLib.Variant.new_string(self.download.id)
        if button == 3 and n_press == 1:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            popup_menu(self, self.menu(), x, y)
        elif button == 1 and n_press == 2 and self.download.state == "complete" and win is not None:
            win.activate_action("win.dl-open", target)

    def menu(self) -> Gio.Menu:
        d = self.download
        did = GLib.Variant.new_string(d.id)

        def item(label, action):
            it = Gio.MenuItem.new(label, None)
            it.set_action_and_target_value(action, did)
            return it
        menu = Gio.Menu()
        first = Gio.Menu()
        if d.state == "complete":
            first.append_item(item(tr("Ouvrir"), "win.dl-open"))
        if d.name:
            first.append_item(item(tr("Afficher dans le dossier"), "win.dl-show"))
        if d.state in ("waiting", "active"):
            first.append_item(item(tr("Mettre en pause"), "win.dl-pause"))
        elif d.state == "paused":
            first.append_item(item(tr("Reprendre"), "win.dl-resume"))
        elif d.state == "error":
            first.append_item(item(tr("Réessayer"), "win.dl-retry"))
        menu.append_section(None, first)
        second = Gio.Menu()
        second.append_item(item(tr("Copier l'adresse"), "win.dl-copy-url"))
        second.append_item(item(tr("Détails"), "win.dl-details"))
        menu.append_section(None, second)
        third = Gio.Menu()
        if d.state == "complete":
            third.append_item(item(tr("Retirer de la liste"), "win.dl-remove"))
            third.append_item(item(tr("Mettre à la corbeille"), "win.dl-trash"))
        else:
            third.append_item(item(tr("Annuler le téléchargement"), "win.dl-remove"))
        menu.append_section(None, third)
        return menu
