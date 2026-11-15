"""Own tray icon: StatusNotifierItem + com.canonical.dbusmenu over D-Bus (jeepney), driven by Qt's event loop.

Replaces QSystemTrayIcon — see docs/comments-details.md [10]. The menu is still built as a QMenu;
``MenuExporter`` publishes that QMenu tree and triggers its QActions when an item is clicked.
Also sends freedesktop notifications (Qt's bindings cannot — see [4]).
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from jeepney import (
    DBusAddress,
    HeaderFields,
    MatchRule,
    Message,
    MessageType,
    message_bus,
    new_error,
    new_method_call,
    new_method_return,
    new_signal,
)
from jeepney.io.blocking import open_dbus_connection
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSocketNotifier, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import QMenu

log = logging.getLogger(__name__)

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
SNI_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
WATCHER = DBusAddress("/StatusNotifierWatcher", bus_name="org.kde.StatusNotifierWatcher",
                      interface="org.kde.StatusNotifierWatcher")
NOTIFICATIONS = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                            interface="org.freedesktop.Notifications")
UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"
ICON_SIZES = (32, 64, 128)

INTROSPECT_XML = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
 <interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionMovieName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewTitle"/>
  <signal name="NewIcon"/>
  <signal name="NewAttentionIcon"/>
  <signal name="NewOverlayIcon"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
 </interface>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
 </interface>
 <interface name="org.freedesktop.DBus.Introspectable">
  <method name="Introspect"><arg type="s" direction="out"/></method>
 </interface>
</node>
"""


def argb_pixmaps(pixmap: QPixmap) -> list[tuple[int, int, bytes]]:
    """QPixmap → SNI ``a(iiay)``: ARGB32 in network byte order, a few sizes for the panel to pick from."""
    out = []
    for size in ICON_SIZES:
        img = pixmap.toImage().scaled(size, size).convertToFormat(QImage.Format.Format_RGBA8888)
        rgba = bytes(img.constBits())[: size * size * 4]
        argb = bytearray(len(rgba))
        argb[0::4] = rgba[3::4]
        argb[1::4] = rgba[0::4]
        argb[2::4] = rgba[1::4]
        argb[3::4] = rgba[2::4]
        out.append((size, size, bytes(argb)))
    return out


def png_bytes(pixmap: QPixmap) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(data)


class MenuExporter:
    """Publishes a QMenu tree as com.canonical.dbusmenu layout; ids are reassigned on every rebuild."""

    def __init__(self, menu: QMenu):
        self.menu = menu
        self.revision = 1
        self.actions: dict[int, QAction] = {}
        self.items: dict[int, tuple[dict, list[int]]] = {}  # id -> (props, child ids)
        self.rebuild()

    def rebuild(self) -> None:
        self.revision += 1
        self.actions.clear()
        self.items = {}
        self._next = 1
        self.items[0] = ({"children-display": ("s", "submenu")}, self._children(self.menu))

    def _children(self, menu: QMenu) -> list[int]:
        ids = []
        for a in menu.actions():
            if not a.isVisible():
                continue
            if a.isSeparator():
                if ids:
                    ids.append(self._add({"type": ("s", "separator")}, []))
                if a.text():  # QMenu.addSection(): separator + caption
                    ids.append(self._add({"label": ("s", self._label(a.text())), "enabled": ("b", False)}, []))
                continue
            props = {"label": ("s", self._label(a.text())), "enabled": ("b", a.isEnabled())}
            if a.isCheckable():
                grp = a.actionGroup()
                props["toggle-type"] = ("s", "radio" if grp is not None and grp.isExclusive() else "checkmark")
                props["toggle-state"] = ("i", 1 if a.isChecked() else 0)
            if not a.icon().isNull():
                props["icon-data"] = ("ay", png_bytes(a.icon().pixmap(32, 32)))
            children: list[int] = []
            if a.menu() is not None:
                props["children-display"] = ("s", "submenu")
                children = self._children(a.menu())
            iid = self._add(props, children)
            self.actions[iid] = a
            ids.append(iid)
        return ids

    def _add(self, props: dict, children: list[int]) -> int:
        iid = self._next
        self._next += 1
        self.items[iid] = (props, children)
        return iid

    @staticmethod
    def _label(text: str) -> str:
        return text.replace("&&", "&").replace("_", "__")  # dbusmenu mnemonics use "_"

    def _props(self, iid: int, names: list[str]) -> dict:
        props = self.items[iid][0]
        return {k: v for k, v in props.items() if not names or k in names}

    def layout(self, parent: int, depth: int, names: list[str]):
        def node(iid: int, d: int):
            props, children = self.items[iid]
            kids = [] if d == 0 else [("(ia{sv}av)", node(c, d - 1)) for c in children]
            return (iid, self._props(iid, names), kids)

        if parent not in self.items:
            parent = 0
        return self.revision, node(parent, depth)

    def group_properties(self, ids: list[int], names: list[str]):
        ids = ids or list(self.items)
        return [(i, self._props(i, names)) for i in ids if i in self.items]

    def trigger(self, iid: int) -> bool:
        a = self.actions.get(iid)
        if a is None:
            return False
        try:
            QTimer.singleShot(0, a.trigger)  # after the D-Bus reply went out
        except RuntimeError:  # QAction deleted by a menu rebuild
            return False
        return True


