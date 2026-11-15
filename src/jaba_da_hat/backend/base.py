"""Interface between the UI layer and an XMPP implementation.

The UI only talks to this interface. Phase 1 ships ``MockBackend``; phase 2
adds a slixmpp-based implementation with the same signals and methods.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from jaba_da_hat.config import Settings


class Backend(QObject):
    # Connection: "offline" | "connecting" | "online"
    stateChanged = Signal(str)
    error = Signal(str)
    # The server refused the login for good (bad password, untrusted certificate, conflict): don't retry.
    loginRejected = Signal(str)
    # Roster: dicts with keys jid, name, groups, subscription
    rosterReceived = Signal(list)
    contactUpdated = Signal(dict)
    contactRemoved = Signal(str)
    presenceReceived = Signal(str, str, str)  # jid, presence, status text
    # Messaging
    messageReceived = Signal(str, str, bool)  # jid, body, encrypted
    messageSentElsewhere = Signal(str, str, bool)  # jid, body, encrypted: I wrote it on another device (XEP-0280)
    notice = Signal(str, str)  # jid, text: a line for that chat (e.g. message not sent, unapproved device)
    # OMEMO
    encryptionAvailable = Signal(str, bool)  # jid, contact has devices we can encrypt to
    # jid (chat window), request id, {"own": fingerprint, "devices": [{id, jid, device, label, own, fingerprint}]}
    trustRequested = Signal(str, str, dict)
    chatStateReceived = Signal(str, str)  # jid, "composing" | "paused" | "active" | "gone"
    subscriptionRequested = Signal(str, str)  # jid, optional message
    blockListChanged = Signal(list)  # bare JIDs blocked on the server (XEP-0191), full list each time
    # File transfer
    fileOffered = Signal(str, str, str, int)  # tid, jid, file name, size
    transferProgress = Signal(str, int)  # tid, bytes done
    transferStateChanged = Signal(str, str, str)  # tid, state, detail
    transferInfo = Signal(str, dict)  # tid, {"size": int} and/or {"can_pause": bool}, learned during the transfer

    is_mock = False

    # ---- connection --------------------------------------------------
    def login(self, settings: Settings) -> None:
        raise NotImplementedError

    def logout(self) -> None:
        raise NotImplementedError

    def set_presence(self, presence: str, status: str = "") -> None:
        raise NotImplementedError

    def check_connection(self) -> None:
        """The network changed under a live connection: verify it quickly, drop it if it is dead."""

    def shutdown(self, timeout: float = 3.0) -> None:
        """Called once when the application exits, after ``logout``; may block up to *timeout* s."""

    # ---- messaging ---------------------------------------------------
    def send_message(self, jid: str, body: str, encrypt: bool) -> None:
        raise NotImplementedError

    def send_chat_state(self, jid: str, state: str) -> None:
        raise NotImplementedError

    def can_encrypt(self, jid: str) -> bool:
        raise NotImplementedError

    def refresh_encryption(self, jid: str) -> None:
        """Look up whether *jid* has OMEMO devices; answers with ``encryptionAvailable``."""

    def answer_trust(self, rid: str, approved: list | None) -> None:
        """Answer ``trustRequested``: ids of the approved devices, or None to cancel (the message is not sent)."""

    def forget_trust(self, jid: str) -> None:
        """The chat window of *jid* closed: its device approvals end."""

    # ---- roster ------------------------------------------------------
    def add_contact(self, jid: str, name: str, groups: list[str]) -> None:
        raise NotImplementedError

    def remove_contact(self, jid: str) -> None:
        raise NotImplementedError

    def block_contact(self, jid: str) -> None:
        raise NotImplementedError

    def unblock_contacts(self, jids: list[str]) -> None:
        raise NotImplementedError

    def set_contact_groups(self, jid: str, name: str, groups: list[str]) -> None:
        raise NotImplementedError

    def answer_subscription(self, jid: str, accept: bool) -> None:
        """Accept (``subscribed``) or decline (``unsubscribed``) a pending request — RFC 6121 §3.1.4."""
        raise NotImplementedError

    # ---- file transfer -----------------------------------------------
    def send_file(self, tid: str, jid: str, path: str, encrypt: bool = False) -> None:
        raise NotImplementedError

    def accept_file(self, tid: str, dest_path: str) -> None:
        raise NotImplementedError

    def decline_file(self, tid: str) -> None:
        raise NotImplementedError

    def cancel_transfer(self, tid: str) -> None:
        raise NotImplementedError

    def pause_transfer(self, tid: str) -> None:
        raise NotImplementedError

    def resume_transfer(self, tid: str) -> None:
        raise NotImplementedError
