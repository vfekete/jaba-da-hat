"""Real XMPP backend built on slixmpp.

slixmpp runs on its own asyncio loop in a worker thread; see docs/comments-details.md [13].
Calls from the UI are queued onto that loop with ``_call``; results come back through
``_emit``, which re-posts every signal emission onto the Qt main thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import itertools
import logging
import mimetypes
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from PySide6.QtCore import Qt, Signal, Slot

from jaba_da_hat.backend import aesgcm
from jaba_da_hat.backend.base import Backend
from jaba_da_hat.backend.httpfiles import HttpFiles, part_path, trust_ca_file
from jaba_da_hat.config import Settings, split_server

log = logging.getLogger(__name__)

# Our presence → XMPP <show/> (RFC 6121 §4.7.2.1); "available" has no <show/>.
SHOW = {"available": "", "away": "away", "dnd": "dnd", "xa": "xa"}
# XMPP <show/> → our presence; "chat" (free for chat) is shown as available.
PRESENCE = {"": "available", "chat": "available", "away": "away", "dnd": "dnd", "xa": "xa"}
# Which resource represents a contact when priorities are equal: the most reachable one.
_REACHABLE = ("available", "dnd", "away", "xa")
PING_CHECK_S = 10  # after a network change, a live connection must answer a ping within this
MAM_PAGE = 50
MAM_LIMIT = 500  # never pull more than this many archived messages at login
UPLOAD_NS = "urn:xmpp:http:upload:0"  # XEP-0363


def shared_file_url(msg) -> str:
    """The link of a file shared through HTTP Upload, or "" for an ordinary message — see docs/comments-details.md [20]."""
    url = (msg["oob"]["url"] or "").strip()
    body = (msg["body"] or "").strip()
    if url.lower().startswith(("https://", "http://")) and (not body or body == url):
        return url
    return ""


def single_url(body: str) -> str:
    """The URL when an (already decrypted) message body is nothing but one https or aesgcm link."""
    body = (body or "").strip()
    if body and " " not in body and "\n" not in body and body.lower().startswith(("https://", "aesgcm://")):
        return body
    return ""


def file_name_from_url(url: str) -> str:
    return Path(unquote(urlsplit(url).path)).name or "download"


def best_presence(resources: dict) -> tuple[str, str]:
    """Pick (presence, status) for a bare JID from slixmpp's per-resource presence dict."""
    if not resources:
        return "offline", ""

    def rank(r):
        p = PRESENCE.get(r.get("show", ""), "available")
        return (-int(r.get("priority", 0) or 0), _REACHABLE.index(p))

    best = min(resources.values(), key=rank)
    return PRESENCE.get(best.get("show", ""), "available"), best.get("status", "") or ""


