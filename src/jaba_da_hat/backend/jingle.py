"""Jingle file transfer (XEP-0166 + XEP-0234) over In-Band Bytestreams (XEP-0261 / XEP-0047).

Runs on the XMPP loop thread, one ``JingleFT`` per client connection. Design notes, including
how pause / resume map onto Jingle: docs/comments-details.md [21].
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

NS_JINGLE = "urn:xmpp:jingle:1"
NS_JINGLE_ERRORS = "urn:xmpp:jingle:errors:1"
NS_FT = "urn:xmpp:jingle:apps:file-transfer:5"
NS_FT_ERRORS = "urn:xmpp:jingle:apps:file-transfer:errors:0"
NS_IBB = "urn:xmpp:jingle:transports:ibb:1"
NS_S5B = "urn:xmpp:jingle:transports:s5b:1"
NS_HASHES = "urn:xmpp:hashes:2"
FEATURES = (NS_JINGLE, NS_FT, NS_IBB, "urn:xmpp:hashes:2", "urn:xmpp:hash-function-text-names:sha-256")
# IANA hash names (XEP-0300) → hashlib
HASHES = {"sha-1": "sha1", "sha-256": "sha256", "sha-512": "sha512", "sha3-256": "sha3_256", "sha3-512": "sha3_512",
          "blake2b-256": None, "blake2b-512": "blake2b"}
OUR_HASH = "sha-256"
BLOCK_SIZE = 4096
MAX_BLOCK_SIZE = 8192  # what slixmpp's IBB accepts
WINDOW = 8  # IBB blocks in flight before waiting for acks
PROGRESS_EVERY_S = 0.1
CONFIRM_TIMEOUT_S = 60
PAUSED = "paused"  # <text/> of our session-terminate when the receiver pauses


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


@dataclass
class FileInfo:
    name: str = ""
    size: int = 0
    media_type: str = ""
    date: str = ""
    hash_algo: str = ""
    hash_value: str = ""  # base64, may be empty
    range_offered: bool = False
    range_offset: int = 0

    @classmethod
    def parse(cls, el: ET.Element | None) -> "FileInfo":
        f = cls()
        if el is None:
            return f
        f.name = Path(el.findtext(_q(NS_FT, "name")) or "").name
        size = el.findtext(_q(NS_FT, "size")) or ""
        f.size = int(size) if size.isdigit() else 0
        f.media_type = el.findtext(_q(NS_FT, "media-type")) or ""
        f.date = el.findtext(_q(NS_FT, "date")) or ""
        rng = el.find(_q(NS_FT, "range"))
        if rng is not None:
            f.range_offered = True
            off = rng.get("offset", "0")
            f.range_offset = int(off) if off.isdigit() else 0
        for h in list(el.findall(_q(NS_HASHES, "hash"))) + list(el.findall(_q(NS_HASHES, "hash-used"))):
            algo = h.get("algo", "")
            if algo in HASHES and HASHES[algo] and (not f.hash_algo or (h.text and not f.hash_value)):
                f.hash_algo, f.hash_value = algo, (h.text or "").strip()
        return f

    def element(self, *, offset: int | None = None, with_range: bool = False) -> ET.Element:
        el = ET.Element(_q(NS_FT, "file"))
        for tag, value in (("date", self.date), ("media-type", self.media_type), ("name", self.name),
                           ("size", str(self.size) if self.size else "")):
            if value:
                ET.SubElement(el, _q(NS_FT, tag)).text = value
        if offset:
            ET.SubElement(el, _q(NS_FT, "range"), {"offset": str(offset)})
        elif with_range:
            ET.SubElement(el, _q(NS_FT, "range"))
        if self.hash_algo:
            h = ET.SubElement(el, _q(NS_HASHES, "hash"), {"algo": self.hash_algo})
            h.text = self.hash_value or None
        return el


@dataclass
class Session:
    sid: str
    tid: str
    peer: object  # full JID
    we_initiated: bool
    we_send: bool
    file: FileInfo
    content_name: str = "a-file-offer"
    content_creator: str = "initiator"
    senders: str = "initiator"
    path: str = ""  # sender: the file; receiver: final destination
    ibb_sid: str = ""
    block_size: int = BLOCK_SIZE
    transport: str = "ibb"
    offset: int = 0
    done: int = 0
    state: str = "pending"
    stream: object = None
    task: asyncio.Task | None = None
    resume: asyncio.Event = field(default_factory=asyncio.Event)
    hasher: object = None
    fh: object = None
    last_progress: float = 0.0
    ended: bool = False

    @property
    def part(self) -> Path:
        return Path(self.path + ".part")


def hash_file(path: str, algo: str = OUR_HASH, limit: int | None = None) -> str:
    h = hashlib.new(HASHES[algo])
    with open(path, "rb") as fh:
        left = limit
        while True:
            chunk = fh.read(1 << 20 if left is None else min(1 << 20, left))
            if not chunk:
                break
            h.update(chunk)
            if left is not None:
                left -= len(chunk)
                if left <= 0:
                    break
    return base64.b64encode(h.digest()).decode()


class JingleFT:
    """Jingle file-transfer sessions of one client connection."""

    def __init__(self, client, backend):
        from slixmpp.xmlstream.handler import CoroutineCallback
        from slixmpp.xmlstream.matcher import MatchXPath

        self.c = client
        self.b = backend
        self.sessions: dict[tuple[str, str], Session] = {}  # (peer full jid, sid)
        self.by_tid: dict[str, Session] = {}
        # Files we offered, kept while their card exists so the receiver can resume them (§6.2).
        self.offered: dict[str, Session] = {}
        # Downloads that were paused or broke, with their .part kept: resume asks the sender for the rest.
        self._finished_incoming: dict[str, Session] = {}
        client.register_handler(CoroutineCallback(
            "Jingle", MatchXPath(f"{{jabber:client}}iq/{{{NS_JINGLE}}}jingle"), self._on_jingle))
        client.add_event_handler("ibb_stream_start", self._on_ibb_start)
        client.add_event_handler("ibb_stream_data", self._on_ibb_data)
        client.add_event_handler("ibb_stream_end", self._on_ibb_end)
        client.add_event_handler("disconnected", lambda _r: self.fail_all("Connection lost."))
        for feature in FEATURES:
            client.plugin["xep_0030"].add_feature(feature)

    # ================================================================ UI-facing state
    def _state(self, s: Session, state: str, detail: str = "") -> None:
        s.state = state
        b = self.b
        b._post.emit(lambda: b._file_state(s.tid, state, detail))

    def _progress(self, s: Session, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - s.last_progress >= PROGRESS_EVERY_S:
            s.last_progress = now
            self.b._emit(self.b.transferProgress, s.tid, s.done)

    def has(self, tid: str) -> bool:
        return tid in self.by_tid

    # ================================================================ discovery
    async def jingle_resource(self, bare: str) -> str:
        """A full JID of *bare* whose client speaks Jingle FT over IBB, or ""."""
        resources = self.c.client_roster.presence(bare)
        for res in sorted(resources, key=lambda r: -int(resources[r].get("priority") or 0)):
            full = f"{bare}/{res}"
            try:
                info = await self.c.plugin["xep_0030"].get_info(jid=full, timeout=8)
            except Exception as exc:  # noqa: BLE001
                log.debug("disco#info %s: %s", full, exc)
                continue
            features = set(info["disco_info"]["features"])
            if {NS_JINGLE, NS_FT, NS_IBB} <= features:
                return full
        return ""

    # ================================================================ sending
    async def offer(self, tid: str, peer_full: str, path: str, media_type: str) -> None:
        """Offer *path* to *peer_full*; raises if the peer refuses the session-initiate (caller falls back)."""
        from slixmpp import JID

        size = os.path.getsize(path)
        digest = await asyncio.get_running_loop().run_in_executor(None, hash_file, path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        info = FileInfo(name=os.path.basename(path), size=size, media_type=media_type, date=mtime,
                        hash_algo=OUR_HASH, hash_value=digest)
        s = Session(sid=_new_id(), tid=tid, peer=JID(peer_full), we_initiated=True, we_send=True, file=info,
                    path=path, ibb_sid=_new_id())
        self._add(s)
        self.offered[tid] = s
        iq = self._jingle_iq(s, "session-initiate", initiator=str(self.c.boundjid))
        content = self._content(iq, s)
        desc = ET.SubElement(content, _q(NS_FT, "description"))
        desc.append(info.element(with_range=True))
        ET.SubElement(content, _q(NS_IBB, "transport"), {"block-size": str(s.block_size), "sid": s.ibb_sid})
        try:
            await iq.send(timeout=20)
        except Exception:
            self._remove(s)
            self.offered.pop(tid, None)
            raise
        self._state(s, "pending", "Waiting for the contact to accept…")

    async def _send_data(self, s: Session) -> None:
        """Push the file over IBB from s.offset, WINDOW blocks in flight; honours pause and cancel."""
        try:
            if s.stream is None:  # we initiated: we open the bytestream (XEP-0261 §2)
                s.stream = await self.c.plugin["xep_0047"].open_stream(s.peer, block_size=s.block_size,
                                                                        sid=s.ibb_sid, timeout=20)
            s.done = s.offset
            self._state(s, "active", "")
            pending: set[asyncio.Task] = set()
            with open(s.path, "rb") as fh:
                fh.seek(s.offset)
                while not s.ended:
                    if not s.resume.is_set():
                        if pending:
                            await asyncio.gather(*pending)
                            pending.clear()
                        await s.resume.wait()
                        continue
                    chunk = fh.read(s.block_size)
                    if not chunk:
                        break
                    pending.add(asyncio.ensure_future(s.stream.send(chunk, timeout=60)))
                    s.done += len(chunk)
                    if len(pending) >= WINDOW:
                        finished, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                        for t in finished:
                            t.result()
                    self._progress(s)
                if pending:
                    await asyncio.gather(*pending)
            if s.ended:
                return
            self._progress(s, force=True)
            await s.stream.close(timeout=20)
            self._state(s, "active", "Sent — waiting for the contact to confirm…")
            await asyncio.sleep(CONFIRM_TIMEOUT_S)
            if not s.ended:  # no session-terminate came back: report what we know
                self._finish(s, "completed", "Sent (the contact's app did not confirm receipt).", terminate="success")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not s.ended:
                self._finish(s, "failed", f"Sending stopped: {_describe(exc)}", terminate="connectivity-error")

    # ================================================================ receiving
    def _accept_offer(self, s: Session) -> None:
        """Send session-accept for an incoming offer (user pressed Download)."""
        part = s.part
        offset = part.stat().st_size if part.exists() else 0
        if not s.file.range_offered or (s.file.size and offset >= s.file.size):
            offset = 0
        s.offset = offset
        self._open_part(s)
        iq = self._jingle_iq(s, "session-accept", responder=str(self.c.boundjid))
        content = self._content(iq, s)
        desc = ET.SubElement(content, _q(NS_FT, "description"))
        desc.append(s.file.element(offset=offset or None, with_range=s.file.range_offered))
        if s.transport == "ibb":
            self._preauthorize(s)
            ET.SubElement(content, _q(NS_IBB, "transport"), {"block-size": str(s.block_size), "sid": s.ibb_sid})
        else:  # SOCKS5 offered: we have no candidates, so the sender falls back to IBB (XEP-0260 §2.4)
            ET.SubElement(content, _q(NS_S5B, "transport"), {"sid": s.ibb_sid, "mode": "tcp"})
        self._state(s, "pending", "Waiting for the contact to start sending…")
        self._send_iq(iq, s)
        if s.transport == "s5b":
            info = self._jingle_iq(s, "transport-info")
            tr = ET.SubElement(self._content(info, s), _q(NS_S5B, "transport"), {"sid": s.ibb_sid})
            ET.SubElement(tr, _q(NS_S5B, "candidate-error"))
            self._send_iq(info, s)

    def _open_part(self, s: Session) -> None:
        s.part.parent.mkdir(parents=True, exist_ok=True)
        with open(s.part, "r+b" if s.part.exists() else "wb") as fh:
            fh.truncate(s.offset)
        s.hasher = hashlib.new(HASHES[s.file.hash_algo]) if s.file.hash_algo and HASHES.get(s.file.hash_algo) else None
        if s.hasher is not None and s.offset:
            with open(s.part, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    s.hasher.update(chunk)
        s.fh = open(s.part, "ab")
        s.done = s.offset

    def _preauthorize(self, s: Session) -> None:
        ibb = self.c.plugin["xep_0047"]
        ibb._preauthorize_sid(self.c.boundjid, s.ibb_sid, s.peer, None)

    def _on_ibb_start(self, stream) -> None:
        s = self._by_ibb(stream)
        if s is None:
            return
        s.stream = stream
        if s.we_send and s.task is None:  # the peer opened the stream for a file it requested from us
            s.task = asyncio.ensure_future(self._send_data(s))
        elif not s.we_send:
            self._state(s, "active", "")

    def _on_ibb_data(self, stream) -> None:
        s = self._by_ibb(stream)
        if s is None or s.we_send or s.fh is None:
            return
        while not stream.recv_queue.empty():
            data = stream.recv_queue.get_nowait()
            if s.file.size and s.done + len(data) > s.file.size:
                self._finish(s, "failed", "The contact sent more data than announced.", terminate="media-error")
                return
            s.fh.write(data)
            if s.hasher is not None:
                s.hasher.update(data)
            s.done += len(data)
        if s.state != "active":
            self._state(s, "active", "")
        self._progress(s)

    def _on_ibb_end(self, stream) -> None:
        s = self._by_ibb(stream)
        if s is None or s.we_send or s.ended or s.fh is None:
            return
        self._on_ibb_data(stream)
        if s.ended:
            return
        s.fh.close()
        s.fh = None
        self._progress(s, force=True)
        if s.file.size and s.done < s.file.size:
            self._finish(s, "failed", f"Transfer stopped after {s.done} of {s.file.size} bytes.",
                         terminate="connectivity-error", keep_part=True)
            return
        if s.hasher is not None and s.file.hash_value:
            got = base64.b64encode(s.hasher.digest()).decode()
            if got != s.file.hash_value:
                s.part.unlink(missing_ok=True)
                self._finish(s, "failed", "The received file is damaged (checksum mismatch); it was deleted.",
                             terminate="media-error")
                return
        dest = s.path
        os.replace(s.part, dest)
        self._finish(s, "completed", dest, terminate="success")

    # ================================================================ user actions (from the backend)
    def accept(self, tid: str, dest: str) -> None:
        s = self.by_tid.get(tid)
        if s is not None and not s.we_send and s.state == "offered":
            s.path = dest
            self._accept_offer(s)

    def decline(self, tid: str) -> None:
        s = self.by_tid.get(tid)
        if s is not None and s.state == "offered":
            self._finish(s, "declined", "", terminate="decline")

    def cancel(self, tid: str) -> None:
        s = self.by_tid.get(tid)
        if s is not None:
            self._finish(s, "cancelled", "", terminate="cancel")
        elif tid in self._finished_incoming:
            old = self._finished_incoming.pop(tid)
            old.part.unlink(missing_ok=True)
            self.b._post.emit(lambda: self.b._file_state(tid, "cancelled", ""))
        elif tid in self.offered and self.offered[tid].state in ("paused", "failed"):
            self.offered.pop(tid)
            self.b._post.emit(lambda: self.b._file_state(tid, "cancelled", ""))
        else:
            self.offered.pop(tid, None)

    def pause(self, tid: str) -> None:
        s = self.by_tid.get(tid)
        if s is None or s.state not in ("active", "pending") or (s.we_send and s.state != "active"):
            return
        if s.we_send:
            s.resume.clear()  # just stop sending blocks; the session stays open
            self._state(s, "paused", "")
        else:
            # Jingle has no pause: end the session, keep the .part, ask for the rest later — see [21].
            self._finish(s, "paused", "", terminate="cancel", text=PAUSED, keep_part=True)

    def resume(self, tid: str) -> None:
        s = self.by_tid.get(tid)
        if s is not None and s.we_send and s.state == "paused":
            s.resume.set()
            self._state(s, "active", "")
            return
        old = self.offered.get(tid) if s is None else None
        if old is not None and old.state in ("failed", "paused"):
            asyncio.ensure_future(self._reoffer(old))
            return
        old = self._finished_incoming.get(tid) if s is None else None
        if old is not None:
            asyncio.ensure_future(self._request_rest(old))

    async def _reoffer(self, old: Session) -> None:
        try:
            await self.offer(old.tid, str(old.peer), old.path, old.file.media_type)
        except Exception as exc:  # noqa: BLE001
            why = f"Could not offer again: {_describe(exc)}"
            self.b._post.emit(lambda: self.b._file_state(old.tid, "failed", why))

    async def _request_rest(self, old: Session) -> None:
        """Ask the sender for the rest of a paused or broken download (XEP-0234 §6.2 + §6.4)."""
        from slixmpp import JID

        part = old.part
        offset = part.stat().st_size if part.exists() else 0
        s = Session(sid=_new_id(), tid=old.tid, peer=JID(str(old.peer)), we_initiated=True, we_send=False,
                    file=old.file, content_name="a-file-request", senders="responder", path=old.path,
                    ibb_sid=_new_id())
        s.offset = offset
        self._finished_incoming.pop(old.tid, None)
        self._add(s)
        iq = self._jingle_iq(s, "session-initiate", initiator=str(self.c.boundjid))
        content = self._content(iq, s)
        desc = ET.SubElement(content, _q(NS_FT, "description"))
        desc.append(s.file.element(offset=offset or None, with_range=True))
        ET.SubElement(content, _q(NS_IBB, "transport"), {"block-size": str(s.block_size), "sid": s.ibb_sid})
        self._state(s, "pending", "Asking the contact to send the rest…")
        try:
            await iq.send(timeout=20)
        except Exception as exc:  # noqa: BLE001
            self._remove(s)
            self._finished_incoming[s.tid] = s
            self._state(s, "failed", f"Cannot resume: {_describe(exc)}. Ask the contact to send the file again.")

    def fail_all(self, why: str) -> None:
        for s in list(self.by_tid.values()):
            self._finish(s, "failed", why, keep_part=True, notify_peer=False)

    # ================================================================ incoming Jingle IQs
    async def _on_jingle(self, iq) -> None:
        from slixmpp.exceptions import XMPPError

        j = iq.xml.find(_q(NS_JINGLE, "jingle"))
        action, sid = j.get("action", ""), j.get("sid", "")
        peer = iq["from"]
        if iq["type"] != "set":
            return
        if action == "session-initiate":
            if (str(peer), sid) in self.sessions:
                raise XMPPError("bad-request")
            iq.reply().send()
            self._on_initiate(peer, sid, j)
            return
        s = self.sessions.get((str(peer), sid))
        if s is None:
            raise XMPPError("item-not-found", etype="cancel", extension="unknown-session",
                            extension_ns=NS_JINGLE_ERRORS)
        iq.reply().send()
        handler = {
            "session-accept": self._on_session_accept,
            "session-terminate": self._on_terminate,
            "session-info": self._on_session_info,
            "transport-replace": self._on_transport_replace,
            "transport-info": lambda *_: None,  # SOCKS5 candidate reports: we only speak IBB
            "transport-accept": lambda *_: None,
        }.get(action)
        if handler is not None:
            handler(s, j)
        elif action.startswith("content-"):
            self._finish(s, "failed", "The contact's app changed the transfer in an unsupported way.",
                         terminate="failed-application")

    def _on_initiate(self, peer, sid: str, j: ET.Element) -> None:
        content = j.find(_q(NS_JINGLE, "content"))
        desc = content.find(_q(NS_FT, "description")) if content is not None else None
        if desc is None:
            self._terminate_raw(peer, sid, "unsupported-applications")
            return
        file = FileInfo.parse(desc.find(_q(NS_FT, "file")))
        ibb = content.find(_q(NS_IBB, "transport"))
        s5b = content.find(_q(NS_S5B, "transport"))
        if ibb is None and s5b is None:
            self._terminate_raw(peer, sid, "unsupported-transports")
            return
        senders = content.get("senders", "initiator")
        s = Session(sid=sid, tid="", peer=peer, we_initiated=False, we_send=(senders == "responder"), file=file,
                    content_name=content.get("name", ""), content_creator=content.get("creator", "initiator"),
                    senders=senders, transport="ibb" if ibb is not None else "s5b",
                    ibb_sid=(ibb if ibb is not None else s5b).get("sid", ""))
        if ibb is not None:
            bs = ibb.get("block-size", "")
            s.block_size = min(int(bs), MAX_BLOCK_SIZE) if bs.isdigit() and int(bs) > 0 else BLOCK_SIZE
        if s.we_send:
            self._on_request(s)
            return
        s.tid = f"jingle-{secrets.token_hex(6)}"
        s.state = "offered"
        self._add(s)
        bare, b, name, size, tid = peer.bare, self.b, file.name or "file", file.size, s.tid
        b._post.emit(lambda: b._offer_jingle(tid, bare, name, size))

    def _on_request(self, s: Session) -> None:
        """The peer asks for a file we offered earlier (resume after pause or failure)."""
        match = None
        for old in self.offered.values():
            same_peer = str(old.peer) == str(s.peer) or old.peer.bare == s.peer.bare
            same_file = (s.file.hash_value and s.file.hash_value == old.file.hash_value) or \
                (not s.file.hash_value and s.file.name == old.file.name and s.file.size == old.file.size)
            if same_peer and same_file and old.tid not in self.by_tid:
                match = old
        if match is None or not os.path.exists(match.path):
            self._terminate_raw(s.peer, s.sid, "failed-application", app_error="file-not-available")
            return
        requested = s.file.range_offset
        s.tid, s.path, s.file = match.tid, match.path, match.file
        s.offset = requested if 0 < requested < s.file.size else 0
        s.resume.set()
        self._add(s)
        self.offered[s.tid] = s
        self._preauthorize(s)
        iq = self._jingle_iq(s, "session-accept", responder=str(self.c.boundjid))
        content = self._content(iq, s)
        desc = ET.SubElement(content, _q(NS_FT, "description"))
        desc.append(s.file.element(offset=s.offset or None, with_range=True))
        ET.SubElement(content, _q(NS_IBB, "transport"), {"block-size": str(s.block_size), "sid": s.ibb_sid})
        self._state(s, "pending", "The contact asked for the rest of the file…")
        self._send_iq(iq, s)

    def _on_session_accept(self, s: Session, j: ET.Element) -> None:
        if not s.we_initiated or s.state not in ("pending",):
            return
        content = j.find(_q(NS_JINGLE, "content"))
        ibb = content.find(_q(NS_IBB, "transport")) if content is not None else None
        if ibb is None:
            self._finish(s, "failed", "The contact's app accepted with a transport we don't support.",
                         terminate="unsupported-transports")
            return
        bs = ibb.get("block-size", "")
        if bs.isdigit() and 0 < int(bs) < s.block_size:
            s.block_size = int(bs)
        if s.we_send:
            desc = content.find(_q(NS_FT, "description"))
            theirs = FileInfo.parse(desc.find(_q(NS_FT, "file")) if desc is not None else None)
            s.offset = theirs.range_offset if 0 < theirs.range_offset < s.file.size else 0
            s.resume.set()
            s.task = asyncio.ensure_future(self._send_data(s))
        else:  # our request was accepted: we initiated, so we open the bytestream and they send over it
            self._open_part(s)
            asyncio.ensure_future(self._open_for_receiving(s))

    async def _open_for_receiving(self, s: Session) -> None:
        try:
            s.stream = await self.c.plugin["xep_0047"].open_stream(s.peer, block_size=s.block_size,
                                                                    sid=s.ibb_sid, timeout=20)
            self._state(s, "active", "")
        except Exception as exc:  # noqa: BLE001
            self._finish(s, "failed", f"Could not open the data stream: {_describe(exc)}",
                         terminate="connectivity-error", keep_part=True)

    def _on_transport_replace(self, s: Session, j: ET.Element) -> None:
        content = j.find(_q(NS_JINGLE, "content"))
        ibb = content.find(_q(NS_IBB, "transport")) if content is not None else None
        if ibb is None:
            iq = self._jingle_iq(s, "transport-reject")
            self._send_iq(iq, s)
            return
        s.transport, s.ibb_sid = "ibb", ibb.get("sid", "")
        bs = ibb.get("block-size", "")
        s.block_size = min(int(bs), MAX_BLOCK_SIZE) if bs.isdigit() and int(bs) > 0 else BLOCK_SIZE
        self._preauthorize(s)
        iq = self._jingle_iq(s, "transport-accept")
        ET.SubElement(self._content(iq, s), _q(NS_IBB, "transport"),
                      {"block-size": str(s.block_size), "sid": s.ibb_sid})
        self._send_iq(iq, s)

    def _on_session_info(self, s: Session, j: ET.Element) -> None:
        checksum = j.find(_q(NS_FT, "checksum"))
        if checksum is not None:
            info = FileInfo.parse(checksum.find(_q(NS_FT, "file")))
            if info.hash_value and (not s.file.hash_algo or info.hash_algo == s.file.hash_algo):
                s.file.hash_algo, s.file.hash_value = info.hash_algo, info.hash_value
        elif j.find(_q(NS_FT, "received")) is not None and s.we_send:
            self._finish(s, "completed", "", notify_peer=False)

    def _on_terminate(self, s: Session, j: ET.Element) -> None:
        reason_el = j.find(_q(NS_JINGLE, "reason"))
        reason = ""
        text = ""
        if reason_el is not None:
            for child in reason_el:
                tag = child.tag.split("}")[-1]
                if tag == "text":
                    text = child.text or ""
                elif not reason:
                    reason = tag
        if reason == "success":
            self._finish(s, "completed", "" if s.we_send else s.path, notify_peer=False)
        elif reason == "decline":
            self._finish(s, "declined", "", notify_peer=False)
        elif reason == "cancel" and text == PAUSED and s.we_send:
            self._finish(s, "paused", "Paused by the contact.", notify_peer=False)
        elif reason == "cancel":
            self._finish(s, "cancelled", "Cancelled by the contact.", notify_peer=False)
        else:
            self._finish(s, "failed", _reason_text(reason, text, j), notify_peer=False, keep_part=True)

    # ================================================================ session plumbing
    def _add(self, s: Session) -> None:
        self.sessions[(str(s.peer), s.sid)] = s
        self.by_tid[s.tid] = s

    def _remove(self, s: Session) -> None:
        self.sessions.pop((str(s.peer), s.sid), None)
        if self.by_tid.get(s.tid) is s:
            self.by_tid.pop(s.tid, None)

    def _by_ibb(self, stream) -> Session | None:
        for s in self.by_tid.values():
            if s.ibb_sid == stream.sid and str(s.peer) == str(stream.peer_jid):
                return s
        return None

    def _finish(self, s: Session, state: str, detail: str, *, terminate: str = "", text: str = "",
                keep_part: bool = False, notify_peer: bool = True) -> None:
        if s.ended:
            return
        s.ended = True
        s.resume.set()  # wake a paused sender so its task can end
        self._remove(s)
        if notify_peer and terminate:
            self._terminate_raw(s.peer, s.sid, terminate, text=text)
        if s.task is not None and s.task is not asyncio.current_task() and not s.task.done():
            s.task.cancel()
        if s.stream is not None and not s.stream.stream_out_closed and state != "completed":
            try:
                s.stream.close()
            except Exception:  # noqa: BLE001, S110 — the peer may already be gone
                pass
        if s.fh is not None:
            s.fh.close()
            s.fh = None
        if not s.we_send:
            if state in ("paused", "failed") and keep_part:
                self._finished_incoming[s.tid] = s
            elif state != "completed" and s.path:
                s.part.unlink(missing_ok=True)
        self._progress(s, force=True)
        self._state(s, state, detail)

    def _terminate_raw(self, peer, sid: str, reason: str, *, text: str = "", app_error: str = "") -> None:
        iq = self.c.make_iq_set(ito=peer)
        j = ET.SubElement(iq.xml, _q(NS_JINGLE, "jingle"), {"action": "session-terminate", "sid": sid})
        r = ET.SubElement(j, _q(NS_JINGLE, "reason"))
        ET.SubElement(r, _q(NS_JINGLE, reason))
        if text:
            ET.SubElement(r, _q(NS_JINGLE, "text")).text = text
        if app_error:
            ET.SubElement(r, _q(NS_FT_ERRORS, app_error))
        fut = iq.send(timeout=15)
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())  # the peer may be gone; nothing to do

    def _jingle_iq(self, s: Session, action: str, **attrs):
        iq = self.c.make_iq_set(ito=s.peer)
        ET.SubElement(iq.xml, _q(NS_JINGLE, "jingle"), {"action": action, "sid": s.sid, **attrs})
        return iq

    def _content(self, iq, s: Session) -> ET.Element:
        j = iq.xml.find(_q(NS_JINGLE, "jingle"))
        return ET.SubElement(j, _q(NS_JINGLE, "content"),
                             {"creator": s.content_creator, "name": s.content_name, "senders": s.senders})

    def _send_iq(self, iq, s: Session) -> None:
        fut = iq.send(timeout=20)

        def done(f):
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None and not s.ended:
                self._finish(s, "failed", f"The contact's app refused: {_describe(exc)}", notify_peer=False,
                             keep_part=True)

        fut.add_done_callback(done)


def _new_id() -> str:
    return secrets.token_hex(8)


def _describe(exc: BaseException) -> str:
    iq = getattr(exc, "iq", None)
    if iq is not None:
        err = iq["error"]
        return err["text"] or err["condition"] or "error"
    return str(exc) or type(exc).__name__


def _reason_text(reason: str, text: str, j: ET.Element) -> str:
    if j.find(f".//{_q(NS_FT_ERRORS, 'file-not-available')}") is not None:
        return "The contact no longer offers this file. Ask them to send it again."
    known = {
        "connectivity-error": "The connection between you and the contact broke.",
        "media-error": "The contact's app reported a problem with the file.",
        "failed-application": "The contact's app could not handle the transfer.",
        "failed-transport": "No way to transfer data between you and the contact.",
        "unsupported-transports": "The contact's app supports none of our transfer methods.",
        "timeout": "The contact did not answer in time.",
        "gone": "The contact went offline.",
        "busy": "The contact is busy.",
        "security-error": "The contact's app refused for security reasons.",
    }
    return (known.get(reason) or f"Transfer ended: {reason or 'unknown reason'}") + (f" ({text})" if text else "")
