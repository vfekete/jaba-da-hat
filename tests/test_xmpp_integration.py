"""XmppBackend against a real (throw-away) Prosody — run with: JDH_XMPP_TEST=1 uv run pytest tests/test_xmpp_integration.py

The fixture recreates the container with tools/test-server.sh, so every run starts from empty accounts.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import wait_until

ROOT = Path(__file__).resolve().parent.parent
PORT = 15222
PASSWORD = "test-password"  # test-only account in a throw-away container

pytestmark = pytest.mark.skipif(not os.environ.get("JDH_XMPP_TEST"), reason="set JDH_XMPP_TEST=1 (needs docker)")


@pytest.fixture(scope="module")
def server():
    subprocess.run([str(ROOT / "tools/test-server.sh"), "start"], check=True, capture_output=True)
    yield str(ROOT / "build/prosody/ca.crt")
    subprocess.run([str(ROOT / "tools/test-server.sh"), "stop"], check=False, capture_output=True)


class Recorder:
    """Collects every signal of a backend."""

    def __init__(self, backend):
        self.b = backend
        self.state = "offline"
        self.events: list[tuple] = []
        for name in ("stateChanged", "error", "loginRejected", "rosterReceived", "contactUpdated", "contactRemoved",
                     "presenceReceived", "messageReceived", "messageSentElsewhere", "chatStateReceived", "subscriptionRequested", "fileOffered", "transferProgress",
                     "transferStateChanged", "transferInfo", "notice", "encryptionAvailable", "trustRequested"):
            getattr(backend, name).connect(lambda *a, n=name: self._on(n, a))

    def _on(self, name, args):
        if name == "stateChanged":
            self.state = args[0]
        self.events.append((name, *args))

    def of(self, name):
        return [e[1:] for e in self.events if e[0] == name]

    def wait(self, cond, timeout_ms=8000):
        assert wait_until(cond, timeout_ms), self.events


def make(qapp, server, user, password=PASSWORD, **kw):
    from jaba_da_hat import config
    from jaba_da_hat.backend.xmpp import XmppBackend

    b = XmppBackend(ca_file=server)
    s = config.Settings(jid=f"{user}@localhost", password=password, server="127.0.0.1", port=PORT, **kw)
    return b, s, Recorder(b)


@pytest.fixture
def pair(qapp, server):
    made = []

    def factory(user, **kw):
        b, s, r = make(qapp, server, user, **kw)
        made.append(b)
        return b, s, r

    yield factory
    for b in made:
        b.logout()
        b.shutdown()


def online(b, s, r):
    b.login(s)
    r.wait(lambda: r.state == "online")
    r.wait(lambda: r.of("rosterReceived"))


def test_login_logout(pair):
    b, s, r = pair("me")
    online(b, s, r)
    b.logout()
    r.wait(lambda: r.state == "offline")
    assert not r.of("error")


def test_wrong_password_is_rejected_once(pair):
    b, s, r = pair("me", password="wrong-password")
    b.login(s)
    r.wait(lambda: r.of("loginRejected"))
    r.wait(lambda: r.state == "offline")
    assert "Authentication failed" in r.of("loginRejected")[0][0]


def test_untrusted_certificate_is_rejected(qapp, server):
    from jaba_da_hat import config
    from jaba_da_hat.backend.xmpp import XmppBackend

    b = XmppBackend()  # no test CA → the self-signed chain must not be accepted
    r = Recorder(b)
    b.login(config.Settings(jid="me@localhost", password=PASSWORD, server="127.0.0.1", port=PORT))
    try:
        r.wait(lambda: r.of("loginRejected"))
        assert "not trusted" in r.of("loginRejected")[0][0]
        assert r.state != "online"
    finally:
        b.shutdown()


def test_subscription_message_chat_state_groups(pair):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(me, ms, mr)
    online(al, as_, ar)

    al.add_contact("me@localhost", "Me", ["Friends"])
    ar.wait(lambda: any(c["jid"] == "me@localhost" for c in ar.of("contactUpdated") for c in c))
    mr.wait(lambda: mr.of("subscriptionRequested"))
    assert mr.of("subscriptionRequested")[0][0] == "alice@localhost"

    me.answer_subscription("alice@localhost", True)
    # mutual: alice's side answers our counter-request automatically, both see each other online
    mr.wait(lambda: ("alice@localhost", "available", "") in mr.of("presenceReceived"))
    ar.wait(lambda: ("me@localhost", "available", "") in ar.of("presenceReceived"))
    assert len(ar.of("subscriptionRequested")) == 0

    al.send_chat_state("me@localhost", "composing")
    mr.wait(lambda: ("alice@localhost", "composing") in mr.of("chatStateReceived"))
    al.send_message("me@localhost", "hello https://example.org", False)
    mr.wait(lambda: ("alice@localhost", "hello https://example.org", False) in mr.of("messageReceived"))

    al.set_presence("dnd", "busy")
    mr.wait(lambda: ("alice@localhost", "dnd", "busy") in mr.of("presenceReceived"))

    me.set_contact_groups("alice@localhost", "Alice", ["Work", "Friends"])
    mr.wait(lambda: any(c[0]["jid"] == "alice@localhost" and sorted(c[0]["groups"]) == ["Friends", "Work"]
                        and c[0]["name"] == "Alice" for c in mr.of("contactUpdated")))

    me.remove_contact("alice@localhost")
    mr.wait(lambda: ("alice@localhost",) in mr.of("contactRemoved"))
    assert not mr.of("error"), mr.of("error")
    assert not ar.of("error"), ar.of("error")


def test_declined_request_is_not_redelivered(pair):
    me, ms, mr = pair("me")
    bob, bs, br = pair("bob")
    online(me, ms, mr)
    online(bob, bs, br)
    bob.add_contact("me@localhost", "", [])
    mr.wait(lambda: mr.of("subscriptionRequested"))
    me.answer_subscription("bob@localhost", False)
    wait_until(lambda: False, 500)
    me.logout()
    mr.wait(lambda: mr.state == "offline")
    before = len(mr.of("subscriptionRequested"))
    online(me, ms, mr)
    wait_until(lambda: False, 1500)
    assert len(mr.of("subscriptionRequested")) == before  # RFC 6121 §3.2: the server forgot the request


def test_history_fetched_at_login(pair):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(al, as_, ar)
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    al.send_message("me@localhost", "while you were away", False)
    wait_until(lambda: False, 800)

    ms.history_since = since.isoformat(timespec="seconds")
    online(me, ms, mr)
    mr.wait(lambda: ("alice@localhost", "while you were away", False) in mr.of("messageReceived"))
    wait_until(lambda: False, 1500)
    # the same message may also come as an offline delivery: it must be shown only once
    assert mr.of("messageReceived").count(("alice@localhost", "while you were away", False)) == 1


def test_block(pair):
    me, ms, mr = pair("me")
    bob, bs, br = pair("bob")
    online(me, ms, mr)
    online(bob, bs, br)
    me.block_contact("bob@localhost")
    wait_until(lambda: False, 500)
    bob.send_message("me@localhost", "spam", False)
    wait_until(lambda: False, 1500)
    assert ("bob@localhost", "spam", False) not in mr.of("messageReceived")
    assert not mr.of("error"), mr.of("error")


def test_encrypted_send_refused_without_omemo(pair):
    me, ms, mr = pair("me")
    online(me, ms, mr)
    me.send_message("nobody@localhost", "secret", True)  # an account that never logs in: no OMEMO devices
    mr.wait(lambda: mr.of("notice"))
    assert "not sent" in mr.of("notice")[0][1] and "no OMEMO devices" in mr.of("notice")[0][1]


def test_carbons_from_my_other_device(pair):
    me, ms, mr = pair("me")
    phone, ps, pr = pair("me", resource="phone")
    online(me, ms, mr)
    online(phone, ps, pr)
    wait_until(lambda: False, 500)  # carbons are enabled right after going online
    phone.send_message("alice@localhost", "sent from the phone", False)
    mr.wait(lambda: ("alice@localhost", "sent from the phone", False) in mr.of("messageSentElsewhere"))


def _states(r, tid):
    return [e[1] for e in r.of("transferStateChanged") if e[0] == tid]


def test_http_upload_and_download_with_resume(pair, tmp_path):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(me, ms, mr)
    online(al, as_, ar)
    data = os.urandom(300_000)
    src = tmp_path / "holiday photo.jpg"
    src.write_bytes(data)

    al.use_jingle = False
    al.send_file("t1", "me@localhost", str(src))
    ar.wait(lambda: "completed" in _states(ar, "t1") or "failed" in _states(ar, "t1"))
    assert _states(ar, "t1")[-1] == "completed", ar.of("transferStateChanged")
    assert ("t1", {"can_pause": False}) in ar.of("transferInfo")

    mr.wait(lambda: mr.of("fileOffered"))
    tid, jid, name, size = mr.of("fileOffered")[0]
    assert (jid, name, size) == ("alice@localhost", "holiday photo.jpg", 0)  # nothing fetched before accepting
    assert not mr.of("messageReceived")

    # pretend an earlier attempt stopped after 100 kB: the rest must come with a Range request
    dest = tmp_path / "dl" / name
    dest.parent.mkdir()
    (tmp_path / "dl" / (name + ".part")).write_bytes(data[:100_000])
    me.accept_file(tid, str(dest))
    mr.wait(lambda: "completed" in _states(mr, tid) or "failed" in _states(mr, tid))
    assert _states(mr, tid)[-1] == "completed", mr.of("transferStateChanged")
    assert dest.read_bytes() == data
    assert not (tmp_path / "dl" / (name + ".part")).exists()
    assert (tid, {"size": 300_000}) in mr.of("transferInfo")


def test_link_with_text_stays_a_message(pair):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(me, ms, mr)
    online(al, as_, ar)
    al.send_message("me@localhost", "look: https://localhost:15281/x.png", False)
    mr.wait(lambda: mr.of("messageReceived"))
    assert not mr.of("fileOffered")


def test_upload_larger_than_server_limit_fails_clearly(pair, tmp_path):
    al, as_, ar = pair("alice")
    online(al, as_, ar)
    big = tmp_path / "big.bin"
    with open(big, "wb") as fh:
        fh.truncate(11 * 1024 * 1024)
    al.send_file("t2", "me@localhost", str(big))
    ar.wait(lambda: "failed" in _states(ar, "t2"))
    detail = [e[2] for e in ar.of("transferStateChanged") if e[0] == "t2" and e[1] == "failed"][0]
    assert "larger than your server allows" in detail


def test_cancel_download(pair, tmp_path):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(me, ms, mr)
    online(al, as_, ar)
    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(50_000))
    al.use_jingle = False
    al.send_file("t3", "me@localhost", str(src))
    mr.wait(lambda: mr.of("fileOffered"))
    tid = mr.of("fileOffered")[0][0]
    me.decline_file(tid)
    assert _states(mr, tid) == ["declined"]


def test_file_through_two_controllers(qapp, server, tmp_path):
    """Chat window → upload → link → offer card → Download → file in the download folder."""
    from PySide6.QtCore import QUrl

    from jaba_da_hat import config
    from jaba_da_hat.backend.xmpp import XmppBackend
    from jaba_da_hat.core import AppController

    ctls = []
    for user in ("me", "alice"):
        s = config.Settings(jid=f"{user}@localhost", password=PASSWORD, server="127.0.0.1", port=PORT,
                            download_dir=str(tmp_path / user / "dl"), start_at_login=False,
                            notification_style="gnome", use_encryption=False)  # the plain path
        c = AppController(XmppBackend(ca_file=server), s, tmp_path / f"{user}.json")
        c.login()
        ctls.append(c)
    me, alice = ctls
    try:
        assert wait_until(lambda: me.connection_state == alice.connection_state == "online", 8000)
        src = tmp_path / "notes.txt"
        src.write_text("hello from alice\n")
        alice.session("me@localhost").sendFile(QUrl.fromLocalFile(str(src)))
        assert wait_until(lambda: me.sessions.get("alice@localhost") and me.sessions["alice@localhost"].transfers, 8000)
        s = me.sessions["alice@localhost"]
        t = next(iter(s.transfers.values()))
        assert t.state == "offered" and t.fileName == "notes.txt" and me.pending_file_offers() == 1
        s.acceptTransfer(t)
        assert wait_until(lambda: t.finished, 8000)
        assert t.state == "completed", (t.state, t.detail)
        assert (tmp_path / "me" / "dl" / "notes.txt").read_text() == "hello from alice\n"
        sent = next(iter(alice.sessions["me@localhost"].transfers.values()))
        assert sent.state == "completed" and not sent.canPause
    finally:
        for c in ctls:
            c.shutdown()
            c.backend.shutdown()



# ---------------------------------------------------------------- Jingle file transfer

def friends(a, ar, a_jid, b, br, b_jid):
    """Make a and b mutual contacts (once per server) and wait until each sees the other online."""
    if not any(c["jid"] == b_jid and c["subscription"] == "both" for c in ar.of("rosterReceived")[-1][0]):
        a.add_contact(b_jid, "", [])
        br.wait(lambda: any(e[0] == a_jid for e in br.of("subscriptionRequested")))
        b.answer_subscription(a_jid, True)
    ar.wait(lambda: any(e[0] == b_jid and e[1] != "offline" for e in ar.of("presenceReceived")))
    br.wait(lambda: any(e[0] == a_jid and e[1] != "offline" for e in br.of("presenceReceived")))


@pytest.fixture
def me_alice(pair):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(me, ms, mr)
    online(al, as_, ar)
    friends(al, ar, "alice@localhost", me, mr, "me@localhost")
    return me, mr, al, ar


def _offer(mr, n=0):
    mr.wait(lambda: len(mr.of("fileOffered")) > n)
    return mr.of("fileOffered")[n]


def test_jingle_transfer_with_checksum(me_alice, tmp_path):
    me, mr, al, ar = me_alice
    data = os.urandom(200_000)
    src = tmp_path / "report.pdf"
    src.write_bytes(data)
    al.send_file("j1", "me@localhost", str(src))
    tid, jid, name, size = _offer(mr)
    assert tid.startswith("jingle-") and (jid, name, size) == ("alice@localhost", "report.pdf", 200_000)
    assert ("j1", {"can_pause": True}) in ar.of("transferInfo")
    dest = tmp_path / "dl" / "report.pdf"
    me.accept_file(tid, str(dest))
    mr.wait(lambda: _states(mr, tid)[-1:] in (["completed"], ["failed"]))
    assert _states(mr, tid)[-1] == "completed", mr.of("transferStateChanged")
    assert dest.read_bytes() == data
    ar.wait(lambda: _states(ar, "j1")[-1:] == ["completed"])
    assert not mr.of("error") and not ar.of("error")


def test_jingle_decline(me_alice, tmp_path):
    me, mr, al, ar = me_alice
    src = tmp_path / "x.txt"
    src.write_text("x")
    al.send_file("j2", "me@localhost", str(src))
    tid = _offer(mr)[0]
    me.decline_file(tid)
    ar.wait(lambda: "declined" in _states(ar, "j2"))
    assert _states(mr, tid) == ["declined"]


def test_jingle_sender_pause_resume(me_alice, tmp_path):
    me, mr, al, ar = me_alice
    data = os.urandom(3_000_000)
    src = tmp_path / "big.bin"
    src.write_bytes(data)
    al.send_file("j3", "me@localhost", str(src))
    tid = _offer(mr)[0]
    me.accept_file(tid, str(tmp_path / "dl" / "big.bin"))
    ar.wait(lambda: "active" in _states(ar, "j3"))
    al.pause_transfer("j3")
    ar.wait(lambda: "paused" in _states(ar, "j3"))
    wait_until(lambda: False, 400)
    got = [e[1] for e in mr.of("transferProgress") if e[0] == tid]
    stalled = got[-1] if got else 0
    wait_until(lambda: False, 600)
    got = [e[1] for e in mr.of("transferProgress") if e[0] == tid]
    assert (got[-1] if got else 0) == stalled < len(data)  # nothing moves while paused
    al.resume_transfer("j3")
    mr.wait(lambda: _states(mr, tid)[-1:] in (["completed"], ["failed"]), 20000)
    assert _states(mr, tid)[-1] == "completed", mr.of("transferStateChanged")
    assert (tmp_path / "dl" / "big.bin").read_bytes() == data


def test_jingle_receiver_pause_resume_requests_the_rest(me_alice, tmp_path):
    me, mr, al, ar = me_alice
    data = os.urandom(3_000_000)
    src = tmp_path / "video.mp4"
    src.write_bytes(data)
    al.send_file("j4", "me@localhost", str(src))
    tid = _offer(mr)[0]
    dest = tmp_path / "dl" / "video.mp4"
    me.accept_file(tid, str(dest))
    mr.wait(lambda: any(e[0] == tid and e[1] > 100_000 for e in mr.of("transferProgress")))
    me.pause_transfer(tid)
    mr.wait(lambda: "paused" in _states(mr, tid))
    ar.wait(lambda: "paused" in _states(ar, "j4"))
    part = tmp_path / "dl" / "video.mp4.part"
    assert 0 < part.stat().st_size < len(data)
    me.resume_transfer(tid)
    mr.wait(lambda: _states(mr, tid)[-1:] in (["completed"], ["failed"]), 20000)
    assert _states(mr, tid)[-1] == "completed", mr.of("transferStateChanged")
    assert dest.read_bytes() == data  # checksum verified over the old part + the requested rest
    ar.wait(lambda: _states(ar, "j4")[-1:] == ["completed"])


def test_jingle_receiver_cancel(me_alice, tmp_path):
    me, mr, al, ar = me_alice
    src = tmp_path / "c.bin"
    src.write_bytes(os.urandom(3_000_000))
    al.send_file("j5", "me@localhost", str(src))
    tid = _offer(mr)[0]
    me.accept_file(tid, str(tmp_path / "dl" / "c.bin"))
    mr.wait(lambda: any(e[0] == tid for e in mr.of("transferProgress")))
    me.cancel_transfer(tid)
    ar.wait(lambda: "cancelled" in _states(ar, "j5"))
    assert not (tmp_path / "dl" / "c.bin.part").exists() and not (tmp_path / "dl" / "c.bin").exists()


def test_offline_contact_gets_http_upload(pair, tmp_path):
    al, as_, ar = pair("alice")
    online(al, as_, ar)
    src = tmp_path / "later.txt"
    src.write_text("for when you're back")
    al.send_file("j6", "bob@localhost", str(src))  # bob is not online: no Jingle partner
    ar.wait(lambda: _states(ar, "j6")[-1:] in (["completed"], ["failed"]))
    assert _states(ar, "j6")[-1] == "completed", ar.of("transferStateChanged")
    assert ("j6", {"can_pause": False}) in ar.of("transferInfo")



# ---------------------------------------------------------------- OMEMO

def omemo_ready(b, r, peer):
    """Ask until the peer's OMEMO device list is visible (it is published shortly after login)."""
    for _ in range(20):
        b.refresh_encryption(peer)
        if wait_until(lambda: (peer, True) in r.of("encryptionAvailable"), 500):
            return
    raise AssertionError(r.events)