class XmppBackend(Backend):
    _post = Signal(object)  # internal: a callable to run on the Qt main thread

    def __init__(self, parent=None, ca_file: str | None = None):
        super().__init__(parent)
        self._ca_file = ca_file
        self._post.connect(self._dispatch, Qt.QueuedConnection)
        if ca_file:
            trust_ca_file(ca_file)
        # File transfers: main thread only.
        self._http = HttpFiles(self)
        self._files: dict[str, dict] = {}
        self._link_ids = itertools.count(1)
        self._crypto_pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="jdh-decrypt")
        self.use_jingle = True  # False: always HTTP Upload (tests)
        self._encryptable: dict[str, bool] = {}  # main thread: contact has OMEMO devices
        self.encryptionAvailable.connect(self._on_encryption_available)  # before the controller's slot
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="xmpp", daemon=True)
        self._thread.start()
        # Everything below is touched only on the loop thread.
        self._client = None
        self._account: tuple | None = None
        self._settings: Settings | None = None
        self._presence = ("available", "")
        self._seen_archive_ids: set[str] = set()
        self._failure_reported = False  # one "cannot connect" notice per outage

    # ---- threading helpers -------------------------------------------
    @Slot(object)
    def _dispatch(self, fn) -> None:
        fn()

    def _emit(self, signal, *args) -> None:
        """Emit *signal* on the Qt main thread (safe to call from the loop thread)."""
        self._post.emit(lambda: signal.emit(*args))

    def _call(self, fn, *args) -> None:
        """Run *fn* on the XMPP loop thread (ignored after shutdown)."""
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(fn, *args)

    def _spawn(self, coro, failure: str) -> None:
        """Run a coroutine on the loop; report an exception as a user-visible error."""
        async def guarded():
            try:
                await coro
            except Exception as exc:  # noqa: BLE001 — every failure becomes a notification
                self._emit(self.error, f"{failure}: {_describe(exc)}")

        asyncio.ensure_future(guarded())

    # ---- connection --------------------------------------------------
    def login(self, settings: Settings) -> None:
        snapshot = Settings(**vars(settings))
        self._emit_state("connecting")
        self._call(self._login, snapshot)

    def _login(self, settings: Settings) -> None:
        account = (settings.jid, settings.password, settings.server, settings.port, settings.resource,
                   settings.require_tls)
        self._settings = settings
        if self._client is not None and account == self._account and not self._client.closing:
            if self._client.is_connected():
                self._emit_state("online")
                return
            self._client.connect(*self._address(settings))  # same account: XEP-0198 may resume the session
            return
        self._drop_client()
        self._account = account
        self._client = c = _Client(self, settings, self._ca_file)
        c.connect(*self._address(settings))

    @staticmethod
    def _address(settings: Settings) -> tuple:
        host, port = split_server(settings.server, settings.port)
        return (host, port) if host else (None, None)

    def logout(self) -> None:
        self._call(self._logout)

    def _logout(self) -> None:
        c = self._client
        self._client = None  # a deliberate logout never resumes the old session
        if c is None or not c.is_connected():
            if c is not None:
                c.closing = True
                c.cancel_connection_attempt()
            self._emit_state("offline")
            return
        c.closing = True
        c.disconnect(wait=2.0, reason="Logged off")

    def _drop_client(self) -> None:
        if self._client is not None:
            self._client.closing = True
            self._client.abort()
            self._client = None

    def set_presence(self, presence: str, status: str = "") -> None:
        self._call(self._set_presence, presence, status)

    def _set_presence(self, presence: str, status: str) -> None:
        self._presence = (presence, status)
        if self._client is not None and self._client.is_connected():
            self._client.send_presence(pshow=SHOW.get(presence, ""), pstatus=status or None)

    def check_connection(self) -> None:
        def run():
            c = self._client
            if c is None or not c.is_connected():
                return

            async def ping():
                try:
                    await c.plugin["xep_0199"].ping(timeout=PING_CHECK_S)
                except Exception as exc:  # noqa: BLE001 — no answer: the old TCP connection is dead
                    log.info("connection check failed (%s); reconnecting", _describe(exc))
                    if c is self._client:
                        c.abort()  # → disconnected → offline → the controller reconnects

            asyncio.ensure_future(ping())

        self._call(run)

    def shutdown(self, timeout: float = 3.0) -> None:
        """Close the connection (waiting up to *timeout* s for the goodbye) and stop the loop thread."""
        self._http.abort_all()
        async def close():
            c, self._client = self._client, None
            if c is not None and c.is_connected():
                c.closing = True
                await c.disconnect(wait=min(timeout, 2.0), reason="Quit")
            c = None
            gc.collect()  # finalize dropped clients while their loop still runs
            me = asyncio.current_task()
            rest = [t for t in asyncio.all_tasks() if t is not me]
            for t in rest:
                t.cancel()
            await asyncio.gather(*rest, return_exceptions=True)

        if not self._thread.is_alive() or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(close(), self._loop).result(timeout)
        except (concurrent.futures.TimeoutError, Exception) as exc:  # noqa: BLE001
            log.debug("closing the connection on quit: %s", exc)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(1.0)
        if not self._thread.is_alive():
            self._loop.close()

    # ---- called by _Client on the loop thread ------------------------
    def _emit_state(self, state: str) -> None:
        self._emit(self.stateChanged, state)

    def _is_current(self, client) -> bool:
        return client is self._client

    # ---- messaging ---------------------------------------------------
    def send_message(self, jid: str, body: str, encrypt: bool) -> None:
        if encrypt:  # never falls back to plain text — see docs/comments-details.md [22]
            self._call(self._send_encrypted, jid, body)
        else:
            self._call(self._send_message, jid, body)

    def _send_encrypted(self, jid: str, body: str) -> None:
        c = self._client
        if c is None or not c.is_connected():
            self._emit(self.notice, jid, "Message not sent: you are offline.")
            return
        state = "active" if self._settings and self._settings.send_typing else ""

        async def run():
            try:
                await c.omemo.send(jid, body, state)
            except Exception as exc:  # noqa: BLE001
                self._emit(self.notice, jid, f"⚠ Message not sent: {_describe(exc)}")

        asyncio.ensure_future(run())

    def _send_message(self, jid: str, body: str) -> None:
        c = self._online_client(f"Message to {jid} not sent: you are offline.")
        if c is None:
            return
        msg = c.make_message(mto=jid, mbody=body, mtype="chat")
        if self._settings and self._settings.send_typing:
            msg["chat_state"] = "active"
        msg.send()

    def send_chat_state(self, jid: str, state: str) -> None:
        self._call(self._send_chat_state, jid, state)

    def _send_chat_state(self, jid: str, state: str) -> None:
        c = self._client
        if c is not None and c.is_connected():
            msg = c.make_message(mto=jid, mtype="chat")
            msg["chat_state"] = state
            msg.send()

    def can_encrypt(self, jid: str) -> bool:
        return self._encryptable.get(jid, False)

    def _on_encryption_available(self, jid: str, available: bool) -> None:
        self._encryptable[jid] = available

    def refresh_encryption(self, jid: str) -> None:
        def run():
            c = self._client
            if c is None or not c.is_connected():
                return

            async def check():
                self._emit(self.encryptionAvailable, jid, await c.omemo.available(jid))

            asyncio.ensure_future(check())

        self._call(run)

    def answer_trust(self, rid: str, approved: list | None) -> None:
        self._call(lambda: self._client is not None and self._client.omemo.answer(rid, approved))

    def forget_trust(self, jid: str) -> None:
        self._call(lambda: self._client is not None and self._client.omemo.forget(jid))

    def _online_client(self, why_not: str):
        c = self._client
        if c is None or not c.is_connected():
            self._emit(self.error, why_not)
            return None
        return c

    # ---- roster ------------------------------------------------------
    def add_contact(self, jid: str, name: str, groups: list[str]) -> None:
        self._call(self._add_contact, jid, name, list(groups))

    def _add_contact(self, jid: str, name: str, groups: list[str]) -> None:
        c = self._online_client(f"Cannot add {jid}: you are offline.")
        if c is None:
            return

        async def add():
            await c.update_roster(jid, name=name, groups=groups)
            c.client_roster[jid].subscribe()  # RFC 6121 §3.1.1

        self._spawn(add(), f"Adding {jid} failed")

    def remove_contact(self, jid: str) -> None:
        self._call(self._roster_call, jid, "remove", None, None)

    def set_contact_groups(self, jid: str, name: str, groups: list[str]) -> None:
        self._call(self._roster_call, jid, "update", name, list(groups))

    def _roster_call(self, jid: str, what: str, name, groups) -> None:
        c = self._online_client(f"Cannot change {jid}: you are offline.")
        if c is None:
            return
        if what == "remove":
            self._spawn(c.del_roster_item(jid), f"Removing {jid} failed")  # the server also cancels subscriptions
        else:
            self._spawn(c.update_roster(jid, name=name, groups=groups), f"Updating {jid} failed")

    def block_contact(self, jid: str) -> None:
        self._call(self._block, jid)

    def _block(self, jid: str) -> None:
        c = self._online_client(f"Cannot block {jid}: you are offline.")
        if c is not None:
            self._spawn(c.plugin["xep_0191"].block([jid]), f"Blocking {jid} failed")  # the server pushes the new list

    def unblock_contacts(self, jids: list[str]) -> None:
        jids = list(jids)

        def run():
            c = self._online_client("Cannot unblock: you are offline.")
            if c is not None and jids:
                self._spawn(c.plugin["xep_0191"].unblock(jids), "Unblocking failed")

        self._call(run)

    def answer_subscription(self, jid: str, accept: bool) -> None:
        self._call(self._answer_subscription, jid, accept)

    def _answer_subscription(self, jid: str, accept: bool) -> None:
        c = self._online_client(f"Cannot answer {jid}: you are offline.")
        if c is None:
            return
        item = c.client_roster[jid]
        if accept:
            item.authorize()  # presence type="subscribed"
            if not item["to"] and not item["pending_out"]:
                item.subscribe()  # ask for theirs too, so both sides see each other
        else:
            item.unauthorize()  # presence type="unsubscribed": the server drops the request — see [2]

    # ---- file transfer: shared links (XEP-0066) and HTTP Upload (XEP-0363) ----
    def _file_state(self, tid: str, state: str, detail: str = "") -> None:
        f = self._files.get(tid)
        if f is not None:
            f["state"] = state
        self.transferStateChanged.emit(tid, state, detail)

    def _offer_link(self, jid: str, url: str) -> None:
        """Main thread: a contact shared a file link. Nothing is fetched until it is accepted."""
        tid = f"link{next(self._link_ids)}"
        f = {"kind": "in", "jid": jid, "url": url, "dest": "", "state": "offered"}
        if aesgcm.is_aesgcm(url):
            try:
                f["url"], f["iv"], f["key"] = aesgcm.parse(url)
            except ValueError:
                self.messageReceived.emit(jid, url, True)  # not a valid encrypted link: show it as text
                return
        self._files[tid] = f
        self.fileOffered.emit(tid, jid, file_name_from_url(f["url"]), 0)

    def _offer_jingle(self, tid: str, jid: str, name: str, size: int) -> None:
        """Main thread: a contact offers a file over Jingle."""
        self._files[tid] = {"kind": "jingle-in", "jid": jid, "state": "offered"}
        self.fileOffered.emit(tid, jid, name, size)

    def _jingle(self, method: str, tid: str, *args) -> None:
        """Forward a user action on a Jingle transfer to the loop thread."""

        def run():
            c = self._client
            if c is not None:
                getattr(c.jingle, method)(tid, *args)
            elif method in ("cancel", "decline"):
                self._post.emit(lambda: self._file_state(tid, "cancelled" if method == "cancel" else "declined"))

        self._call(run)

    def accept_file(self, tid: str, dest_path: str) -> None:
        f = self._files.get(tid)
        if f is not None and f["kind"] == "jingle-in":
            self._jingle("accept", tid, dest_path)
            return
        if f is None or f["kind"] != "in":
            return
        f["dest"] = dest_path
        self.transferInfo.emit(tid, {"can_pause": True})
        self._download(tid)

    def _download(self, tid: str) -> None:
        f = self._files[tid]
        self._file_state(tid, "active", "")

        def progress(done: int, total: int) -> None:
            if total and total != f.get("size"):
                f["size"] = total
                self.transferInfo.emit(tid, {"size": total})
            self.transferProgress.emit(tid, done)

        if "key" not in f:
            self._http.download(tid, f["url"], f["dest"], progress, lambda st, detail: self._file_state(tid, st, detail))
            return
        enc = f["dest"] + ".encrypted"  # ciphertext (resumable); decrypted into dest when complete

        def done(state: str, detail: str) -> None:
            if state != "completed":
                self._file_state(tid, state, detail)
                return
            self._file_state(tid, "active", "Decrypting…")
            fut = self._crypto_pool.submit(aesgcm.decrypt_file, enc, f["dest"], f["iv"], f["key"])

            def finished(_f) -> None:
                exc = _f.exception()
                Path(enc).unlink(missing_ok=True)
                if exc is None:
                    self._post.emit(lambda: self._file_state(tid, "completed", f["dest"]))
                else:
                    why = "The file could not be decrypted — it was altered or damaged, and was deleted."
                    self._post.emit(lambda: self._file_state(tid, "failed", why))

            fut.add_done_callback(finished)

        self._http.download(tid, f["url"], enc, progress, done)

    def decline_file(self, tid: str) -> None:
        f = self._files.get(tid)
        if f is not None and f["kind"].startswith("jingle"):
            self._jingle("decline", tid)
        elif f is not None and f["state"] == "offered":
            self._file_state(tid, "declined")  # a shared link needs no answer to the sender

    def cancel_transfer(self, tid: str) -> None:
        f = self._files.get(tid)
        if f is None or f["state"] in ("completed", "cancelled", "declined"):
            return
        if f["kind"].startswith("jingle"):
            self._jingle("cancel", tid)
        elif not self._http.abort(tid, "cancelled"):
            if f["kind"] == "in" and f["dest"]:
                part_path(f["dest"]).unlink(missing_ok=True)
                part_path(f["dest"] + ".encrypted").unlink(missing_ok=True)
            self._file_state(tid, "cancelled")

    def pause_transfer(self, tid: str) -> None:
        f = self._files.get(tid)
        if f is not None and f["kind"].startswith("jingle"):
            self._jingle("pause", tid)
        elif f is not None and f["kind"] == "in":
            self._http.abort(tid, "paused")  # the .part file stays; resume continues with a Range request

    def resume_transfer(self, tid: str) -> None:
        f = self._files.get(tid)
        if f is not None and f["kind"].startswith("jingle"):
            self._jingle("resume", tid)
        elif f is not None and f["kind"] == "in" and f["state"] in ("paused", "failed") and not self._http.busy(tid):
            self._download(tid)

    def send_file(self, tid: str, jid: str, path: str, encrypt: bool = False) -> None:
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            self.transferStateChanged.emit(tid, "failed", str(exc))
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self._files[tid] = {"kind": "out", "jid": jid, "path": path, "size": size, "type": ctype, "state": "pending"}
        self._file_state(tid, "pending", "Preparing…")
        if encrypt:  # an encrypted chat never sends a file the server could read — see [22]
            self.transferInfo.emit(tid, {"can_pause": False})
            self._call(self._send_encrypted_file, tid, jid, path)
        else:
            self._call(self._route_send, tid, jid, path, size, ctype)

    def _send_encrypted_file(self, tid: str, jid: str, path: str) -> None:
        c = self._client
        if c is None or not c.is_connected():
            self._post.emit(lambda: self._file_state(tid, "failed", "You are offline."))
            return

        async def run():
            tmp = ""
            try:
                if not await c.omemo.devices(jid):
                    raise UploadError(f"{jid} has no OMEMO devices; turn encryption off to send the file unencrypted.")
                self._post.emit(lambda: self._file_state(tid, "pending", "Encrypting…"))
                tmp, iv, key, size = await asyncio.get_running_loop().run_in_executor(None, aesgcm.encrypt_file, path)
                put_url, headers, get_url = await c.request_upload_slot(os.path.basename(path), size,
                                                                       "application/octet-stream")
                self._post.emit(lambda: self._file_state(tid, "pending", "Waiting for your approval of the devices…"))
                stanza = await c.omemo.prepare(jid, aesgcm.make_url(get_url, iv, key))
            except Exception as exc:  # noqa: BLE001
                if tmp:
                    Path(tmp).unlink(missing_ok=True)
                why = str(exc) if isinstance(exc, UploadError) else _describe(exc)
                self._post.emit(lambda: self._file_state(tid, "failed", why))
                return
            self._post.emit(lambda: self._upload(tid, put_url, headers, get_url, source=tmp,
                                                 after=lambda: self._call(stanza.send)))

        asyncio.ensure_future(run())

    def _set_kind(self, tid: str, kind: str, can_pause: bool) -> None:
        f = self._files.get(tid)
        if f is not None:
            f["kind"] = kind
        self.transferInfo.emit(tid, {"can_pause": can_pause})

    def _route_send(self, tid: str, jid: str, path: str, size: int, ctype: str) -> None:
        """Jingle when the contact's app supports it, else HTTP Upload — see docs/comments-details.md [21]."""
        c = self._client
        if c is None or not c.is_connected():
            self._post.emit(lambda: self._file_state(tid, "failed", "You are offline."))
            return

        async def run():
            full = ""
            try:
                full = await c.jingle.jingle_resource(jid) if self.use_jingle else ""
            except Exception as exc:  # noqa: BLE001
                log.debug("jingle discovery for %s: %s", jid, exc)
            if full:
                self._post.emit(lambda: self._set_kind(tid, "jingle", True))
                try:
                    await c.jingle.offer(tid, full, path, ctype)
                    return
                except Exception as exc:  # noqa: BLE001
                    log.info("jingle offer to %s refused (%s); using HTTP Upload", full, _describe(exc))
            self._post.emit(lambda: self._set_kind(tid, "out", False))  # HTTP Upload cannot pause, only cancel
            self._post.emit(lambda: self._file_state(tid, "pending", "Asking the server for an upload slot…"))
            await self._request_slot(c, tid, os.path.basename(path), size, ctype)

        asyncio.ensure_future(run())

    async def _request_slot(self, c, tid: str, name: str, size: int, ctype: str) -> None:
        try:
            put_url, headers, get_url = await c.request_upload_slot(name, size, ctype)
        except Exception as exc:  # noqa: BLE001
            why = str(exc) if isinstance(exc, UploadError) else _describe(exc)
            self._post.emit(lambda: self._file_state(tid, "failed", why))
            return
        self._post.emit(lambda: self._upload(tid, put_url, headers, get_url))

    def _upload(self, tid: str, put_url: str, headers: dict, get_url: str, source: str = "", after=None) -> None:
        """PUT the file (or its encrypted copy *source*), then share the link (or run *after*)."""
        f = self._files.get(tid)
        if f is None or f["state"] != "pending":
            if source:
                Path(source).unlink(missing_ok=True)
            return  # cancelled while waiting for the slot
        self._file_state(tid, "active", "")

        def done(state: str, detail: str) -> None:
            if source:
                Path(source).unlink(missing_ok=True)
            if state == "completed":
                if after is not None:
                    after()
                else:
                    self._call(self._send_link, f["jid"], get_url)
            self._file_state(tid, state, detail)

        self._http.upload(tid, put_url, headers, source or f["path"],
                          "application/octet-stream" if source else f["type"],
                          lambda sent, _total: self.transferProgress.emit(tid, sent), done)

    def _send_link(self, jid: str, url: str) -> None:
        c = self._online_client(f"The file was uploaded, but the link could not be sent to {jid}: you are offline.")
        if c is None:
            return
        msg = c.make_message(mto=jid, mbody=url, mtype="chat")
        msg["oob"]["url"] = url  # body == link marks it as a shared file for other clients — see [20]
        msg.send()


