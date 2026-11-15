"""Suspend / resume (systemd-logind) and connectivity (NetworkManager) from the system D-Bus.

Lets the controller go offline cleanly before sleep and reconnect right after resume or when the
network comes back, instead of waiting for the XMPP ping to time out — see docs/comments-details.md [23].
"""

from __future__ import annotations

import logging

from jeepney import DBusAddress, HeaderFields, MatchRule, Message, MessageType, message_bus, new_method_call
from jeepney.io.blocking import open_dbus_connection
from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

log = logging.getLogger(__name__)

LOGIND = DBusAddress("/org/freedesktop/login1", bus_name="org.freedesktop.login1",
                     interface="org.freedesktop.login1.Manager")
NM = DBusAddress("/org/freedesktop/NetworkManager", bus_name="org.freedesktop.NetworkManager",
                 interface="org.freedesktop.NetworkManager")
NM_CONNECTED_SITE = 60  # NMState: 60 = site, 70 = global connectivity
SLEEP_DELAY_MAX_MS = 3000  # we never hold up suspend longer than this


class SystemEvents(QObject):
    sleeping = Signal(bool)  # True: about to suspend, False: resumed
    networkChanged = Signal(bool)  # True: network usable again, False: gone

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conn = None
        self._notifier: QSocketNotifier | None = None
        self._replies: dict[int, object] = {}
        self._inhibitor = None  # logind delay lock (file descriptor) while we are awake
        self._network_up: bool | None = None

    def start(self) -> bool:
        try:
            self._conn = open_dbus_connection(bus="SYSTEM", enable_fds=True)
        except Exception as exc:  # noqa: BLE001 — no system bus (container, tests): just don't react
            log.info("no system D-Bus, sleep/network events unavailable: %s", exc)
            return False
        self._notifier = QSocketNotifier(self._conn.sock.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)
        self._call(message_bus.AddMatch(MatchRule(type="signal", interface=LOGIND.interface,
                                                  member="PrepareForSleep", path=LOGIND.object_path)))
        self._call(message_bus.AddMatch(MatchRule(type="signal", interface=NM.interface,
                                                  member="StateChanged", path=NM.object_path)))
        self._take_inhibitor()
        return True

    def stop(self) -> None:
        self.release_sleep()
        if self._notifier is not None:
            self._notifier.setEnabled(False)
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn = None

    # ---- sleep inhibitor ----------------------------------------------
    def _take_inhibitor(self) -> None:
        if self._inhibitor is not None:
            return

        def got(reply: Message) -> None:
            if reply.header.message_type == MessageType.method_return:
                self._inhibitor = reply.body[0]
            else:
                log.info("logind delay lock refused: %s", reply.body)

        self._call(new_method_call(LOGIND, "Inhibit", "ssss",
                                   ("sleep", "jaba-da-hat", "Going offline before sleep", "delay")), got)

    def release_sleep(self) -> None:
        """Let the system suspend now (call after going offline)."""
        fd, self._inhibitor = self._inhibitor, None
        if fd is not None:
            try:
                fd.close()
            except OSError:
                pass

    # ---- D-Bus plumbing -------------------------------------------------
    def _call(self, msg: Message, callback=None) -> None:
        if self._conn is None:
            return
        try:
            serial = next(self._conn.outgoing_serial)
            if callback is not None:
                self._replies[serial] = callback
            self._conn.send(msg, serial=serial)
        except OSError as exc:
            log.info("system D-Bus send failed: %s", exc)

    def _on_readable(self) -> None:
        while self._conn is not None:
            try:
                msg = self._conn.receive(timeout=0)
            except TimeoutError:
                return
            except (OSError, ValueError) as exc:
                log.info("system D-Bus connection lost: %s", exc)
                self.stop()
                return
            try:
                self.dispatch(msg)
            except Exception:  # noqa: BLE001
                log.exception("error handling a system D-Bus message")

    def dispatch(self, msg: Message) -> None:
        h = msg.header
        if h.message_type in (MessageType.method_return, MessageType.error):
            cb = self._replies.pop(h.fields.get(HeaderFields.reply_serial), None)
            if cb is not None:
                cb(msg)
            return
        if h.message_type != MessageType.signal:
            return
        iface, member = h.fields.get(HeaderFields.interface), h.fields.get(HeaderFields.member)
        if iface == LOGIND.interface and member == "PrepareForSleep":
            going = bool(msg.body[0])
            if going:
                QTimer.singleShot(SLEEP_DELAY_MAX_MS, self.release_sleep)  # never block suspend for long
            else:
                self._take_inhibitor()
            self.sleeping.emit(going)
        elif iface == NM.interface and member == "StateChanged":
            up = int(msg.body[0]) >= NM_CONNECTED_SITE
            if up != self._network_up:
                was = self._network_up
                self._network_up = up
                if was is not None or not up:
                    self.networkChanged.emit(up)
