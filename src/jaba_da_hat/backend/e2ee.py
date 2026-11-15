"""OMEMO end-to-end encryption (XEP-0384) on top of slixmpp-omemo / python-omemo.

Keys and sessions are stored on disk (they must survive restarts); trust decisions never are.
Every device must be approved per chat window, and the approval ends when the window closes —
see docs/comments-details.md [22]. Runs on the XMPP loop thread, one ``Omemo`` per connection.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger(__name__)

TRUST_PREFIX = "/trust/"
FALLBACK_BODY = "This message is OMEMO encrypted."


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "jaba-da-hat"


def storage_path(bare_jid: str) -> Path:
    """One key store per account; the file name doesn't reveal the JID."""
    return data_dir() / "omemo" / (hashlib.sha256(bare_jid.lower().encode()).hexdigest()[:24] + ".json")


def fingerprint(identity_key: bytes) -> str:
    """The fingerprint other clients show: hex of the Curve25519 form, in groups of 8."""
    import xeddsa

    try:
        key = bytes(xeddsa.ed25519_pub_to_curve25519_pub(identity_key))
    except Exception:  # noqa: BLE001 — already Curve25519 or malformed: show what we have
        key = identity_key
    h = key.hex()
    return " ".join(h[i:i + 8] for i in range(0, len(h), 8))


def _make_storage_class():
    from omemo.storage import Just, Nothing, Storage

    class FileStorage(Storage):
        """JSON key/value store, written atomically with mode 0600; trust keys stay in memory."""

        def __init__(self, path: Path):
            super().__init__(disable_cache=True)
            self._path = path
            self._memory: dict = {}
            try:
                self._data: dict = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._data = {}

        def _src(self, key: str) -> dict:
            return self._memory if key.startswith(TRUST_PREFIX) else self._data

        async def _load(self, key: str):
            src = self._src(key)
            return Just(copy.deepcopy(src[key])) if key in src else Nothing()

        async def _store(self, key: str, value) -> None:
            self._src(key)[key] = copy.deepcopy(value)
            if not key.startswith(TRUST_PREFIX):
                self._write()

        async def _delete(self, key: str) -> None:
            if self._src(key).pop(key, None) is not None and not key.startswith(TRUST_PREFIX):
                self._write()

        def _write(self) -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self._path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
                fh.flush()
                os.fsync(fh.fileno())  # ratchet state must not be lost, or messages become undecryptable
            os.replace(tmp, self._path)

    return FileStorage


_plugin_registered = False


def _register_plugin_class() -> None:
    global _plugin_registered
    if _plugin_registered:
        return
    from slixmpp.plugins import register_plugin
    from slixmpp_omemo import XEP_0384

    storage_class = _make_storage_class()

    class JdhOmemo(XEP_0384):
        name = "xep_0384"
        default_config = {"fallback_message": FALLBACK_BODY, "storage_path": "", "controller": None}
        _jdh_storage = None

        @property
        def storage(self):
            if self._jdh_storage is None:
                self._jdh_storage = storage_class(Path(self.storage_path))
            return self._jdh_storage

        @property
        def _btbv_enabled(self) -> bool:
            return False  # no blind trust: every device is asked about

        async def _prompt_manual_trust(self, manually_trusted, identifier) -> None:
            await self.controller.ask_trust(manually_trusted, identifier)

    register_plugin(JdhOmemo)
    _plugin_registered = True


