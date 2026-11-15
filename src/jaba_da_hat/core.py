"""Application controller: owns settings, backend, models and windows."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlEngine

from jaba_da_hat import APP_NAME, __version__, autostart, config
from jaba_da_hat.backend.base import Backend
from jaba_da_hat.chat import ChatSession
from jaba_da_hat.models import PRESENCE_LABELS, PRESENCES, UNGROUPED, Contact, NotificationModel, RosterModel, Transfer

log = logging.getLogger(__name__)
QML_DIR = Path(__file__).resolve().parent / "qml"


class AppController(QObject):
    stateChanged = Signal()
    presenceChanged = Signal()
    settingsChanged = Signal()
    themeChanged = Signal()
    unreadChanged = Signal()
    # tray hooks
    notify = Signal(str, str, str)  # title, body, jid ("" = not a chat message)

    def __init__(self, backend: Backend, settings: config.Settings, settings_path: Path | None = None,
                 engine: QQmlApplicationEngine | None = None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.settings = settings
        self._settings_path = settings_path
        self.connection_state = "offline"
        self._presence = "available"  # desired presence while online
        self._wanted_online = False
        self.roster = RosterModel(self)
        self.roster.set_local_groups(settings.local_groups)
        self.notifications = NotificationModel(self)
        self.sessions: dict[str, ChatSession] = {}
        self.windows: dict[str, QObject] = {}
        self._transfers: dict[str, Transfer] = {}
        self._singletons: dict[str, QObject] = {}
        self.engine = engine
        self._components: dict[str, QQmlComponent] = {}
        self._system_dark = False

        b = backend
        b.stateChanged.connect(self._on_state)
        b.error.connect(self._on_error)
        b.rosterReceived.connect(self._on_roster)
        b.contactUpdated.connect(self._on_contact)
        b.contactRemoved.connect(self.roster.remove)
        b.presenceReceived.connect(self._on_presence)
        b.messageReceived.connect(self._on_message)
        b.chatStateReceived.connect(self._on_chat_state)
        b.subscriptionRequested.connect(self._on_subscription)
        b.fileOffered.connect(self._on_file_offer)
        b.transferProgress.connect(self._on_transfer_progress)
        b.transferStateChanged.connect(self._on_transfer_state)
        self.roster.changed.connect(self._on_roster_changed)

    # ================================================================ properties
    version = Property(str, lambda s: __version__, constant=True)
    appName = Property(str, lambda s: APP_NAME, constant=True)
    isMock = Property(bool, lambda s: s.backend.is_mock, constant=True)
    connectionState = Property(str, lambda s: s.connection_state, notify=stateChanged)
    presence = Property(str, lambda s: s._presence if s.connection_state == "online" else "offline",
                        notify=presenceChanged)
    presenceLabel = Property(str, lambda s: PRESENCE_LABELS[s.presence], notify=presenceChanged)
    rosterModel = Property(QObject, lambda s: s.roster, constant=True)
    notificationModel = Property(QObject, lambda s: s.notifications, constant=True)
    totalUnread = Property(int, lambda s: s.roster.total_unread(), notify=unreadChanged)
    groups = Property(list, lambda s: s.roster.groups(), notify=unreadChanged)

    def _dark(self) -> bool:
        t = self.settings.theme
        return self._system_dark if t == "system" else t == "dark"

    darkTheme = Property(bool, _dark, notify=themeChanged)

    def set_system_dark(self, dark: bool) -> None:
        if dark != self._system_dark:
            self._system_dark = dark
            self.themeChanged.emit()

    @Slot(result="QVariantMap")
    def settingsMap(self) -> dict:  # noqa: N802
        d = asdict(self.settings)
        d.pop("local_groups")
        d["start_at_login"] = autostart.is_enabled()
        return d

    @Slot("QVariantMap")
    def saveSettings(self, values: dict) -> None:  # noqa: N802
        """Apply settings from the Settings window; reconnect if the account changed."""
        old_account = (self.settings.jid, self.settings.password, self.settings.server, self.settings.port,
                       self.settings.resource, self.settings.require_tls)
        for k, v in values.items():
            if hasattr(self.settings, k) and k != "local_groups":
                cur = getattr(self.settings, k)
                setattr(self.settings, k, type(cur)(v) if cur is not None else v)
        if self.settings.theme not in config.THEMES:
            self.settings.theme = "system"
        self._persist()
        try:
            autostart.set_enabled(self.settings.start_at_login)
        except OSError as exc:
            self._on_error(f"Could not update autostart entry: {exc}")
        self.themeChanged.emit()
        self.settingsChanged.emit()
        new_account = (self.settings.jid, self.settings.password, self.settings.server, self.settings.port,
                       self.settings.resource, self.settings.require_tls)
        if new_account != old_account and self.connection_state != "offline":
            self.logout()
            self.login()

    def _persist(self) -> None:
        self.settings.local_groups = self.roster.local_groups()
        try:
            config.save(self.settings, self._settings_path)
        except OSError as exc:
            log.error("saving settings failed: %s", exc)

    # ================================================================ connection
    def start(self) -> None:
        if self.settings.auto_login and self.settings.has_credentials():
            self.login()
        elif not self.settings.has_credentials():
            self.notifications.add("info", "No account configured yet — open Settings to enter your Jabber ID.")

    @Slot()
    def login(self) -> None:
        if not self.settings.has_credentials():
            self.notifications.add("info", "Enter your Jabber ID and password in Settings first.")
            self.openSettings()
            return
        self._wanted_online = True
        self.backend.login(self.settings)

    @Slot()
    def logout(self) -> None:
        self._wanted_online = False
        self.backend.logout()

    @Slot(str)
    def setPresence(self, presence: str) -> None:  # noqa: N802
        if presence not in PRESENCES:
            return
        if presence == "offline":
            self.logout()
            return
        self._presence = presence
        if self.connection_state == "online":
            self.backend.set_presence(presence)
        else:
            self.login()
        self.presenceChanged.emit()

    def _on_state(self, state: str) -> None:
        self.connection_state = state
        if state == "online" and self._presence != "available":
            self.backend.set_presence(self._presence)
        if state == "offline":
            self.roster.set_all_offline()
            for s in self.sessions.values():
                s.set_remote_typing(False)
            if self._wanted_online and self.settings.auto_reconnect:
                self.notifications.add("error", "Disconnected — reconnecting…")
                QTimer.singleShot(3000, lambda: self._wanted_online and self.connection_state == "offline"
                                  and self.backend.login(self.settings))
        self.stateChanged.emit()
        self.presenceChanged.emit()

    def _on_error(self, text: str) -> None:
        self.notifications.add("error", text)
        self.notify.emit(f"{APP_NAME}: problem", text, "")
        if "Authentication failed" in text:
            self._wanted_online = False  # retrying with a bad password would only lock the account

    # ================================================================ roster
    def _on_roster(self, items: list) -> None:
        self.roster.set_contacts([Contact(jid=i["jid"], name=i.get("name", ""), groups=list(i.get("groups", [])),
                                          subscription=i.get("subscription", "both")) for i in items])

    def _on_contact(self, item: dict) -> None:
        self.roster.upsert(Contact(jid=item["jid"], name=item.get("name", ""), groups=list(item.get("groups", [])),
                                   subscription=item.get("subscription", "both")))

    def _on_presence(self, jid: str, presence: str, status: str) -> None:
        self.roster.update_presence(jid, presence, status)
        if jid in self.sessions:
            self.sessions[jid].changed.emit()

    def _on_roster_changed(self) -> None:
        for s in self.sessions.values():
            s.changed.emit()
        self.unreadChanged.emit()

    @Slot(str, result=bool)
    def hasContact(self, jid: str) -> bool:  # noqa: N802
        return self.roster.contact(jid) is not None

    @Slot(str, str, str)
    def addContact(self, jid: str, name: str, group: str) -> None:  # noqa: N802
        jid = jid.strip().lower()
        if "@" not in jid or self.roster.contact(jid):
            return
        self.backend.add_contact(jid, name.strip(), [group] if group else [])

    @Slot(list)
    def removeContacts(self, jids: list) -> None:  # noqa: N802
        for jid in jids:
            self.backend.remove_contact(jid)

    @Slot(list)
    def removeAndBlock(self, jids: list) -> None:  # noqa: N802
        for jid in jids:
            self.backend.block_contact(jid)
            self.backend.remove_contact(jid)

    @Slot(str)
    def addGroup(self, name: str) -> None:  # noqa: N802
        name = name.strip()
        if name and name not in self.roster.groups():
            self.roster.set_local_groups(self.roster.local_groups() + [name])
            self._persist()

    @Slot(list)
    def removeGroups(self, names: list) -> None:  # noqa: N802
        """Delete groups; their members stay in the roster (moved to their remaining groups or Ungrouped)."""
        names = set(names)
        for c in self.roster.contacts():
            if names & set(c.groups):
                self.backend.set_contact_groups(c.jid, c.name, [g for g in c.groups if g not in names])
        self.roster.set_local_groups([g for g in self.roster.local_groups() if g not in names])
        self._persist()

    @Slot(str, str, str)
    def moveContact(self, jid: str, from_group: str, to_group: str) -> None:  # noqa: N802
        c = self.roster.contact(jid)
        if c is None or from_group == to_group:
            return
        groups = [g for g in c.groups if g != from_group]
        if to_group != UNGROUPED and to_group not in groups:
            groups.append(to_group)
        self.backend.set_contact_groups(jid, c.name, groups)

    @Slot(str, str, str, str)
    def groupTogether(self, jid: str, from_group: str, target_jid: str, name: str) -> None:  # noqa: N802
        """Dropping a contact onto another one: create group *name* containing both."""
        name = name.strip()
        target = self.roster.contact(target_jid)
        if not name or target is None or jid == target_jid:
            return
        if name not in target.groups:
            self.backend.set_contact_groups(target_jid, target.name, target.groups + [name])
        self.moveContact(jid, from_group, name)

    @Slot(str, str)
    def renameContact(self, jid: str, name: str) -> None:  # noqa: N802
        c = self.roster.contact(jid)
        if c is not None:
            self.backend.set_contact_groups(jid, name.strip(), c.groups)

    # ================================================================ subscriptions / notifications
    def _on_subscription(self, jid: str, msg: str) -> None:
        text = f"{jid} wants to add you to their contacts." + (f"\n“{msg}”" if msg else "")
        self.notifications.add("subscription", text, jid)
        self.notify.emit("Authorization request", text, "")

    @Slot(str)
    def acceptSubscription(self, nid: str) -> None:  # noqa: N802
        item = self.notifications.get(nid)
        if item:
            self.backend.answer_subscription(item["jid"], True)
            self.notifications.remove(nid)

    @Slot(str, bool)
    def declineSubscription(self, nid: str, block: bool = False) -> None:  # noqa: N802
        """Decline with presence type="unsubscribed" so the server drops the pending request — see docs/comments-details.md [2]."""
        item = self.notifications.get(nid)
        if item:
            self.backend.answer_subscription(item["jid"], False)
            if block:
                self.backend.block_contact(item["jid"])
            self.notifications.remove(nid)

    @Slot(str)
    def dismissNotification(self, nid: str) -> None:  # noqa: N802
        self.notifications.remove(nid)

    @Slot()
    def clearNotifications(self) -> None:  # noqa: N802
        self.notifications.remove_where(kind="error")
        self.notifications.remove_where(kind="info")

    # ================================================================ messages
    def session(self, jid: str) -> ChatSession:
        s = self.sessions.get(jid)
        if s is None:
            s = ChatSession(self, jid, self)
            s.closed.connect(self._on_session_closed)
            self.sessions[jid] = s
        return s

    def _on_message(self, jid: str, body: str, encrypted: bool) -> None:
        s = self.session(jid)
        s.receive(body, encrypted)
        win = self.windows.get(jid)
        if win is None or not win.property("active"):
            self.roster.add_unread(jid)
            c = self.roster.contact(jid)
            self.notify.emit(c.display_name if c else jid, body[:200], jid)

    def _on_chat_state(self, jid: str, state: str) -> None:
        if jid in self.sessions:
            self.sessions[jid].set_remote_typing(state == "composing")

    def mark_read(self, jid: str) -> None:
        c = self.roster.contact(jid)
        if c is not None and c.unread:
            self.roster.add_unread(jid, 0)

    def _on_session_closed(self, jid: str) -> None:
        s = self.sessions.pop(jid, None)
        w = self.windows.pop(jid, None)
        if w is not None:
            w.deleteLater()
        if s is not None:
            for tid in s.transfers:
                self._transfers.pop(tid, None)
            s.deleteLater()
        self.mark_read(jid)

    # ================================================================ file transfer
    def register_transfer(self, t: Transfer) -> None:
        self._transfers[t.tid] = t

    def unique_download_path(self, name: str) -> Path:
        folder = Path(self.settings.download_dir or config.default_download_dir()).expanduser()
        name = Path(name).name or "download"  # never trust a remote path
        dest = folder / name
        stem, suffix, n = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = folder / f"{stem} ({n}){suffix}"
            n += 1
        return dest

    def _on_file_offer(self, tid: str, jid: str, name: str, size: int) -> None:
        s = self.session(jid)
        t = Transfer(jid, Path(name).name, size, incoming=True, tid=tid, parent=s)
        self.register_transfer(t)
        s.add_transfer(t)
        win = self.windows.get(jid)
        if win is None or not win.property("active"):
            self.roster.add_unread(jid)
            c = self.roster.contact(jid)
            self.notify.emit(c.display_name if c else jid, f"Wants to send you “{t.fileName}” ({t.sizeText})", jid)

    def _on_transfer_progress(self, tid: str, done: int) -> None:
        t = self._transfers.get(tid)
        if t is not None:
            t.update(done=done)

    def _on_transfer_state(self, tid: str, state: str, detail: str) -> None:
        t = self._transfers.get(tid)
        if t is None:
            return
        if state == "completed" and t.incoming and detail and detail != t.path:
            state = "failed"  # backend reports a save error in *detail*
        t.update(state=state, detail=detail)

    # ================================================================ windows
    def _create_window(self, qml: str, props: dict) -> QObject | None:
        if self.engine is None:
            return None
        comp = self._components.get(qml)
        if comp is None:
            comp = self._components[qml] = QQmlComponent(self.engine, QUrl.fromLocalFile(str(QML_DIR / qml)))
        obj = comp.createWithInitialProperties({"app": self} | props)
        if obj is None:
            for e in comp.errors():
                log.error("%s", e.toString())
            return None
        QQmlEngine.setObjectOwnership(obj, QQmlEngine.CppOwnership)  # we delete windows ourselves
        obj.setParent(None)
        return obj

    @Slot(str)
    def openChat(self, jid: str) -> None:  # noqa: N802
        s = self.session(jid)
        win = self.windows.get(jid)
        if win is None:
            win = self._create_window("ChatWindow.qml", {"session": s})
            if win is None:
                return
            self.windows[jid] = win
            s.window_shown()
        self._show(win)
        self.mark_read(jid)

    @Slot()
    def openFirstUnread(self) -> None:  # noqa: N802
        for c in self.roster.contacts():
            if c.unread:
                self.openChat(c.jid)
                return

    def _open_singleton(self, key: str, qml: str) -> None:
        win = self._singletons.get(key)
        if win is None:
            win = self._create_window(qml, {})
            if win is None:
                return
            self._singletons[key] = win
        self._show(win)

    @Slot()
    def openContacts(self) -> None:  # noqa: N802
        self._open_singleton("contacts", "ContactsWindow.qml")

    @Slot()
    def openSettings(self) -> None:  # noqa: N802
        self._open_singleton("settings", "SettingsWindow.qml")

    @Slot()
    def openNotifications(self) -> None:  # noqa: N802
        self._open_singleton("notifications", "NotificationsWindow.qml")

    @staticmethod
    def _show(win: QObject) -> None:
        win.setProperty("visible", True)
        if win.property("visibility") == 3:  # QWindow.Minimized
            win.setProperty("visibility", 2)
        if hasattr(win, "raise_"):
            win.raise_()
        if hasattr(win, "requestActivate"):
            win.requestActivate()

    @Slot(str)
    def copyText(self, text: str) -> None:  # noqa: N802
        QGuiApplication.clipboard().setText(text)

    @Slot()
    def quit(self) -> None:
        QGuiApplication.quit()

    def shutdown(self) -> None:
        """Called on quit / session logout: say goodbye to the server and drop all chats."""
        for s in list(self.sessions.values()):
            s.windowClosed()
        if self.connection_state != "offline":
            self.logout()
        self._persist()

