"""Real XMPP backend built on slixmpp.

slixmpp runs on its own asyncio loop in a worker thread; see docs/comments-details.md [13].
Calls from the UI are queued onto that loop with ``_call``; results come back through
``_emit``, which re-posts every signal emission onto the Qt main thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import logging
import threading
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal, Slot

from jaba_da_hat.backend.base import Backend
from jaba_da_hat.config import Settings, split_server

log = logging.getLogger(__name__)

# Our presence → XMPP <show/> (RFC 6121 §4.7.2.1); "available" has no <show/>.
SHOW = {"available": "", "away": "away", "dnd": "dnd", "xa": "xa"}
# XMPP <show/> → our presence; "chat" (free for chat) is shown as available.
PRESENCE = {"": "available", "chat": "available", "away": "away", "dnd": "dnd", "xa": "xa"}
# Which resource represents a contact when priorities are equal: the most reachable one.
_REACHABLE = ("available", "dnd", "away", "xa")
MAM_PAGE = 50
MAM_LIMIT = 500  # never pull more than this many archived messages at login
NOT_YET = "File transfer is not available yet (planned for 0.3.0)."


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
        """Run *fn* on the XMPP loop thread."""
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

    def shutdown(self, timeout: float = 3.0) -> None:
        """Close the connection (waiting up to *timeout* s for the goodbye) and stop the loop thread."""
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

        if not self._thread.is_alive():
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
        if encrypt:  # never fall back to plain text silently
            self.error.emit(f"Message to {jid} not sent: OMEMO encryption is not available yet.")
            return
        self._call(self._send_message, jid, body)

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
        return False  # OMEMO arrives in 0.4.0

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
            self._spawn(c.plugin["xep_0191"].block([jid]), f"Blocking {jid} failed")

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

    # ---- file transfer (0.3.0) ---------------------------------------
    def send_file(self, tid: str, jid: str, path: str) -> None:
        self.transferStateChanged.emit(tid, "failed", NOT_YET)

    def accept_file(self, tid: str, dest_path: str) -> None:
        self.transferStateChanged.emit(tid, "failed", NOT_YET)

    def decline_file(self, tid: str) -> None:
        self.transferStateChanged.emit(tid, "declined", "")

    def cancel_transfer(self, tid: str) -> None:
        self.transferStateChanged.emit(tid, "cancelled", "")

    def pause_transfer(self, tid: str) -> None:
        pass

    def resume_transfer(self, tid: str) -> None:
        pass


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
            for plugin in ("xep_0030", "xep_0059", "xep_0066", "xep_0085", "xep_0191", "xep_0198",
                           "xep_0199", "xep_0203", "xep_0280", "xep_0313", "xep_0359"):
                self.register_plugin(plugin)
            self.plugin["xep_0199"].enable_keepalive(interval=120, timeout=30)
            self.plugin["xep_0030"].add_feature("http://jabber.org/protocol/chatstates")

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
            try:
                await self.plugin["xep_0280"].enable()  # copies of chats from my other devices (XEP-0280)
            except Exception as exc:  # noqa: BLE001
                log.info("message carbons unavailable: %s", _describe(exc))
            await self._fetch_history()

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
            mtype = msg["type"]
            peer = msg["from"].bare
            if mtype == "error":
                cond = msg["error"]["text"] or msg["error"]["condition"]
                self._error(f"Message to {peer} could not be delivered: {cond}")
                return
            if mtype not in ("chat", "normal") or peer == self.boundjid.bare:
                return
            body = msg["body"] or msg["oob"]["url"]  # a bare XEP-0066 link becomes a clickable text
            if not body:
                return
            archive_id = msg["stanza_id"]["id"] if msg["stanza_id"]["by"] == self.boundjid.bare else ""
            if archive_id:
                if archive_id in self.backend._seen_archive_ids:
                    return
                self.backend._seen_archive_ids.add(archive_id)
            self.backend._emit(self.backend.messageReceived, peer, body, False)

        def _on_carbon_sent(self, msg) -> None:
            sent = msg["carbon_sent"]
            if sent["type"] in ("chat", "normal") and sent["body"]:
                self.backend._emit(self.backend.messageSentElsewhere, sent["to"].bare, sent["body"])

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
                    aid = result["mam_result"]["id"]
                    if fwd["from"].bare == me or fwd["type"] not in ("chat", "normal", ""):
                        continue
                    body = fwd["body"] or fwd["oob"]["url"]
                    if not body or aid in self.backend._seen_archive_ids:
                        continue
                    self.backend._seen_archive_ids.add(aid)
                    self.backend._emit(self.backend.messageReceived, fwd["from"].bare, body, False)
            except Exception as exc:  # noqa: BLE001
                log.info("server history unavailable: %s", _describe(exc))

    return Client


def _item(jid, item) -> dict:
    return {"jid": str(jid), "name": item["name"] or "", "groups": list(item["groups"]),
            "subscription": item["subscription"] or "none"}


_client_class = None


def _Client(*args):  # noqa: N802 — factory standing in for the lazily created class
    global _client_class
    if _client_class is None:
        _client_class = _make_client_class()
    return _client_class(*args)