class Omemo:
    """OMEMO for one client connection: encryption, decryption and per-window trust."""

    def __init__(self, client, backend):
        _register_plugin_class()
        self.c = client
        self.b = backend
        client.use_message_ids = True
        client.register_plugin("xep_0384", {"storage_path": str(storage_path(client.boundjid.bare)),
                                            "controller": self})
        self.p = client.plugin["xep_0384"]
        # Per chat window (bare JID of the contact): device keys approved / rejected in it.
        self.approved: dict[str, set[tuple[str, str]]] = {}
        self.rejected: dict[str, set[tuple[str, str]]] = {}
        self.noticed: dict[str, set[tuple[str, str]]] = {}
        self.pending: dict[str, tuple[str, asyncio.Future]] = {}  # request id → (window, answer)

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _key(device) -> tuple[str, str]:
        return device.bare_jid, base64.b64encode(device.identity_key).decode()

    async def _sm(self):
        return await self.p.get_session_manager()

    async def own_fingerprint(self) -> str:
        sm = await self._sm()
        own = await sm.get_own_device_information()
        return fingerprint(own[0].identity_key)

    async def devices(self, bare: str) -> list:
        from slixmpp import JID

        await self.p.refresh_device_lists({JID(bare)})
        sm = await self._sm()
        own_id = (await sm.get_own_device_information())[0].device_id
        return [d for d in await sm.get_device_information(bare)
                if any(active for _ns, active in d.active) and not (bare == self.c.boundjid.bare and d.device_id == own_id)]

    async def available(self, bare: str) -> bool:
        try:
            return bool(await self.devices(bare))
        except Exception as exc:  # noqa: BLE001
            log.info("OMEMO device list of %s unavailable: %s", bare, exc)
            return False

    # ---- trust ----------------------------------------------------------
    async def _apply_window_trust(self, window: str) -> None:
        """Set every device's trust to what *this* window decided; everything else is undecided."""
        from slixmpp_omemo import TrustLevel

        sm = await self._sm()
        approved, rejected = self.approved.get(window, set()), self.rejected.get(window, set())
        for bare in {window, self.c.boundjid.bare}:
            for d in await sm.get_device_information(bare):
                k = self._key(d)
                level = TrustLevel.TRUSTED if k in approved else \
                    TrustLevel.DISTRUSTED if k in rejected else TrustLevel.UNDECIDED
                if d.trust_level_name != level.value:
                    await sm.set_trust(d.bare_jid, d.identity_key, level.value)

    async def ask_trust(self, devices, window) -> None:
        """Called by the library for undecided devices: ask the chat window and wait for the answer."""
        from omemo.session_manager import TrustDecisionFailed
        from slixmpp_omemo import TrustLevel

        window = window or ""
        rid = secrets.token_hex(6)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = (window, fut)
        me = self.c.boundjid.bare
        request = {
            "own": await self.own_fingerprint(),
            "devices": [{"id": f"{d.bare_jid}/{d.device_id}", "jid": d.bare_jid, "device": d.device_id,
                         "label": d.label or "", "own": d.bare_jid == me, "fingerprint": fingerprint(d.identity_key)}
                        for d in sorted(devices, key=lambda d: (d.bare_jid != window, d.bare_jid, d.device_id))],
        }
        self.b._emit(self.b.trustRequested, window, rid, request)
        try:
            answer = await fut
        finally:
            self.pending.pop(rid, None)
        if answer is None:
            raise TrustDecisionFailed("You did not approve the devices; the message was not sent.")
        sm = await self._sm()
        approved = self.approved.setdefault(window, set())
        rejected = self.rejected.setdefault(window, set())
        for d in devices:
            ok = f"{d.bare_jid}/{d.device_id}" in answer
            (approved if ok else rejected).add(self._key(d))
            await sm.set_trust(d.bare_jid, d.identity_key, (TrustLevel.TRUSTED if ok else TrustLevel.DISTRUSTED).value)

    def answer(self, rid: str, approved_ids: list[str] | None) -> None:
        item = self.pending.get(rid)
        if item is not None and not item[1].done():
            item[1].set_result(None if approved_ids is None else set(approved_ids))

    def forget(self, window: str) -> None:
        """The chat window closed: its approvals end, open questions are cancelled."""
        self.approved.pop(window, None)
        self.rejected.pop(window, None)
        self.noticed.pop(window, None)
        for rid, (w, fut) in list(self.pending.items()):
            if w == window and not fut.done():
                fut.set_result(None)

    # ---- messages -------------------------------------------------------
    def is_encrypted(self, msg) -> bool:
        return bool(self.p.is_encrypted(msg))

    async def send(self, jid: str, body: str, chat_state: str = "") -> None:
        """Encrypt *body* for every approved device of *jid* and my other devices, then send it."""
        (await self.prepare(jid, body, chat_state)).send()

    async def prepare(self, jid: str, body: str, chat_state: str = ""):
        """The encrypted stanza for *body*, ready to ``send()``; asks for device approval if needed."""
        from slixmpp import JID

        if not await self.devices(jid):
            raise RuntimeError(f"{jid} has no OMEMO devices. Turn encryption off to send this unencrypted.")
        await self._apply_window_trust(jid)
        msg = self.c.make_message(mto=jid, mtype="chat", mbody=body)
        encrypted, errors = await self.p.encrypt_message(msg, JID(jid), identifier=jid)
        if encrypted is None:
            raise RuntimeError("nothing could be encrypted")
        for err in errors:
            log.info("OMEMO: not encrypted for one device: %s", err)
        if chat_state:
            encrypted["chat_state"] = chat_state
        return encrypted

    async def decrypt(self, msg, window: str) -> tuple[str, str]:
        """Return (plaintext body, notice for the chat or "")."""
        decrypted, device = await self.p.decrypt_message(msg)
        body = decrypted["body"] or ""
        k = self._key(device)
        notice = ""
        if k not in self.approved.get(window, set()) and k not in self.noticed.setdefault(window, set()):
            self.noticed[window].add(k)
            who = "your own other device" if device.bare_jid == self.c.boundjid.bare else f"a device of {device.bare_jid}"
            notice = (f"Encrypted message from {who} that you haven't approved in this chat "
                      f"(fingerprint {fingerprint(device.identity_key)}). You'll be asked before your next "
                      f"encrypted message.")
        return body, notice
