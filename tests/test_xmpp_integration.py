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
                     "presenceReceived", "messageReceived", "messageSentElsewhere", "chatStateReceived", "subscriptionRequested"):
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
    me.send_message("alice@localhost", "secret", True)
    mr.wait(lambda: mr.of("error"))
    assert "not sent" in mr.of("error")[0][0]


def test_carbons_from_my_other_device(pair):
    me, ms, mr = pair("me")
    phone, ps, pr = pair("me", resource="phone")
    online(me, ms, mr)
    online(phone, ps, pr)
    wait_until(lambda: False, 500)  # carbons are enabled right after going online
    phone.send_message("alice@localhost", "sent from the phone", False)
    mr.wait(lambda: ("alice@localhost", "sent from the phone") in mr.of("messageSentElsewhere"))
