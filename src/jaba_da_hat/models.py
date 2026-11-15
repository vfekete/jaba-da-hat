"""Qt item models and plain data objects shared by the tray menu and QML."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    Property,
    Qt,
    Signal,
    Slot,
)

from jaba_da_hat.textutil import human_size, linkify

PRESENCES = ("available", "away", "dnd", "xa", "offline")
PRESENCE_LABELS = {
    "available": "Available",
    "away": "Away",
    "dnd": "Do not disturb",
    "xa": "Extended away",
    "offline": "Offline",
}
UNGROUPED = ""


@dataclass
class Contact:
    jid: str
    name: str = ""
    groups: list[str] = field(default_factory=list)
    presence: str = "offline"
    status_text: str = ""
    subscription: str = "both"
    unread: int = 0

    @property
    def display_name(self) -> str:
        return self.name or self.jid.split("@", 1)[0]

    @property
    def online(self) -> bool:
        return self.presence != "offline"


class _RoleModel(QAbstractListModel):
    """List model whose roles are declared as a tuple of names."""

    ROLES: tuple[str, ...] = ()

    def roleNames(self):  # noqa: N802 (Qt API)
        return {Qt.UserRole + i: QByteArray(n.encode()) for i, n in enumerate(self.ROLES)}

    def _role(self, role: int) -> str | None:
        i = role - Qt.UserRole
        return self.ROLES[i] if 0 <= i < len(self.ROLES) else None


class RosterModel(_RoleModel):
    """One row per (contact, group) pair, plus a placeholder row per empty group."""

    ROLES = ("jid", "name", "group", "presence", "statusText", "unread", "placeholder", "online")
    changed = Signal()
    groupsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._contacts: dict[str, Contact] = {}
        self._local_groups: list[str] = []
        self._rows: list[tuple[Contact | None, str]] = []

    # ---- data access -------------------------------------------------
    def contacts(self) -> list[Contact]:
        return sorted(self._contacts.values(), key=lambda c: c.display_name.lower())

    def contact(self, jid: str) -> Contact | None:
        return self._contacts.get(jid)

    def groups(self) -> list[str]:
        names = set(self._local_groups)
        for c in self._contacts.values():
            names.update(c.groups)
        names.discard(UNGROUPED)
        return sorted(names, key=str.lower)

    def local_groups(self) -> list[str]:
        return list(self._local_groups)

    # ---- mutation ----------------------------------------------------
    def set_local_groups(self, groups: list[str]) -> None:
        self._local_groups = sorted({g for g in groups if g}, key=str.lower)
        self._rebuild()

    def set_contacts(self, contacts: list[Contact]) -> None:
        self._contacts = {c.jid: c for c in contacts}
        self._rebuild()

    def upsert(self, contact: Contact) -> None:
        old = self._contacts.get(contact.jid)
        if old is not None:  # roster pushes carry no presence or unread state
            contact.unread = old.unread
            contact.presence = old.presence
            contact.status_text = old.status_text
        self._contacts[contact.jid] = contact
        self._rebuild()

    def remove(self, jid: str) -> None:
        if self._contacts.pop(jid, None) is not None:
            self._rebuild()

    def update_presence(self, jid: str, presence: str, status: str) -> None:
        c = self._contacts.get(jid)
        if c is None:
            return
        c.presence, c.status_text = presence, status
        self._refresh(jid)

    def set_all_offline(self) -> None:
        for c in self._contacts.values():
            c.presence, c.status_text = "offline", ""
        self._rebuild()

    def add_unread(self, jid: str, n: int = 1) -> None:
        c = self._contacts.get(jid)
        if c is not None:
            c.unread = 0 if n == 0 else c.unread + n
            self._refresh(jid)

    def total_unread(self) -> int:
        return sum(c.unread for c in self._contacts.values())

    # ---- Qt model ----------------------------------------------------
    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        contact, group = self._rows[index.row()]
        name = self._role(role)
        if name == "group":
            return group
        if name == "placeholder":
            return contact is None
        if contact is None:
            return "" if name != "unread" else 0
        return {
            "jid": contact.jid,
            "name": contact.display_name,
            "presence": contact.presence,
            "statusText": contact.status_text,
            "unread": contact.unread,
            "online": contact.online,
        }.get(name, contact.display_name if role == Qt.DisplayRole else None)

    def _rebuild(self) -> None:
        rows: list[tuple[Contact | None, str]] = []
        used: set[str] = set()
        for c in self._contacts.values():
            for g in c.groups or [UNGROUPED]:
                rows.append((c, g))
                used.add(g)
        for g in self._local_groups:
            if g not in used:
                rows.append((None, g))
        rows.sort(key=lambda r: (r[1] == UNGROUPED, r[1].lower(), r[0] is None, r[0].display_name.lower() if r[0] else ""))
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
        self.changed.emit()
        self.groupsChanged.emit()

    def _refresh(self, jid: str) -> None:
        for i, (c, _) in enumerate(self._rows):
            if c is not None and c.jid == jid:
                idx = self.index(i)
                self.dataChanged.emit(idx, idx)
        self.changed.emit()


class Transfer(QObject):
    """One file transfer; exposed to QML as an object inside a chat message row."""

    changed = Signal()
    _ids = itertools.count(1)

    def __init__(self, jid: str, file_name: str, size: int, incoming: bool, tid: str | None = None, parent=None):
        super().__init__(parent)
        self.tid = tid or f"t{next(self._ids)}"
        self.jid = jid
        self._file_name = file_name
        self._size = size
        self._done = 0
        self._incoming = incoming
        self._state = "offered" if incoming else "pending"
        self._detail = ""
        self._can_pause = True
        self.path = ""

    def update(self, *, done: int | None = None, state: str | None = None, detail: str | None = None,
               can_pause: bool | None = None, size: int | None = None) -> None:
        if size is not None:
            self._size = size
        if done is not None:
            self._done = done
        if state is not None:
            self._state = state
        if detail is not None:
            self._detail = detail
        if can_pause is not None:
            self._can_pause = can_pause
        self.changed.emit()

    fileName = Property(str, lambda s: s._file_name, notify=changed)
    size = Property(int, lambda s: s._size, notify=changed)
    sizeText = Property(str, lambda s: human_size(s._size) if s._size > 0 else "size unknown", notify=changed)
    done = Property(int, lambda s: s._done, notify=changed)
    progress = Property(float, lambda s: (s._done / s._size) if s._size else 0.0, notify=changed)
    incoming = Property(bool, lambda s: s._incoming, notify=changed)
    # offered | pending | active | paused | completed | cancelled | failed | declined
    state = Property(str, lambda s: s._state, notify=changed)
    detail = Property(str, lambda s: s._detail, notify=changed)
    canPause = Property(bool, lambda s: s._can_pause, notify=changed)

    @property
    def finished(self) -> bool:
        return self._state in ("completed", "cancelled", "failed", "declined")


class MessageModel(_RoleModel):
    """In-memory conversation of a single open chat window."""

    ROLES = ("direction", "html", "time", "kind", "transfer", "encrypted")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        return self._rows[index.row()].get(self._role(role))

    def _append(self, row: dict) -> None:
        row.setdefault("time", datetime.now().strftime("%H:%M"))
        row.setdefault("transfer", None)
        row.setdefault("encrypted", False)
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(row)
        self.endInsertRows()

    def add_text(self, direction: str, text: str, encrypted: bool = False) -> None:
        self._append({"direction": direction, "kind": "text", "html": linkify(text), "encrypted": encrypted})

    def add_system(self, text: str) -> None:
        self._append({"direction": "system", "kind": "text", "html": linkify(text)})

    def add_transfer(self, transfer: Transfer) -> None:
        self._append({"direction": "in" if transfer.incoming else "out", "kind": "file", "html": "",
                      "transfer": transfer})

    def plain_rows(self) -> list[dict]:
        return list(self._rows)


class NotificationModel(_RoleModel):
    """Pending XMPP-related items the user should act on or acknowledge."""

    ROLES = ("nid", "kind", "jid", "text", "time")
    countChanged = Signal()
    _ids = itertools.count(1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        return self._rows[index.row()].get(self._role(role))

    def items(self) -> list[dict]:
        return list(self._rows)

    def add(self, kind: str, text: str, jid: str = "") -> str:
        """Add an item; a second subscription request from the same JID replaces the first."""
        if kind == "subscription":
            self.remove_where(kind=kind, jid=jid)
        nid = f"n{next(self._ids)}"
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append({"nid": nid, "kind": kind, "jid": jid, "text": text,
                           "time": datetime.now().strftime("%H:%M")})
        self.endInsertRows()
        self.countChanged.emit()
        return nid

    def get(self, nid: str) -> dict | None:
        return next((r for r in self._rows if r["nid"] == nid), None)

    @Slot(str)
    def remove(self, nid: str) -> None:
        self.remove_where(nid=nid)

    def remove_where(self, **match) -> None:
        for i in reversed(range(len(self._rows))):
            if all(self._rows[i].get(k) == v for k, v in match.items()):
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        self.countChanged.emit()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()
        self.countChanged.emit()

    count = Property(int, lambda s: len(s._rows), notify=countChanged)