class UploadError(Exception):
    """A problem with HTTP Upload that is worth showing as it is."""


def _describe(exc: BaseException) -> str:
    iq = getattr(exc, "iq", None)
    if iq is not None:
        err = iq["error"]
        return err["text"] or err["condition"] or "server error"
    return str(exc) or type(exc).__name__


def _import_slixmpp():
    import slixmpp  # imported lazily: the mock backend and the tests don't need it

    return slixmpp


def _make_client_class():
    slixmpp = _import_slixmpp()

    class Client(slixmpp.ClientXMPP):
        """One XMPP account connection; lives on the backend's loop thread."""

        def __init__(self, backend: XmppBackend, settings: Settings, ca_file: str | None):
            jid = settings.jid + (f"/{settings.resource}" if settings.resource else "")
            super().__init__(jid, settings.password)
            self.backend = backend
            self.closing = False
            self._last_failure = ""
            self._history_since = settings.history_since
            if ca_file:
                self.ca_certs = ca_file
            self.enable_plaintext = not settings.require_tls
            if not settings.require_tls:
                # Without TLS allow only SCRAM, which never reveals the password — see [14].
                self.plugin["feature_mechanisms"].unencrypted_scram = True
            self.auto_authorize = None  # every request is asked about in Notifications
            self.auto_subscribe = False
            for plugin in ("xep_0030", "xep_0047", "xep_0059", "xep_0066", "xep_0085", "xep_0191", "xep_0198",
                           "xep_0199", "xep_0203", "xep_0280", "xep_0313", "xep_0359"):
                self.register_plugin(plugin)
            self.plugin["xep_0199"].enable_keepalive(interval=120, timeout=30)
            self.plugin["xep_0030"].add_feature("http://jabber.org/protocol/chatstates")
            from jaba_da_hat.backend.e2ee import Omemo
            from jaba_da_hat.backend.jingle import JingleFT

            self.jingle = JingleFT(self, backend)
            self.omemo = Omemo(self, backend)
            self._msg_lock = asyncio.Lock()  # messages are handled one by one, in arrival order

            on = self.add_event_handler
            on("session_start", self._on_session_start)
            on("session_resumed", lambda _e: self._state("online"))
            on("disconnected", self._on_disconnected)
            on("connection_failed", self._on_connection_failed)
            on("failed_all_auth", self._on_auth_failed)
            on("ssl_invalid_chain", self._on_bad_certificate)
            on("stream_error", self._on_stream_error)
            on("roster_update", self._on_roster_push)
            on("changed_status", self._on_presence)
            on("roster_subscription_request", self._on_subscription_request)
            on("message", self._on_message)
            on("carbon_received", lambda m: self._on_message(m["carbon_received"]))
            on("carbon_sent", self._on_carbon_sent)
            on("chatstate", self._on_chat_state)
            on("blocked", lambda iq: self._on_block_push(iq, "block"))
            on("unblocked", lambda iq: self._on_block_push(iq, "unblock"))

        # ---- helpers --------------------------------------------------
        @property
        def current(self) -> bool:
            return self.backend._is_current(self) and not self.closing

        def _state(self, state: str) -> None:
            if self.current or state == "offline":
                self.backend._emit_state(state)

        def _error(self, text: str) -> None:
            self.backend._emit(self.backend.error, text)

        def _reject(self, text: str) -> None:
            """A permanent failure: report it, stop this connection and don't retry automatically."""
            self.backend._emit(self.backend.loginRejected, text)
            self.closing = True
            self.cancel_connection_attempt()
            self.abort()
            self._state("offline")

        def reschedule_connection_attempt(self):
            # All addresses failed; the controller decides when to retry — see [15].
            if self._current_connection_attempt is None:
                return None
            self._current_connection_attempt = None
            if not self.closing:
                if not self.backend._failure_reported:
                    self.backend._failure_reported = True
                    self._error(f"Cannot connect to {self._target()}: {self._last_failure or 'no route'}")
                self._state("offline")
            return None

        # ---- connection events ---------------------------------------
        def _target(self) -> str:
            if self.custom_address:
                host, port = self.custom_address
                return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            return f"{self.boundjid.domain} (address looked up via DNS)"

        def _on_connection_failed(self, reason) -> None:
            text = str(reason)
            if isinstance(reason, ConnectionRefusedError):
                text = "connection refused (is the server running, is the port right?)"
            elif text.startswith("No DNS record"):  # slixmpp names the JID domain even for a manual server
                text = "host name not found" if self.custom_address else f"no DNS entry for {self.boundjid.domain}"
            self._last_failure = text

        def _on_auth_failed(self, _event) -> None:
            self._reject(f"Authentication failed for {self.boundjid.bare}: check the Jabber ID and password.")

        def _on_bad_certificate(self, exc) -> None:
            self._reject(f"The TLS certificate of {self.boundjid.domain} is not trusted ({exc}); not connecting.")

        def _on_stream_error(self, err) -> None:
            cond = err["condition"]
            if cond == "conflict":
                self._reject("Disconnected: this account logged in elsewhere with the same resource.")
            else:
                self._error(f"Server closed the connection: {err['text'] or cond}")

        def _on_disconnected(self, _reason) -> None:
            if self.backend._is_current(self) or self.closing:
                self.backend._emit_state("offline")

        async def _on_session_start(self, _event) -> None:
            if not self.current:
                return
            try:
                await self.get_roster()
            except Exception as exc:  # noqa: BLE001
                self._error(f"Could not load contacts: {_describe(exc)}")
            self.backend._emit(self.backend.rosterReceived, self._roster_items())
            self.backend._failure_reported = False
            presence, status = self.backend._presence
            self.send_presence(pshow=SHOW.get(presence, ""), pstatus=status or None)
            self._state("online")
            await self._load_block_list()
            try:
                await self.plugin["xep_0280"].enable()  # copies of chats from my other devices (XEP-0280)
            except Exception as exc:  # noqa: BLE001
                log.info("message carbons unavailable: %s", _describe(exc))
            await self._fetch_history()

        # ---- blocking (XEP-0191) ---------------------------------------
        async def _load_block_list(self) -> None:
            try:
                self._blocked = {str(j) for j in await self.plugin["xep_0191"].get_blocked_jids(timeout=15)}
            except Exception as exc:  # noqa: BLE001
                log.info("block list unavailable: %s", _describe(exc))
                self._blocked = set()
            self.backend._emit(self.backend.blockListChanged, sorted(self._blocked))

        def _on_block_push(self, iq, kind: str) -> None:
            el = iq.xml.find(f"{{urn:xmpp:blocking}}{kind}")
            items = {i.get("jid", "") for i in el.findall("{urn:xmpp:blocking}item")} if el is not None else set()
            items.discard("")
            blocked = getattr(self, "_blocked", set())
            if kind == "block":
                blocked |= items
            else:
                blocked = blocked - items if items else set()  # an empty <unblock/> means "everyone"
            self._blocked = blocked
            self.backend._emit(self.backend.blockListChanged, sorted(blocked))

        # ---- roster ---------------------------------------------------
        def _roster_items(self) -> list[dict]:
            me = self.boundjid.bare
            roster = self.client_roster
            return [_item(jid, roster[jid]) for jid in roster if jid != me]

        def _on_roster_push(self, iq) -> None:
            for jid, data in iq["roster"]["items"].items():
                jid = str(jid)
                if jid == self.boundjid.bare:
                    continue
                if data["subscription"] == "remove":
                    self.backend._emit(self.backend.contactRemoved, jid)
                else:
                    self.backend._emit(self.backend.contactUpdated, {
                        "jid": jid, "name": data["name"] or "", "groups": list(data["groups"]),
                        "subscription": data["subscription"] or "none"})

        def _on_presence(self, pres) -> None:
            jid = pres["from"].bare
            if jid == self.boundjid.bare:
                return
            presence, status = best_presence(self.client_roster.presence(jid))
            self.backend._emit(self.backend.presenceReceived, jid, presence, status)

        def _on_subscription_request(self, pres) -> None:
            jid = pres["from"].bare
            item = self.client_roster[jid]
            if item["to"] or item["pending_out"]:
                item.authorize()  # we asked them first, so this is the expected mutual request — see [16]
                return
            self.backend._emit(self.backend.subscriptionRequested, jid, pres["status"] or "")

        # ---- messages ---------------------------------------------------
        def _on_message(self, msg) -> None:
            asyncio.ensure_future(self._process(msg))

        async def _process(self, msg, archive_id: str = "") -> None:
            async with self._msg_lock:
                mtype = msg["type"]
                peer = msg["from"].bare
                if mtype == "error":
                    cond = msg["error"]["text"] or msg["error"]["condition"]
                    self._error(f"Message to {peer} could not be delivered: {cond}")
                    return
                if mtype not in ("chat", "normal", "") or peer == self.boundjid.bare:
                    return
                if not archive_id and msg["stanza_id"]["by"] == self.boundjid.bare:
                    archive_id = msg["stanza_id"]["id"]
                if archive_id:
                    if archive_id in self.backend._seen_archive_ids:
                        return  # already shown (live and from the archive)
                    self.backend._seen_archive_ids.add(archive_id)
                if self.omemo.is_encrypted(msg):
                    try:
                        body, notice = await self.omemo.decrypt(msg, peer)
                    except Exception as exc:  # noqa: BLE001
                        self._notice(peer, f"An encrypted message from {peer} could not be decrypted ({_describe(exc)}).")
                        return
                    link = single_url(body)
                    if link:  # a file shared inside the encrypted chat (XEP-0454 aesgcm:// or https)
                        self.backend._post.emit(lambda: self.backend._offer_link(peer, link))
                    elif body:
                        self.backend._emit(self.backend.messageReceived, peer, body, True)
                    if notice:
                        self._notice(peer, notice)
                    return
                link = shared_file_url(msg)
                body = msg["body"]
                if link:
                    self.backend._post.emit(lambda: self.backend._offer_link(peer, link))
                elif body:
                    self.backend._emit(self.backend.messageReceived, peer, body, False)

        def _notice(self, jid: str, text: str) -> None:
            self.backend._emit(self.backend.notice, jid, text)

        def _on_carbon_sent(self, msg) -> None:
            asyncio.ensure_future(self._process_sent(msg["carbon_sent"]))

        async def _process_sent(self, sent) -> None:
            async with self._msg_lock:
                if sent["type"] not in ("chat", "normal"):
                    return
                peer = sent["to"].bare
                body, encrypted = sent["body"], False
                if self.omemo.is_encrypted(sent):
                    try:
                        body, _notice = await self.omemo.decrypt(sent, peer)
                        encrypted = True
                    except Exception as exc:  # noqa: BLE001
                        log.info("cannot decrypt a message I sent from another device: %s", _describe(exc))
                        return
                if body:
                    self.backend._emit(self.backend.messageSentElsewhere, peer, body, encrypted)

        async def upload_service(self) -> tuple[str, int]:
            """(service JID, max size or 0) of the server's HTTP Upload component; cached per connection."""
            if getattr(self, "_upload_service", None) is None:
                self._upload_service = ("", 0)
                disco = self.plugin["xep_0030"]
                domain = self.boundjid.domain
                items = await disco.get_items(jid=domain, timeout=10)
                for jid in [domain] + [str(i[0]) for i in items["disco_items"]["items"]]:
                    info = await disco.get_info(jid=jid, timeout=10)
                    if UPLOAD_NS in info["disco_info"]["features"]:
                        self._upload_service = (jid, _max_upload_size(info))
                        break
            return self._upload_service

        async def request_upload_slot(self, name: str, size: int, ctype: str) -> tuple[str, dict, str]:
            service, limit = await self.upload_service()
            if not service:
                raise UploadError("Your server offers no file upload (XEP-0363).")
            if limit and size > limit:
                raise UploadError(f"The file is larger than your server allows ({_mb(limit)}).")
            iq = self.make_iq_get(ito=service)
            ET.SubElement(iq.xml, f"{{{UPLOAD_NS}}}request",
                          {"filename": name, "size": str(size), "content-type": ctype})
            resp = await iq.send(timeout=30)
            slot = resp.xml.find(f"{{{UPLOAD_NS}}}slot")
            put = slot.find(f"{{{UPLOAD_NS}}}put") if slot is not None else None
            get = slot.find(f"{{{UPLOAD_NS}}}get") if slot is not None else None
            if put is None or get is None or not put.get("url") or not get.get("url"):
                raise UploadError("The upload service sent an invalid slot.")
            headers = {h.get("name", ""): (h.text or "") for h in put.findall(f"{{{UPLOAD_NS}}}header")}
            return put.get("url"), headers, get.get("url")

        def _on_chat_state(self, msg) -> None:
            if msg["type"] in ("chat", "normal") and msg["chat_state"] and not msg["body"]:
                self.backend._emit(self.backend.chatStateReceived, msg["from"].bare, msg["chat_state"])

        async def _fetch_history(self) -> None:
            """Show archived messages (XEP-0313) received while we were offline — see [17]."""
            if not self._history_since:
                return
            try:
                since = datetime.fromisoformat(self._history_since)
            except ValueError:
                return
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            me = self.boundjid.bare
            try:
                async for result in self.plugin["xep_0313"].iterate(start=since, rsm={"max": MAM_PAGE}, total=MAM_LIMIT):
                    fwd = result["mam_result"]["forwarded"]["stanza"]
                    if fwd["from"].bare == me:
                        continue  # what I wrote elsewhere is shown only live (carbons)
                    await self._process(fwd, archive_id=result["mam_result"]["id"])
            except Exception as exc:  # noqa: BLE001
                log.info("server history unavailable: %s", _describe(exc))

    return Client


def _max_upload_size(info) -> int:
    for form in info["disco_info"].xml.findall("{jabber:x:data}x"):
        for fld in form.findall("{jabber:x:data}field"):
            if fld.get("var") == "max-file-size":
                value = fld.findtext("{jabber:x:data}value") or ""
                return int(value) if value.isdigit() else 0
    return 0


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def _item(jid, item) -> dict:
    return {"jid": str(jid), "name": item["name"] or "", "groups": list(item["groups"]),
            "subscription": item["subscription"] or "none"}


_client_class = None


def _Client(*args):  # noqa: N802 — factory standing in for the lazily created class
    global _client_class
    if _client_class is None:
        _client_class = _make_client_class()
    return _client_class(*args)
