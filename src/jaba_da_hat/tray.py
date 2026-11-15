"""System tray icon (own StatusNotifierItem), its menu, toasts and desktop notifications."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import logging

from PySide6.QtCore import QObject, QProcess, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QMenu

from jaba_da_hat import APP_ID, APP_NAME, autostart, trayicons
from jaba_da_hat.sni import DBusTray
from jaba_da_hat.models import PRESENCE_LABELS, UNGROUPED

log = logging.getLogger(__name__)

RESOURCES = Path(__file__).resolve().parent / "resources"
MENU_PRESENCES = ("available", "away", "dnd", "offline")
# 7 frames × 150 ms ≈ 1 s; each frame outlasts the panel's ~140 ms fetch cycle — see docs/comments-details.md [12].
WIGGLE_ANGLES = (18, -18, 18, -18, 14, -14, 6)
MENU_CONTACTS = 10  # contacts listed directly in the tray menu; the rest sit under "…"
WIGGLE_FRAME_MS = 150
WIGGLE_REPEAT_MS = 2000  # …then 1 s still, repeated for as long as something is waiting
TOAST_LINES = 4


def status_icon(presence: str) -> QIcon:
    return QIcon(trayicons.status_pixmap(presence, 32))


def app_pixmap(size: int = 256) -> QPixmap:
    """The application logo — used for windows and the .desktop launcher, never for the tray."""
    for cand in (RESOURCES / f"icon-{size}.png", RESOURCES / "icon-256.png"):
        if cand.exists():
            return QPixmap(str(cand)).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm = QPixmap(size, size)
    pm.fill(QColor("#3584e4"))
    return pm


class Tray(QObject):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.menu = QMenu()
        # D-Bus side is only connected by show(), so tests never put an icon in the real panel.
        self.sni = DBusTray(self.menu, APP_ID, APP_NAME, self)
        self.sni.menuAboutToShow.connect(self.rebuild_menu)
        self.sni.activated.connect(self.activate)
        self.sni.notificationClicked.connect(self._on_notification_clicked)
        self._last_notified_jid = ""
        self._kind = "status"
        self._wiggle_step = -1
        self._wiggle = QTimer(self, interval=WIGGLE_FRAME_MS)
        self._wiggle.timeout.connect(self._wiggle_frame)
        self._rewiggle = QTimer(self, interval=WIGGLE_REPEAT_MS)
        self._rewiggle.timeout.connect(self.start_wiggle)
        self._toast: QProcess | None = None
        self._toast_events: list[tuple[str, str, str, str]] = []  # events shown in the current toast
        app.stateChanged.connect(self.refresh)
        app.activityChanged.connect(self.refresh)
        app.presenceChanged.connect(self.refresh)
        app.unreadChanged.connect(self.refresh)
        app.notifications.countChanged.connect(self.refresh)
        app.notify.connect(self.show_notification)
        self.rebuild_menu()
        self.refresh()

    def show(self) -> bool:
        """Connect to the session bus and register the tray icon; False without a session bus."""
        ok = self.sni.start()
        self.refresh()
        return ok

    @property
    def watcher_available(self) -> bool | None:
        return self.sni.watcher_available

    def hide(self) -> None:
        self.sni.stop()

    # ---- icon / tooltip ----------------------------------------------
    def refresh(self) -> None:
        app = self.app
        unread = app.roster.total_unread()
        files = app.pending_file_offers()
        notes = app.notifications.count
        kind = trayicons.tray_kind(unread, files, app.notifications.unseen)  # only what came since the last Clear
        if kind != self._kind:
            self._kind = kind
            if kind == "status":
                self._wiggle.stop()
                self._rewiggle.stop()
                self._wiggle_step = -1
        if self._wiggle_step < 0:
            self._set_icon(0)
        state = app.connection_state
        tip = f"{APP_NAME} — {PRESENCE_LABELS[app.presence] if state != 'connecting' else 'Connecting…'}"
        if unread:
            tip += f"\n{unread} unread message(s)"
        if files:
            tip += f"\n{files} incoming file(s)"
        if notes:
            tip += f"\n{notes} notification(s)"
        title, _, rest = tip.partition("\n")
        self.sni.set_tooltip(title, rest)
        if self.sni.connected:
            self.rebuild_menu()
            self.sni.menu_changed()  # an open menu shows the new state

    def _set_icon(self, angle: float) -> None:
        self.sni.set_icon(trayicons.render(self._kind, self.app.presence, angle))

    # ---- wiggle ------------------------------------------------------
    def start_wiggle(self) -> None:
        """Shake the envelope / file / note icon so new activity gets noticed."""
        if self._kind == "status":
            self._rewiggle.stop()
            return
        log.debug("wiggle (%s)", self._kind)
        self._wiggle_step = 0
        self._wiggle.start()
        self._rewiggle.start()

    def _wiggle_frame(self) -> None:
        if self._wiggle_step < 0 or self._wiggle_step >= len(WIGGLE_ANGLES):
            self._wiggle.stop()
            self._wiggle_step = -1
            self._set_icon(0)
            return
        self._set_icon(WIGGLE_ANGLES[self._wiggle_step])
        self._wiggle_step += 1

    # ---- notifications -----------------------------------------------
    def show_notification(self, title: str, body: str, jid: str, kind: str) -> None:
        """Announce an event: wiggle the tray icon and show a toast and/or a GNOME notification."""
        self._last_notified_jid = jid
        self.refresh()
        self.start_wiggle()
        style = self.app.settings.notification_style
        if style in ("toast", "both"):
            self._toast_events.append((title, body, jid, kind))
            self._show_toast()
        if style in ("gnome", "both"):
            self._show_gnome(title, body, [(title, body, jid, kind)])

    def _show_gnome(self, title: str, body: str, events: list | None = None) -> None:
        """freedesktop notification (lands in GNOME's message list even under Do Not Disturb). See docs/comments-details.md [4]."""
        icon = autostart.icon_file()
        self.sni.notify(title, body, str(icon) if icon.exists() else "", payload=events)

    @staticmethod
    def summarize(events: list[tuple[str, str, str, str]]) -> tuple[str, str, str]:
        """(title, body, kind) for one toast covering all *events* that arrived while it was up."""
        if len(events) == 1:
            title, body, _jid, kind = events[0]
            return title, body, kind
        kinds = {e[3] for e in events}
        kind = next(k for k in ("message", "file", "note") if k in kinds)
        n_msg = sum(1 for e in events if e[3] == "message")
        n_file = sum(1 for e in events if e[3] == "file")
        n_note = len(events) - n_msg - n_file
        parts = [f"{n} {word}{'s' if n > 1 else ''}" for n, word in
                 ((n_msg, "message"), (n_file, "file"), (n_note, "notification")) if n]
        senders = len({e[2] for e in events if e[2]})
        title = ", ".join(parts) + (f" from {senders} contacts" if senders > 1 else "")
        lines = [f"{t}: {b.splitlines()[0][:70]}" for t, b, _j, _k in events[-TOAST_LINES:]]
        if len(events) > TOAST_LINES:
            lines.insert(0, f"…and {len(events) - TOAST_LINES} earlier")
        return title, "\n".join(lines), kind

    def _show_toast(self) -> None:
        if self._toast is not None and self._toast.state() != QProcess.NotRunning:
            self._toast.finished.disconnect()
            self._toast.errorOccurred.disconnect()
            self._toast.kill()  # replaced by a toast that also lists the new event
            self._toast.waitForFinished(300)
        events = list(self._toast_events)
        title, body, kind = self.summarize(events)
        cmd = autostart.self_command() + ["--toast", title, body, "--toast-kind", kind]
        if self.app.darkTheme:
            cmd.append("--toast-dark")
        proc = QProcess(self)
        proc.finished.connect(lambda code, _status, p=proc: self._toast_done(p, code, events))
        proc.errorOccurred.connect(lambda err, p=proc: err == QProcess.FailedToStart and self._toast_done(p, -1, events))
        self._toast = proc
        proc.start(cmd[0], cmd[1:])

    def _toast_done(self, proc: QProcess, code: int, events: list) -> None:
        try:
            err = bytes(proc.readAllStandardError()).decode(errors="ignore")[-300:]
            proc.deleteLater()
        except RuntimeError:  # already destroyed during shutdown
            return
        if self._toast is proc:
            self._toast = None
        self._toast_events = []
        if code == 0:
            self.open_events(events)
        elif code not in (1, 2):
            log.warning("toast helper failed (exit %s): %s", code, err)
            title, body, _kind = self.summarize(events)
            self._show_gnome(title, body, events)

    def open_events(self, events: list) -> None:
        """Clicked toast: open every chat it mentioned (several windows at once), or the notifications."""
        jids = list(dict.fromkeys(e[2] for e in events if e[2]))
        for jid in jids:
            self.app.openChat(jid)
        if any(not e[2] for e in events):
            self.app.openNotifications()

    def close_toast(self) -> None:
        if self._toast is not None and self._toast.state() != QProcess.NotRunning:
            self._toast.finished.disconnect()
            self._toast.kill()
            self._toast.waitForFinished(500)

    def _on_notification_clicked(self, nid: int) -> None:
        events = self.sni.notification_ids.pop(nid, None)
        if events:
            self.open_events(events)
        else:
            self.app.openUnread()

    def activate(self) -> None:
        """Double click on the icon (single left click opens the menu — AppIndicator extension): open every waiting chat."""
        self.app.openUnread()

    # ---- menu --------------------------------------------------------
    def rebuild_menu(self) -> None:
        m = self.menu
        m.clear()
        app = self.app
        state = app.connection_state

        status = m.addMenu(status_icon(app.presence), f"Status: {PRESENCE_LABELS[app.presence]}")
        grp = QActionGroup(status)
        for p in MENU_PRESENCES:
            a = status.addAction(status_icon(p), PRESENCE_LABELS[p])
            a.setCheckable(True)
            a.setChecked(app.presence == p and state == "online" or (p == "offline" and state == "offline"))
            grp.addAction(a)
            a.triggered.connect(partial(app.setPresence, p))

        self._build_contact_list(m)
        self._build_contact_options(m.addMenu("Contacts"))

        n = app.notifications.count
        self._build_notifications(m.addMenu(f"Notifications ({n})" if n else "Notifications"))
        m.addAction("Settings…", app.openSettings)
        if state == "offline":
            m.addAction("Log on", app.login)
        elif state == "connecting":
            m.addAction("Connecting…").setEnabled(False)
        else:
            m.addAction("Log off", app.logout)
        if app.backend.is_mock:
            self._build_mock(m.addMenu("Mock / testing"))
        m.addSeparator()
        m.addAction("Close", app.quit)

    def _build_contact_list(self, menu: QMenu) -> None:
        """Contacts right in the menu, by group; after MENU_CONTACTS a "…" submenu opens the rest in place."""
        app = self.app
        show_offline = app.settings.show_offline
        contacts = [c for c in app.roster.contacts() if show_offline or c.online]
        if not contacts:
            menu.addAction("No contacts online" if app.roster.contacts() else "No contacts").setEnabled(False)
            return
        by_group: dict[str, list] = {}
        for c in contacts:
            for g in c.groups or [UNGROUPED]:
                by_group.setdefault(g, []).append(c)
        entries = [(g, c) for g in sorted(by_group, key=lambda x: (x == UNGROUPED, x.lower())) for c in by_group[g]]
        headers = any(g != UNGROUPED for g, _ in entries)  # no headings when nobody is in a group
        if len(entries) > MENU_CONTACTS + 1:  # a "…" that hides a single contact would be silly
            shown, rest = entries[:MENU_CONTACTS], entries[MENU_CONTACTS:]
        else:
            shown, rest = entries, []
        self._add_contacts(menu, shown, headers)
        if rest:
            # A submenu opens inline in GNOME's tray menu (and scrolls when long); a plain item would close it.
            more = menu.addMenu("…")
            more.setToolTip(f"{len(rest)} more")
            self._add_contacts(more, rest, headers)
        menu.setToolTipsVisible(True)

    def _add_contacts(self, menu: QMenu, entries: list, headers: bool) -> None:
        app = self.app
        group = None
        for g, c in entries:
            if headers and g != group:
                menu.addSection(g or "Ungrouped").setEnabled(False)
                group = g
            label = c.display_name + (f"  ({c.unread})" if c.unread else "")
            a = menu.addAction(status_icon(c.presence), label)
            a.setToolTip(f"{c.jid}\n{PRESENCE_LABELS[c.presence]}" + (f": {c.status_text}" if c.status_text else ""))
            a.triggered.connect(partial(app.openChat, c.jid))
        menu.setToolTipsVisible(True)

    def _build_contact_options(self, menu: QMenu) -> None:
        app = self.app
        grp = QActionGroup(menu)
        for label, value in (("Only online", False), ("All contacts", True)):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(app.settings.show_offline == value)
            grp.addAction(a)
            a.triggered.connect(partial(self._set_show_offline, value))
        menu.addSeparator()
        menu.addAction("Manage contacts…", app.openContacts)

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
