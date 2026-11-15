"""System tray icon, its right-click menu and desktop notifications."""

from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from jaba_da_hat import APP_NAME
from jaba_da_hat.models import PRESENCE_LABELS, UNGROUPED

RESOURCES = Path(__file__).resolve().parent / "resources"
PRESENCE_COLORS = {
    "available": "#2ec27e",
    "away": "#f5c211",
    "xa": "#e66100",
    "dnd": "#e01b24",
    "offline": "#9a9996",
}
MENU_PRESENCES = ("available", "away", "dnd", "offline")


def status_icon(presence: str, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(PRESENCE_COLORS.get(presence, "#9a9996")))
    p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
    p.end()
    return QIcon(pm)


def app_pixmap(size: int = 256) -> QPixmap:
    for cand in (RESOURCES / f"icon-{size}.png", RESOURCES / "icon-256.png"):
        if cand.exists():
            return QPixmap(str(cand)).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm = QPixmap(size, size)
    pm.fill(QColor("#3584e4"))
    return pm


def tray_pixmap(presence: str, unread: int, size: int = 64) -> QPixmap:
    """App icon with a presence dot (bottom-right) and an unread badge (top-right)."""
    base = app_pixmap(size)
    if presence == "offline":
        img = base.toImage().convertToFormat(base.toImage().Format.Format_Grayscale8)
        faded = QPixmap(size, size)
        faded.fill(Qt.transparent)
        p = QPainter(faded)
        p.setOpacity(0.6)
        p.drawImage(0, 0, img)
        p.end()
        base = faded
    p = QPainter(base)
    p.setRenderHint(QPainter.Antialiasing)
    d = size * 0.38
    p.setPen(QColor("#ffffff"))
    p.setBrush(QColor(PRESENCE_COLORS.get(presence, "#9a9996")))
    p.drawEllipse(QRectF(size - d - 1, size - d - 1, d, d))
    if unread:
        b = size * 0.5
        p.setBrush(QColor("#e01b24"))
        p.drawEllipse(QRectF(size - b, 0, b, b))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(b * 0.62))
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(size - b, 0, b, b), Qt.AlignCenter, str(unread) if unread < 10 else "9+")
    p.end()
    return base


