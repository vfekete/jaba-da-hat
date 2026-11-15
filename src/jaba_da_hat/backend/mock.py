"""Fake XMPP backend with made-up contacts, used for UI development and tests.

All JIDs live under example.org / example.net (RFC 2606 reserved domains).
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from PySide6.QtCore import QTimer

from jaba_da_hat.backend.base import Backend
from jaba_da_hat.config import Settings

MOCK_ROSTER = [
    {"jid": "alice@example.org", "name": "Alice", "groups": ["Friends"], "presence": "available",
     "status": "Out for lunch soon"},
    {"jid": "bob@example.org", "name": "Bob", "groups": ["Friends"], "presence": "away", "status": ""},
    {"jid": "carol@example.net", "name": "Carol", "groups": ["Work"], "presence": "dnd", "status": "In a meeting"},
    {"jid": "dave@example.net", "name": "Dave", "groups": ["Work"], "presence": "offline", "status": ""},
    {"jid": "erin@example.org", "name": "Erin", "groups": [], "presence": "available", "status": ""},
    {"jid": "frank@example.net", "name": "Frank", "groups": ["Work", "Friends"], "presence": "xa",
     "status": "On holiday"},
    {"jid": "grace@example.org", "name": "", "groups": [], "presence": "offline", "status": ""},
]

REPLIES = [
    "Sounds good!",
    "Have a look at https://example.org/docs and tell me what you think.",
    "I'll get back to you in a minute.",
    "Here's the picture I mentioned: https://example.org/images/cat.jpg (not loaded automatically)",
    "ok :)",
]

MOCK_CHUNK = 64 * 1024
MOCK_TICK_MS = 120


class MockBackend(Backend):
    is_mock = True

    def __init__(self, parent=None, fast: bool = False):
        super().__init__(parent)
        self._fast = fast  # tests: no artificial delays
        self._online = False
        self._roster = {c["jid"]: dict(c) for c in MOCK_ROSTER}
        self._transfers: dict[str, dict] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(MOCK_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.sent: list[tuple] = []  # outgoing stanzas, for tests
        self._approved: set[str] = set()  # chats whose devices were approved (until the window closes)
        self._held: dict[str, tuple[str, str]] = {}  # trust request id → message waiting for approval
        self.blocked: set[str] = set()

    # ---- helpers -----------------------------------------------------
    def _later(self, ms: int, fn) -> None:
        if self._fast:
            fn()
        else:
            QTimer.singleShot(ms, fn)

    # ---- connection --------------------------------------------------
    def login(self, settings: Settings) -> None:
        if self._online:
            return
        self.stateChanged.emit("connecting")

        def done():
            if settings.password == "wrong":
                self.stateChanged.emit("offline")
                self.loginRejected.emit(
                    f"Authentication failed for {settings.jid or 'account'} (mock: password 'wrong').")
                return
            self._online = True
            self.stateChanged.emit("online")
            self.rosterReceived.emit([
                {k: c[k] for k in ("jid", "name", "groups")} | {"subscription": "both"}
                for c in self._roster.values() if c["jid"] not in self.blocked
            ])
            for c in self._roster.values():
                if c["presence"] != "offline":
                    self.presenceReceived.emit(c["jid"], c["presence"], c["status"])

        self._later(600, done)

    def logout(self) -> None:
        if not self._online:
            return
        self._online = False
        self.sent.append(("presence", "unavailable"))
        for tid, t in self._transfers.items():
            if t["state"] in ("active", "paused", "pending"):
                t["state"] = "failed"
                self.transferStateChanged.emit(tid, "failed", "Disconnected")
        self.stateChanged.emit("offline")

    def set_presence(self, presence: str, status: str = "") -> None:
        self.sent.append(("presence", presence, status))

    def simulate_connection_drop(self) -> None:
        if self._online:
            self._online = False
            self.stateChanged.emit("offline")
            self.error.emit("Connection to example.org lost (mock).")

    # ---- messaging ---------------------------------------------------
    def send_message(self, jid: str, body: str, encrypt: bool) -> None:
        if encrypt and not self.can_encrypt(jid):
            self.notice.emit(jid, f"⚠ Message not sent: {jid} has no OMEMO devices (mock).")
            return
        if encrypt and jid not in self._approved:
            rid = f"r{random.randrange(1 << 30)}"
            self._held[rid] = (jid, body)
            self.trustRequested.emit(jid, rid, self._mock_devices(jid))
            return
        self._deliver(jid, body, encrypt)

    def _deliver(self, jid: str, body: str, encrypt: bool) -> None:
        self.sent.append(("message", jid, body, encrypt))
        if not self._online:
            return
        c = self._roster.get(jid)
        if c and c["presence"] != "offline":
            self._later(400, lambda: self.chatStateReceived.emit(jid, "composing"))
            self._later(1800, lambda: self.messageReceived.emit(jid, random.choice(REPLIES), encrypt))

    def send_chat_state(self, jid: str, state: str) -> None:
        self.sent.append(("chatstate", jid, state))

    def can_encrypt(self, jid: str) -> bool:
        return not jid.startswith("grace@")  # one contact without OMEMO devices, to test the UI

    def refresh_encryption(self, jid: str) -> None:
        self.encryptionAvailable.emit(jid, self.can_encrypt(jid))

    @staticmethod
    def _mock_devices(jid: str) -> dict:
        rnd = random.Random(jid)

        def fp():
            return " ".join(f"{rnd.getrandbits(32):08x}" for _ in range(8))

        return {"own": fp(), "devices": [
            {"id": f"{jid}/1111", "jid": jid, "device": 1111, "label": "Phone", "own": False, "fingerprint": fp()},
            {"id": f"{jid}/2222", "jid": jid, "device": 2222, "label": "", "own": False, "fingerprint": fp()},
            {"id": "me@example.org/3333", "jid": "me@example.org", "device": 3333, "label": "Laptop", "own": True,
             "fingerprint": fp()},
        ]}

    def answer_trust(self, rid: str, approved: list | None) -> None:
        held = self._held.pop(rid, None)
        if held is None:
            return
        jid, body = held
        if not approved or not any(i.startswith(jid + "/") for i in approved):
            self.notice.emit(jid, "⚠ Message not sent: no device of the contact was approved.")
            return
        self._approved.add(jid)
        self._deliver(jid, body, True)

    def forget_trust(self, jid: str) -> None:
        self._approved.discard(jid)

    def simulate_incoming_message(self, jid: str | None = None) -> None:
        jid = jid or random.choice([j for j, c in self._roster.items() if c["presence"] != "offline"])
        self.chatStateReceived.emit(jid, "composing")
        self._later(1200, lambda: self.messageReceived.emit(
            jid, random.choice(REPLIES + ["Hi! Are you there?", "Check www.example.net, please."]), True))

    # ---- roster ------------------------------------------------------
    def add_contact(self, jid: str, name: str, groups: list[str]) -> None:
        self.sent.append(("roster-set", jid, name, list(groups)))
        self.sent.append(("presence", "subscribe", jid))
        self._roster[jid] = {"jid": jid, "name": name, "groups": list(groups), "presence": "offline", "status": ""}
        self.contactUpdated.emit({"jid": jid, "name": name, "groups": list(groups), "subscription": "none"})
        # pretend the contact accepted after a while
        self._later(2500, lambda: jid in self._roster and self.presenceReceived.emit(jid, "available", ""))

    def remove_contact(self, jid: str) -> None:
        self.sent.append(("roster-remove", jid))
        self._roster.pop(jid, None)
        self.contactRemoved.emit(jid)

    def block_contact(self, jid: str) -> None:
        self.sent.append(("block", jid))
        self.blocked.add(jid)

    def set_contact_groups(self, jid: str, name: str, groups: list[str]) -> None:
        self.sent.append(("roster-set", jid, name, list(groups)))
        if jid in self._roster:
            self._roster[jid]["groups"] = list(groups)
        self.contactUpdated.emit({"jid": jid, "name": name, "groups": list(groups), "subscription": "both"})

    def answer_subscription(self, jid: str, accept: bool) -> None:
        self.sent.append(("presence", "subscribed" if accept else "unsubscribed", jid))
        if accept:
            self.sent.append(("presence", "subscribe", jid))  # mutual subscription
            self._roster[jid] = {"jid": jid, "name": "", "groups": [], "presence": "available", "status": ""}
            self.contactUpdated.emit({"jid": jid, "name": "", "groups": [], "subscription": "both"})
            self._later(300, lambda: self.presenceReceived.emit(jid, "available", ""))

    _strangers = iter(f"stranger{i}@example.net" for i in range(1, 10_000))

    def simulate_subscription_request(self) -> None:
        self.subscriptionRequested.emit(next(self._strangers), "Hi, please add me to your contacts.")

    # ---- file transfer -----------------------------------------------
    def simulate_file_offer(self, jid: str | None = None) -> None:
        jid = jid or "alice@example.org"
        tid = f"in{random.randrange(1 << 30)}"
        size = random.randint(30, 80) * MOCK_CHUNK
        self._transfers[tid] = {"jid": jid, "size": size, "done": 0, "state": "offered", "dest": "", "incoming": True}
        self.fileOffered.emit(tid, jid, "mock-notes.txt", size)

    def send_file(self, tid: str, jid: str, path: str, encrypt: bool = False) -> None:
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            self.transferStateChanged.emit(tid, "failed", str(exc))
            return
        self._transfers[tid] = {"jid": jid, "size": max(size, 1), "done": 0, "state": "pending", "incoming": False}
        self.sent.append(("jingle-offer", jid, os.path.basename(path)))
        self.transferStateChanged.emit(tid, "pending", "Waiting for the contact to accept…")
        self._later(1000, lambda: self._start(tid))

    def accept_file(self, tid: str, dest_path: str) -> None:
        t = self._transfers.get(tid)
        if t is None:
            return
        t["dest"] = dest_path
        self._start(tid)

    def decline_file(self, tid: str) -> None:
        t = self._transfers.get(tid)
        if t is not None:
            t["state"] = "declined"
            self.transferStateChanged.emit(tid, "declined", "")

    def cancel_transfer(self, tid: str) -> None:
        t = self._transfers.get(tid)
        if t is not None and t["state"] not in ("completed", "cancelled"):
            t["state"] = "cancelled"
            self.transferStateChanged.emit(tid, "cancelled", "")

    def pause_transfer(self, tid: str) -> None:
        t = self._transfers.get(tid)
        if t is not None and t["state"] == "active":
            t["state"] = "paused"
            self.transferStateChanged.emit(tid, "paused", "")

    def resume_transfer(self, tid: str) -> None:
        t = self._transfers.get(tid)
        if t is not None and t["state"] in ("paused", "failed"):
            self._start(tid)

    def _start(self, tid: str) -> None:
        t = self._transfers[tid]
        if t["state"] in ("cancelled", "declined", "completed"):
            return
        t["state"] = "active"
        self.transferStateChanged.emit(tid, "active", "Resumed at offset" if t["done"] else "")
        if self._fast:
            while t["state"] == "active":
                self._step(tid, t)
        elif not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        active = [(tid, t) for tid, t in self._transfers.items() if t["state"] == "active"]
        for tid, t in active:
            self._step(tid, t)
        if not active:
            self._timer.stop()

    def _step(self, tid: str, t: dict) -> None:
        t["done"] = min(t["size"], t["done"] + MOCK_CHUNK)
        self.transferProgress.emit(tid, t["done"])
        if t["done"] >= t["size"]:
            t["state"] = "completed"
            detail = ""
            if t.get("incoming") and t.get("dest"):
                detail = self._write_mock_file(Path(t["dest"]))
            self.transferStateChanged.emit(tid, "completed", detail)

    @staticmethod
    def _write_mock_file(dest: Path) -> str:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("This file was produced by the jaba-da-hat mock backend.\n", encoding="utf-8")
            return str(dest)
        except OSError as exc:
            return f"Could not save: {exc}"
