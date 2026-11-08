"""Icône dans la barre système (pendant la lecture en arrière-plan) : protocole StatusNotifierItem,
celui des « AppIndicator » que Zorin / Ubuntu affichent (extension AppIndicator de GNOME Shell).

libayatana-appindicator est en GTK 3 et ne peut pas cohabiter avec GTK 4 dans le même processus :
on parle D-Bus directement. L'icône : org.kde.StatusNotifierItem ; son menu : com.canonical.dbusmenu
(GetLayout, Event…). L'icône s'enregistre auprès de org.kde.StatusNotifierWatcher ; s'il n'y en a pas
(pas d'extension), rien ne s'affiche et rien ne casse.
"""

from __future__ import annotations

import os
from typing import Callable

from gi.repository import Gio, GLib

from .. import APP_ID, APP_NAME
from ..i18n import tr

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"
ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "data", "icons")
# Icône EN COULEUR (disque violet, triangle « lecture ») : la symbolique (note sur des vagues) était
# illisible à 16 px, et une icône symbolique est recolorée d'une seule teinte (adieu le violet).
ICON_NAME = APP_ID + "-tray"


def icon_theme_path() -> str:
    """Dossier d'icônes à annoncer : vide si l'icône est installée dans le thème du système (paquet,
    install.sh), la barre la cherche alors comme les autres ; sinon data/icons (lancé sans installer).
    Sans index.theme dans ce dossier, l'extension AppIndicator ne trouvait rien et montrait son icône
    d'attente (« ••• »)."""
    for base in [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]:
        if os.path.exists(os.path.join(base, "icons", "hicolor", "scalable", "apps", ICON_NAME + ".svg")):
            return ""
    return ICONS_DIR

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/><property name="IconThemePath" type="s" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/><property name="OverlayIconName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/><property name="WindowId" type="i" access="read"/>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewIcon"/><signal name="NewTitle"/><signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout"><arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
    <arg type="as" name="propertyNames" direction="in"/><arg type="u" name="revision" direction="out"/>
    <arg type="(ia{sv}av)" name="layout" direction="out"/></method>
  <method name="GetGroupProperties"><arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
    <arg type="a(ia{sv})" name="properties" direction="out"/></method>
  <method name="GetProperty"><arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
    <arg type="v" name="value" direction="out"/></method>
  <method name="Event"><arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
    <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
  <method name="EventGroup"><arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/></method>
  <method name="AboutToShow"><arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/></method>
  <method name="AboutToShowGroup"><arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/>
    <arg type="ai" name="idErrors" direction="out"/></method>
  <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
  <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias{sv})" name="removedProps"/></signal>