class DBusTray(QObject):
    """A StatusNotifierItem with a dbusmenu, plus freedesktop notifications, on its own session-bus connection."""

    activated = Signal()
    menuAboutToShow = Signal()
    notificationClicked = Signal(int)  # notification id

    def __init__(self, menu: QMenu, item_id: str, title: str, parent=None):
        super().__init__(parent)
        self.menu = menu
        self.exporter = MenuExporter(menu)
        self._id = item_id
        self._title = title
        self._status = "Active"
        self._icon: list = []
        self._tooltip = ("", [], title, "")
        self._conn = None
        self._notifier: QSocketNotifier | None = None
        self._replies: dict[int, Callable[[Message], None]] = {}
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.watcher_available: bool | None = None  # None = not known yet
        self.notification_ids: dict[int, object] = {}  # id -> caller's payload

    # ---- connection --------------------------------------------------
    def start(self) -> bool:
        try:
            self._conn = open_dbus_connection(bus="SESSION")
        except Exception as exc:  # noqa: BLE001 — no session bus: run without a tray
            log.warning("no D-Bus session bus: %s", exc)
            return False
        self._notifier = QSocketNotifier(self._conn.sock.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)
        watcher_rule = MatchRule(type="signal", sender="org.freedesktop.DBus", interface="org.freedesktop.DBus",
                                 member="NameOwnerChanged", path="/org/freedesktop/DBus")
        watcher_rule.add_arg_condition(0, WATCHER.bus_name)
        self._call(message_bus.AddMatch(watcher_rule))
        self._call(message_bus.AddMatch(MatchRule(type="signal", interface=NOTIFICATIONS.interface,
                                                  path=NOTIFICATIONS.object_path)))
        self._call(message_bus.RequestName(self.bus_name), lambda _r: self._register())
        return True

    def stop(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn = None

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def _register(self) -> None:
        def done(reply: Message):
            self.watcher_available = reply.header.message_type == MessageType.method_return
            if not self.watcher_available:
                log.warning("no StatusNotifierWatcher (on GNOME enable the AppIndicator extension)")

        self._call(new_method_call(WATCHER, "RegisterStatusNotifierItem", "s", (self.bus_name,)), done)

    def _call(self, msg: Message, callback: Callable[[Message], None] | None = None) -> None:
        if self._conn is None:
            return
        try:
            serial = next(self._conn.outgoing_serial)
            if callback is not None:
                self._replies[serial] = callback
            self._conn.send(msg, serial=serial)
        except OSError as exc:
            log.warning("D-Bus send failed: %s", exc)

    def _signal(self, path: str, iface: str, name: str, sig: str | None = None, body: tuple = ()) -> None:
        if self._conn is None:
            return
        try:
            self._conn.send(new_signal(DBusAddress(path, interface=iface), name, sig, body))
        except OSError as exc:
            log.warning("D-Bus signal failed: %s", exc)

    def _on_readable(self) -> None:
        while self._conn is not None:
            try:
                msg = self._conn.receive(timeout=0)
            except TimeoutError:
                return
            except (OSError, ValueError) as exc:
                log.warning("D-Bus connection lost: %s", exc)
                self.stop()
                return
            try:
                self._dispatch(msg)
            except Exception:  # noqa: BLE001 — one bad message must not kill the tray
                log.exception("error handling D-Bus message")

    # ---- dispatch ----------------------------------------------------
    def _dispatch(self, msg: Message) -> None:
        h = msg.header
        if h.message_type in (MessageType.method_return, MessageType.error):
            cb = self._replies.pop(h.fields.get(HeaderFields.reply_serial), None)
            if cb is not None:
                cb(msg)
            return
        member = h.fields.get(HeaderFields.member)
        iface = h.fields.get(HeaderFields.interface)
        if h.message_type == MessageType.signal:
            self._on_signal(iface, member, msg.body)
            return
        path = h.fields.get(HeaderFields.path)
        reply = self._handle_call(path, iface, member, msg)
        if self._conn is not None and reply is not None:
            self._conn.send(reply)

    def _on_signal(self, iface: str, member: str, body: tuple) -> None:
        if member == "NameOwnerChanged" and body and body[0] == WATCHER.bus_name and body[2]:
            self._register()  # extension (re)loaded: announce ourselves again
        elif iface == NOTIFICATIONS.interface and member == "ActionInvoked":
            nid, _key = body
            if nid in self.notification_ids:
                self.notificationClicked.emit(nid)
        elif iface == NOTIFICATIONS.interface and member == "NotificationClosed":
            self.notification_ids.pop(body[0], None)

    def _handle_call(self, path: str, iface: str, member: str, msg: Message) -> Message | None:
        body = msg.body
        if member == "Introspect":
            return new_method_return(msg, "s", (INTROSPECT_XML if path == SNI_PATH else "<node/>",))
        if member == "Ping":
            return new_method_return(msg)
        if iface == PROPS_IFACE or (iface is None and member in ("Get", "GetAll")):
            props = self._sni_props() if path == SNI_PATH else self._menu_props()
            if member == "GetAll":
                return new_method_return(msg, "a{sv}", (props,))
            if member == "Get" and body[1] in props:
                return new_method_return(msg, "v", (props[body[1]],))
            return new_error(msg, "org.freedesktop.DBus.Error.InvalidArgs", "s", (f"no property {body[-1]}",))
        if path == SNI_PATH:
            # Deliberately no ProvideXdgActivationToken — see docs/comments-details.md [10].
            if member == "Activate":
                QTimer.singleShot(0, self.activated.emit)
                return new_method_return(msg)
            if member in ("SecondaryActivate", "Scroll", "ContextMenu"):
                return new_method_return(msg)
        if path == MENU_PATH:
            return self._handle_menu(member, msg)
        return new_error(msg, UNKNOWN_METHOD, "s", (f"{iface}.{member} is not implemented",))

    def _handle_menu(self, member: str, msg: Message) -> Message:
        body = msg.body
        ex = self.exporter
        if member == "GetLayout":
            parent, depth, names = body
            return new_method_return(msg, "u(ia{sv}av)", ex.layout(parent, depth, list(names)))
        if member == "GetGroupProperties":
            ids, names = body
            return new_method_return(msg, "a(ia{sv})", (ex.group_properties(list(ids), list(names)),))
        if member == "GetProperty":
            iid, name = body
            props = ex.items.get(iid, ({}, []))[0]
            if name in props:
                return new_method_return(msg, "v", (props[name],))
            return new_error(msg, "org.freedesktop.DBus.Error.InvalidArgs", "s", (name,))
        if member == "Event":
            iid, event, _data, _ts = body
            if event == "clicked":
                ex.trigger(iid)
            return new_method_return(msg)
        if member == "EventGroup":
            errors = []
            for iid, event, _data, _ts in body[0]:
                if event == "clicked" and not ex.trigger(iid):
                    errors.append(iid)
            return new_method_return(msg, "ai", (errors,))
        if member == "AboutToShow":
            if body[0] == 0:
                self._rebuild_menu()
                return new_method_return(msg, "b", (True,))
            return new_method_return(msg, "b", (False,))
        if member == "AboutToShowGroup":
            ids = list(body[0])
            if 0 in ids:
                self._rebuild_menu()
            return new_method_return(msg, "aiai", ([0] if 0 in ids else [], []))
        return new_error(msg, UNKNOWN_METHOD, "s", (member,))

    def _rebuild_menu(self) -> None:
        self.menuAboutToShow.emit()  # Tray rebuilds the QMenu synchronously
        self.exporter.rebuild()

    def menu_changed(self) -> None:
        """The QMenu was rebuilt outside AboutToShow (state change while the menu may be open)."""
        self.exporter.rebuild()
        self._signal(MENU_PATH, MENU_IFACE, "LayoutUpdated", "ui", (self.exporter.revision, 0))

    # ---- properties --------------------------------------------------
    def _sni_props(self) -> dict:
        return {
            "Category": ("s", "Communications"),
            "Id": ("s", self._id),
            "Title": ("s", self._title),
            "Status": ("s", self._status),
            "WindowId": ("i", 0),
            "IconName": ("s", ""),
            "IconPixmap": ("a(iiay)", self._icon),
            "OverlayIconName": ("s", ""),
            "OverlayIconPixmap": ("a(iiay)", []),
            "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []),
            "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", self._tooltip),
            "ItemIsMenu": ("b", False),
            "Menu": ("o", MENU_PATH),
        }

    @staticmethod
    def _menu_props() -> dict:
        return {
            "Version": ("u", 3),
            "TextDirection": ("s", "ltr"),
            "Status": ("s", "normal"),
            "IconThemePath": ("as", []),
        }

    def set_icon(self, pixmap: QPixmap) -> None:
        self._icon = argb_pixmaps(pixmap)
        self._signal(SNI_PATH, SNI_IFACE, "NewIcon")

    def set_tooltip(self, title: str, text: str) -> None:
        tip = ("", [], title, text)
        if tip != self._tooltip:
            self._tooltip = tip
            self._signal(SNI_PATH, SNI_IFACE, "NewToolTip")

    # ---- notifications -----------------------------------------------
    def notify(self, title: str, body: str, icon_path: str, payload: object = None, timeout_ms: int = 8000) -> bool:
        """freedesktop Notify (signature susssasa{sv}i); clicking it emits notificationClicked(id)."""
        if self._conn is None:
            return False
        hints = {"desktop-entry": ("s", self._id), "urgency": ("y", 1)}
        msg = new_method_call(NOTIFICATIONS, "Notify", "susssasa{sv}i",
                              (self._id, 0, icon_path, title, body, ["default", "Open"], hints, timeout_ms))

        def done(reply: Message):
            if reply.header.message_type == MessageType.method_return:
                self.notification_ids[reply.body[0]] = payload

        self._call(msg, done)
        return True