def answer_next_trust(b, r, n, approve=True):
    r.wait(lambda: len(r.of("trustRequested")) > n)
    window, rid, req = r.of("trustRequested")[n]
    b.answer_trust(rid, [d["id"] for d in req["devices"]] if approve else None)
    return window, req


def test_omemo_round_trip_with_per_window_trust(me_alice, tmp_path):
    from jaba_da_hat.backend.e2ee import storage_path

    me, mr, al, ar = me_alice
    omemo_ready(me, mr, "alice@localhost")
    me.send_message("alice@localhost", "top secret", True)
    window, req = answer_next_trust(me, mr, 0)
    assert window == "alice@localhost" and req["own"]
    assert any(d["jid"] == "alice@localhost" for d in req["devices"])
    assert all(len(d["fingerprint"].replace(" ", "")) == 64 for d in req["devices"])
    ar.wait(lambda: ("me@localhost", "top secret", True) in ar.of("messageReceived"))
    ar.wait(lambda: any("haven't approved" in n[1] for n in ar.of("notice")))  # alice never approved me

    me.send_message("alice@localhost", "again", True)  # same window: no question
    ar.wait(lambda: ("me@localhost", "again", True) in ar.of("messageReceived"))
    assert len(mr.of("trustRequested")) == 1

    me.forget_trust("alice@localhost")  # window closed
    me.send_message("alice@localhost", "never", True)
    answer_next_trust(me, mr, 1, approve=False)
    mr.wait(lambda: any("not sent" in n[1] for n in mr.of("notice")))
    wait_until(lambda: False, 800)
    assert ("me@localhost", "never", True) not in ar.of("messageReceived")

    # keys are on disk (0600), trust decisions are not
    f = storage_path("me@localhost")
    assert f.stat().st_mode & 0o777 == 0o600
    assert "/trust/" not in f.read_text()


