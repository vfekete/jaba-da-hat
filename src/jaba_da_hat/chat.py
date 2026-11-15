"""Chat sessions: per-contact conversation state backing one chat window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Property, Signal, Slot
from PySide6.QtGui import QDesktopServices

from jaba_da_hat.models import MessageModel, Transfer

PAUSED_AFTER_MS = 5000  # XEP-0085 §5.1: "paused" after a few seconds without typing
REMOTE_TYPING_TIMEOUT_MS = 30000


class ChatSession(QObject):
    changed = Signal()
    typingChanged = Signal()
    encryptedChanged = Signal()
    closed = Signal(str)
    raiseRequested = Signal()

    def __init__(self, app, jid: str, parent=None):
        super().__init__(parent)
        self._app = app
        self._jid = jid
        self._messages = MessageModel(self)
        self._remote_typing = False
        self._encrypted = app.settings.use_encryption and app.backend.can_encrypt(jid)
        self._local_state = "active"
        self._window_open = False
        self._idle = QTimer(self, singleShot=True, interval=PAUSED_AFTER_MS)
        self._idle.timeout.connect(lambda: self._set_local_state("paused"))
        self._remote_timeout = QTimer(self, singleShot=True, interval=REMOTE_TYPING_TIMEOUT_MS)
        self._remote_timeout.timeout.connect(lambda: self.set_remote_typing(False))
        self.transfers: dict[str, Transfer] = {}

    # ---- properties --------------------------------------------------
    def _contact(self):
        return self._app.roster.contact(self._jid)

    jid = Property(str, lambda s: s._jid, constant=True)
    name = Property(str, lambda s: s._contact().display_name if s._contact() else s._jid, notify=changed)
    presence = Property(str, lambda s: s._contact().presence if s._contact() else "offline", notify=changed)
    statusText = Property(str, lambda s: s._contact().status_text if s._contact() else "", notify=changed)
    messages = Property(QObject, lambda s: s._messages, constant=True)
    remoteTyping = Property(bool, lambda s: s._remote_typing, notify=typingChanged)
    canEncrypt = Property(bool, lambda s: s._app.backend.can_encrypt(s._jid), notify=changed)

    def _get_encrypted(self):
        return self._encrypted

    def _set_encrypted(self, value: bool):
        value = bool(value) and self._app.backend.can_encrypt(self._jid)
        if value != self._encrypted:
            self._encrypted = value
            self._messages.add_system("Encryption (OMEMO) enabled." if value else
                                      "Encryption disabled — messages are protected only by TLS to the server.")
            self.encryptedChanged.emit()

    encrypted = Property(bool, _get_encrypted, _set_encrypted, notify=encryptedChanged)

    @Slot(result=int)
    def activeTransferCount(self) -> int:  # noqa: N802
        return sum(1 for t in self.transfers.values() if t.state in ("pending", "active", "paused"))

    @property
    def window_open(self) -> bool:
        return self._window_open

    # ---- incoming ----------------------------------------------------
    def receive(self, body: str, encrypted: bool) -> None:
        self.set_remote_typing(False)
        self._messages.add_text("in", body, encrypted)

    def set_remote_typing(self, typing: bool) -> None:
        if not self._app.settings.show_typing:
            typing = False
        if typing:
            self._remote_timeout.start()
        if typing != self._remote_typing:
            self._remote_typing = typing
            self.typingChanged.emit()

    def add_transfer(self, transfer: Transfer) -> None:
        self.transfers[transfer.tid] = transfer
        self._messages.add_transfer(transfer)

    # ---- outgoing ----------------------------------------------------
    @Slot(str)
    def send(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._idle.stop()
        self._local_state = "active"  # XEP-0085: a message implies "active"; no separate stanza
        self._app.backend.send_message(self._jid, text, self._encrypted)
        self._messages.add_text("out", text, self._encrypted)

    @Slot(str)
    def textEdited(self, text: str) -> None:  # noqa: N802
        if not text:
            self._idle.stop()
            self._set_local_state("active")
            return
        self._set_local_state("composing")
        self._idle.start()

    def _set_local_state(self, state: str) -> None:
        if state == self._local_state:
            return
        self._local_state = state
        if self._app.settings.send_typing and self._app.connection_state == "online":
            self._app.backend.send_chat_state(self._jid, state)

    @Slot(QUrl)
    def sendFile(self, url: QUrl) -> None:  # noqa: N802
        path = url.toLocalFile()
        if not path:
            return
        p = Path(path)
        size = p.stat().st_size if p.exists() else 0
        t = Transfer(self._jid, p.name, size, incoming=False, parent=self)
        t.path = path
        self._app.register_transfer(t)
        self.add_transfer(t)
        self._app.backend.send_file(t.tid, self._jid, path)

    @Slot(QObject)
    def acceptTransfer(self, t: Transfer) -> None:  # noqa: N802
        dest = self._app.unique_download_path(t.fileName)
        t.path = str(dest)
        t.update(state="pending", detail=f"Saving to {dest}")
        self._app.backend.accept_file(t.tid, str(dest))

    @Slot(QObject)
    def declineTransfer(self, t: Transfer) -> None:  # noqa: N802
        self._app.backend.decline_file(t.tid)

    @Slot(QObject)
    def cancelTransfer(self, t: Transfer) -> None:  # noqa: N802
        self._app.backend.cancel_transfer(t.tid)

    @Slot(QObject)
    def pauseTransfer(self, t: Transfer) -> None:  # noqa: N802
        self._app.backend.pause_transfer(t.tid)

    @Slot(QObject)
    def resumeTransfer(self, t: Transfer) -> None:  # noqa: N802
        self._app.backend.resume_transfer(t.tid)

    @Slot(QObject)
    def openTransferFolder(self, t: Transfer) -> None:  # noqa: N802
        if t.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(t.path).parent)))

    @Slot(str)
    def openLink(self, url: str) -> None:  # noqa: N802
        """Links open in the user's browser only when clicked; nothing is prefetched."""
        QDesktopServices.openUrl(QUrl(url))

    # ---- window lifecycle --------------------------------------------
    @Slot(bool)
    def setWindowActive(self, active: bool) -> None:  # noqa: N802
        if active:
            self._app.mark_read(self._jid)

    def window_shown(self) -> None:
        self._window_open = True

    @Slot()
    def windowClosed(self) -> None:  # noqa: N802
        """Closing the window forgets the conversation (it is never written to disk)."""
        self._window_open = False
        self._idle.stop()
        if self._app.settings.send_typing and self._app.connection_state == "online":
            self._app.backend.send_chat_state(self._jid, "gone")
        for t in self.transfers.values():
            if t.state == "offered":
                self._app.backend.decline_file(t.tid)
            elif not t.finished:
                self._app.backend.cancel_transfer(t.tid)
        self.closed.emit(self._jid)