class Tray(QObject):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.icon = QSystemTrayIcon(self)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild_menu)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)
        self.icon.messageClicked.connect(self._on_message_clicked)
        self._last_notified_jid = ""
        app.stateChanged.connect(self.refresh)
        app.presenceChanged.connect(self.refresh)
        app.unreadChanged.connect(self.refresh)
        app.notifications.countChanged.connect(self.refresh)
        app.notify.connect(self.show_notification)
        self.rebuild_menu()
        self.refresh()

    def show(self) -> None:
        self.icon.show()

    # ---- icon / tooltip ----------------------------------------------
    def refresh(self) -> None:
        unread = self.app.roster.total_unread()
        self.icon.setIcon(QIcon(tray_pixmap(self.app.presence, unread)))
        state = self.app.connection_state
        tip = f"{APP_NAME} — {PRESENCE_LABELS[self.app.presence] if state != 'connecting' else 'Connecting…'}"
        if unread:
            tip += f"\n{unread} unread message(s)"
        n = self.app.notifications.count
        if n:
            tip += f"\n{n} notification(s)"
        self.icon.setToolTip(tip)
        if self.menu.isVisible():
            self.rebuild_menu()

    def show_notification(self, title: str, body: str, jid: str) -> None:
        """Desktop notification through the freedesktop D-Bus service. See docs/comments-details.md [4]."""
        self._last_notified_jid = jid
        self.icon.showMessage(title, body, QIcon(app_pixmap(128)), 8000)

    def _on_message_clicked(self) -> None:
        if self._last_notified_jid:
            self.app.openChat(self._last_notified_jid)
        else:
            self.app.openNotifications()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.app.roster.total_unread() or self.app.windows:
                self.app.openFirstUnread()
            else:
                self.app.openContacts()

    # ---- menu --------------------------------------------------------
    def rebuild_menu(self) -> None:
        m = self.menu
        m.clear()
        app = self.app
        state = app.connection_state

        header = m.addAction(QIcon(app_pixmap(32)), f"{APP_NAME} — {app.settings.jid or 'no account'}")
        header.setEnabled(False)

        status = m.addMenu(status_icon(app.presence), f"Status: {PRESENCE_LABELS[app.presence]}")
        grp = QActionGroup(status)
        for p in MENU_PRESENCES:
            a = status.addAction(status_icon(p), PRESENCE_LABELS[p])
            a.setCheckable(True)
            a.setChecked(app.presence == p and state == "online" or (p == "offline" and state == "offline"))
            grp.addAction(a)
            a.triggered.connect(partial(app.setPresence, p))

        if state == "offline":
            m.addAction("Log on", app.login)
        elif state == "connecting":
            m.addAction("Connecting…").setEnabled(False)
        else:
            m.addAction("Log off", app.logout)
        m.addSeparator()

        self._build_contacts(m.addMenu("Contacts"))

        n = app.notifications.count
        self._build_notifications(m.addMenu(f"Notifications ({n})" if n else "Notifications"))
        m.addSeparator()

        if app.backend.is_mock:
            self._build_mock(m.addMenu("Mock / testing"))
        m.addAction("Settings…", app.openSettings)
        m.addAction("Quit", app.quit)

    def _build_contacts(self, menu: QMenu) -> None:
        app = self.app
        show_offline = app.settings.show_offline
        f = menu.addMenu("Show")
        grp = QActionGroup(f)
        for label, value in (("Only online", False), ("All contacts", True)):
            a = f.addAction(label)
            a.setCheckable(True)
            a.setChecked(show_offline == value)
            grp.addAction(a)
            a.triggered.connect(partial(self._set_show_offline, value))
        menu.addAction("Manage contacts…", app.openContacts)
        menu.addSeparator()

        contacts = [c for c in app.roster.contacts() if show_offline or c.online]
        if not contacts:
            menu.addAction("No contacts online" if app.roster.contacts() else "No contacts").setEnabled(False)
            return
        by_group: dict[str, list] = {}
        for c in contacts:
            for g in c.groups or [UNGROUPED]:
                by_group.setdefault(g, []).append(c)
        for g in sorted(by_group, key=lambda x: (x == UNGROUPED, x.lower())):
            sec = menu.addSection(g or "Ungrouped")
            sec.setEnabled(False)
            for c in by_group[g]:
                label = c.display_name + (f"  ({c.unread})" if c.unread else "")
                a = menu.addAction(status_icon(c.presence), label)
                a.setToolTip(f"{c.jid}\n{PRESENCE_LABELS[c.presence]}" + (f": {c.status_text}" if c.status_text else ""))
                a.triggered.connect(partial(app.openChat, c.jid))
        menu.setToolTipsVisible(True)

    def _set_show_offline(self, value: bool) -> None:
        self.app.settings.show_offline = value
        self.app._persist()

    def _build_notifications(self, menu: QMenu) -> None:
        app = self.app
        items = app.notifications.items()
        if not items:
            menu.addAction("Nothing new").setEnabled(False)
            return
        for item in items:
            text = item["text"].split("\n")[0]
            if item["kind"] == "subscription":
                sub = menu.addMenu(f"Authorization: {item['jid']}")
                sub.addAction(text).setEnabled(False)
                sub.addSeparator()
                sub.addAction("Accept", partial(app.acceptSubscription, item["nid"]))
                sub.addAction("Decline", partial(app.declineSubscription, item["nid"], False))
                sub.addAction("Decline and block", partial(app.declineSubscription, item["nid"], True))
            else:
                prefix = "⚠ " if item["kind"] == "error" else ""
                a = menu.addAction(f"{prefix}{item['time']}  {text[:80]}")
                a.setToolTip(item["text"])
                a.triggered.connect(partial(app.dismissNotification, item["nid"]))
        menu.addSeparator()
        menu.addAction("Show all…", app.openNotifications)
        menu.addAction("Clear messages", app.clearNotifications)
        menu.setToolTipsVisible(True)

    def _build_mock(self, menu: QMenu) -> None:
        b = self.app.backend
        menu.addAction("Incoming message", b.simulate_incoming_message)
        menu.addAction("Authorization request", b.simulate_subscription_request)
        menu.addAction("Incoming file", b.simulate_file_offer)
        menu.addAction("Drop connection", b.simulate_connection_drop)