def test_omemo_keys_survive_restart(pair):
    me, ms, mr = pair("me")
    al, as_, ar = pair("alice")
    online(me, ms, mr)
    online(al, as_, ar)
    friends(al, ar, "alice@localhost", me, mr, "me@localhost")
    omemo_ready(al, ar, "me@localhost")
    al.send_message("me@localhost", "before restart", True)
    answer_next_trust(al, ar, 0)
    mr.wait(lambda: ("alice@localhost", "before restart", True) in mr.of("messageReceived"))

    me.logout()
    me.shutdown()
    me2, ms2, mr2 = pair("me")  # same data dir: same identity and sessions
    online(me2, ms2, mr2)
    al.send_message("me@localhost", "after restart", True)  # alice's window is still open: no question
    mr2.wait(lambda: ("alice@localhost", "after restart", True) in mr2.of("messageReceived"))
    assert len(ar.of("trustRequested")) == 1



def test_encrypted_file_xep0454(me_alice, tmp_path):
    import ssl
    import urllib.request

    me, mr, al, ar = me_alice
    omemo_ready(me, mr, "alice@localhost")
    data = b"confidential " * 5000
    src = tmp_path / "contract.pdf"
    src.write_bytes(data)
    me.send_file("e1", "alice@localhost", str(src), True)
    answer_next_trust(me, mr, len(mr.of("trustRequested")))
    mr.wait(lambda: _states(mr, "e1")[-1:] in (["completed"], ["failed"]), 15000)
    assert _states(mr, "e1")[-1] == "completed", mr.of("transferStateChanged")

    ar.wait(lambda: any(o[2] == "contract.pdf" for o in ar.of("fileOffered")))
    tid, jid, name, _ = next(o for o in ar.of("fileOffered") if o[2] == "contract.pdf")
    assert (jid, name) == ("me@localhost", "contract.pdf")
    url = al._files[tid]["url"]
    assert url.startswith("https://") and "#" not in url  # key and IV never go to the web server
    ctx = ssl.create_default_context(cafile=str(ROOT / "build/prosody/ca.crt"))
    stored = urllib.request.urlopen(url, context=ctx).read()
    assert len(stored) == len(data) + 16 and b"confidential" not in stored  # the server holds ciphertext

    dest = tmp_path / "dl" / "contract.pdf"
    al.accept_file(tid, str(dest))
    ar.wait(lambda: _states(ar, tid)[-1:] in (["completed"], ["failed"]), 15000)
    assert _states(ar, tid)[-1] == "completed", ar.of("transferStateChanged")
    assert dest.read_bytes() == data
    assert not list((tmp_path / "dl").glob("*.encrypted*"))


def test_encrypted_chat_never_uses_jingle_or_plain_upload(me_alice, tmp_path):
    me, mr, al, ar = me_alice
    src = tmp_path / "x.txt"
    src.write_text("x")
    me.send_file("e2", "nobody@localhost", str(src), True)  # no OMEMO devices
    mr.wait(lambda: "failed" in _states(mr, "e2"))
    detail = [e[2] for e in mr.of("transferStateChanged") if e[0] == "e2" and e[1] == "failed"][0]
    assert "no OMEMO devices" in detail