</interface></node>"""

# identifiants des entrées du menu
SONG, SHOW, PLAY, NEXT, PREV, SEP, QUIT = 1, 2, 3, 4, 5, 6, 7


class Tray:
    """controller : backend.controller.Controller ; show_cb / quit_cb : fenêtre, application."""

    def __init__(self, controller, show_cb: Callable[[], None], quit_cb: Callable[[], None]):
        self.c = controller
        self.show_cb = show_cb
        self.quit_cb = quit_cb
        self.conn = None
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._owner = 0
        self._ids: list[int] = []
        self.revision = 1
        self.registered = False          # accepté par un StatusNotifierWatcher
        self.item_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        self.menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._watch = 0

    @property
    def active(self) -> bool:
        return bool(self._owner)

    # -- apparition / disparition ---------------------------------------------------------------
    def show(self):
        if self._owner:
            return
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, self.name, Gio.BusNameOwnerFlags.NONE,
                                       self._on_bus, None, None)
        self.c.connect(self._on_event)

    def hide(self):
        if not self._owner:
            return
        self.c.disconnect(self._on_event)
        if self.conn is not None:
            for i in self._ids:
                self.conn.unregister_object(i)
            if self._watch:
                self.conn.signal_unsubscribe(self._watch)
        self._ids, self._watch = [], 0
        Gio.bus_unown_name(self._owner)
        self._owner = 0
        self.conn = None
        self.registered = False

    def _on_bus(self, conn, _name):
        self.conn = conn
        self._ids = [conn.register_object(ITEM_PATH, self.item_info, self._on_item_call, self._on_item_get, None),
                     conn.register_object(MENU_PATH, self.menu_info, self._on_menu_call, self._on_menu_get, None)]
        self._register()
        # la barre (l'extension) redémarre : on se réenregistre
        self._watch = conn.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                            "/org/freedesktop/DBus", WATCHER, Gio.DBusSignalFlags.NONE,
                                            lambda *a: self._register(), )

    def _register(self):
        if self.conn is None:
            return

        def done(conn, res):
            try:
                conn.call_finish(res)
                self.registered = True
            except GLib.Error:
                self.registered = False          # pas de barre AppIndicator : rien ne s'affiche
        self.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                       GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, 3000, None, done)

    # -- icône --------------------------------------------------------------------------------
    def _tooltip(self) -> GLib.Variant:
        song = self.c.song
        text = " — ".join(x for x in (song.display_title, song.artist) if x) if song else ""
        return GLib.Variant("(sa(iiay)ss)", (APP_ID, [], APP_NAME, text))

    def _on_item_get(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"), "Id": GLib.Variant("s", "ithaca"),
            "Title": GLib.Variant("s", APP_NAME), "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", ICON_NAME),
            "IconThemePath": GLib.Variant("s", icon_theme_path()),
            "AttentionIconName": GLib.Variant("s", ""), "OverlayIconName": GLib.Variant("s", ""),
            "ToolTip": self._tooltip(), "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH), "WindowId": GLib.Variant("i", 0),
        }
        return values.get(prop)

    def _on_item_call(self, _conn, _sender, _path, _iface, method, params, inv):
        if method == "Activate":
            GLib.idle_add(lambda: (self.show_cb(), False)[1])
        elif method == "SecondaryActivate":
            self.c.toggle()
        elif method == "Scroll":
            from ..settings import settings
            delta, orientation = params.unpack()
            if orientation == "vertical":
                self.c.set_volume(settings["volume"] + (0.05 if delta < 0 else -0.05))
        inv.return_value(None)

    # -- menu ---------------------------------------------------------------------------------
    def items(self) -> list[tuple[int, dict]]:
        song = self.c.song
        title = " — ".join(x for x in (song.display_title, song.artist) if x) if song else APP_NAME
        has = len(self.c.queue) > 0
        return [
            (SONG, {"label": GLib.Variant("s", title), "enabled": GLib.Variant("b", False)}),
            (SHOW, {"label": GLib.Variant("s", tr("Afficher Ithaca"))}),
            (PLAY, {"label": GLib.Variant("s", tr("Pause") if self.c.playing else tr("Lire")),
                    "enabled": GLib.Variant("b", has)}),
            (NEXT, {"label": GLib.Variant("s", tr("Morceau suivant")), "enabled": GLib.Variant("b", has)}),
            (PREV, {"label": GLib.Variant("s", tr("Morceau précédent")), "enabled": GLib.Variant("b", has)}),
            (SEP, {"type": GLib.Variant("s", "separator")}),
            (QUIT, {"label": GLib.Variant("s", tr("Quitter"))}),
        ]

    def _layout(self) -> GLib.Variant:
        children = [GLib.Variant("(ia{sv}av)", (i, props, [])) for i, props in self.items()]
        return GLib.Variant("(u(ia{sv}av))", (self.revision, (0, {"children-display": GLib.Variant("s", "submenu")},
                                                                children)))

    def _on_menu_get(self, _conn, _sender, _path, _iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"),
                "IconThemePath": GLib.Variant("as", [d for d in [icon_theme_path()] if d])}.get(prop)

    def _on_menu_call(self, _conn, _sender, _path, _iface, method, params, inv):
        if method == "GetLayout":
            inv.return_value(self._layout())
            return
        if method == "GetGroupProperties":
            ids, _names = params.unpack()
            props = [(i, p) for i, p in self.items() if not ids or i in ids]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (props,)))
            return
        if method == "GetProperty":
            i, name = params.unpack()
            value = dict(self.items()).get(i, {}).get(name, GLib.Variant("s", ""))
            inv.return_value(GLib.Variant("(v)", (value,)))
            return
        if method == "Event":
            i, event, _data, _ts = params.unpack()
            if event == "clicked":
                GLib.idle_add(lambda: (self.activate(i), False)[1])
            inv.return_value(None)
            return
        if method == "EventGroup":
            events = params.unpack()[0]
            for i, event, _data, _ts in events:
                if event == "clicked":
                    GLib.idle_add(lambda i=i: (self.activate(i), False)[1])
            inv.return_value(GLib.Variant("(ai)", ([],)))
            return
        if method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
            return
        if method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
            return
        inv.return_value(None)

    def activate(self, item: int):
        if item == SHOW:
            self.show_cb()
        elif item == PLAY:
            self.c.toggle()
        elif item == NEXT:
            self.c.next()
        elif item == PREV:
            self.c.previous()
        elif item == QUIT:
            self.quit_cb()

    def _on_event(self, event, *args):
        if self.conn is None or event in ("position", "loading", "volume"):
            return
        self.revision += 1
        self.conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                              GLib.Variant("(ui)", (self.revision, 0)))
        self.conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)
