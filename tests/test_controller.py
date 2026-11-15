from conftest import wait_until

from jaba_da_hat.backend.mock import MockBackend
from jaba_da_hat.models import UNGROUPED


def test_login_populates_roster(ctl):
    ctl.login()
    assert ctl.connection_state == "online"
    assert ctl.roster.contact("alice@example.org").presence == "available"
    assert ctl.roster.contact("dave@example.net").presence == "offline"


def test_bad_password_reports_error_and_stops_retrying(ctl):
    ctl.settings.password = "wrong"
    ctl.login()
    assert ctl.connection_state == "offline"
    assert any(i["kind"] == "error" for i in ctl.notifications.items())
    assert ctl._wanted_online is False


def test_login_without_credentials_asks_for_settings(ctl):
    ctl.settings.password = ""
    ctl.login()
    assert ctl.connection_state == "offline"
    assert ctl.notifications.count == 1


def test_presence_and_logoff(ctl):
    ctl.login()
    ctl.setPresence("dnd")
    assert ("presence", "dnd", "") in ctl.backend.sent
    assert ctl.presence == "dnd"
    ctl.setPresence("offline")
    assert ctl.connection_state == "offline" and ctl.presence == "offline"
    assert ("presence", "unavailable") in ctl.backend.sent
    assert all(c.presence == "offline" for c in ctl.roster.contacts())


def test_decline_sends_unsubscribed_and_clears(ctl):
    ctl.login()
    ctl.backend.simulate_subscription_request()
    item = ctl.notifications.items()[0]
    ctl.declineSubscription(item["nid"], False)
    assert ("presence", "unsubscribed", item["jid"]) in ctl.backend.sent
    assert ctl.notifications.count == 0


def test_decline_and_block(ctl):
    ctl.login()
    ctl.backend.simulate_subscription_request()
    item = ctl.notifications.items()[0]
    ctl.declineSubscription(item["nid"], True)
    assert ("block", item["jid"]) in ctl.backend.sent


def test_accept_subscription_adds_contact(ctl):
    ctl.login()
    ctl.backend.simulate_subscription_request()
    item = ctl.notifications.items()[0]
    ctl.acceptSubscription(item["nid"])
    assert ("presence", "subscribed", item["jid"]) in ctl.backend.sent
    assert ctl.roster.contact(item["jid"]) is not None


def test_add_remove_block_contacts(ctl):
    ctl.login()
    ctl.addContact(" New@Example.org ", "Newbie", "Friends")
    assert ctl.roster.contact("new@example.org").groups == ["Friends"]
    ctl.removeContacts(["alice@example.org", "bob@example.org"])
    assert ctl.roster.contact("alice@example.org") is None and ctl.roster.contact("bob@example.org") is None
    ctl.removeAndBlock(["carol@example.net"])
    assert ("block", "carol@example.net") in ctl.backend.sent
    assert ctl.roster.contact("carol@example.net") is None


def test_groups_add_remove_keep_members(ctl, tmp_path):
    ctl.login()
    ctl.addGroup("Family")
    assert "Family" in ctl.roster.groups()
    ctl.removeGroups(["Family", "Work"])
    assert "Work" not in ctl.roster.groups() and "Family" not in ctl.roster.groups()
    frank = ctl.roster.contact("frank@example.net")
    assert frank.groups == ["Friends"]
    assert ctl.roster.contact("dave@example.net").groups == []  # kept, now ungrouped
    assert "Family" not in ctl.settings.local_groups


def test_move_and_group_together(ctl):
    ctl.login()
    ctl.moveContact("alice@example.org", "Friends", "Work")
    assert ctl.roster.contact("alice@example.org").groups == ["Work"]
    ctl.moveContact("alice@example.org", "Work", UNGROUPED)
    assert ctl.roster.contact("alice@example.org").groups == []
    ctl.groupTogether("erin@example.org", UNGROUPED, "grace@example.org", "Pals")
    assert ctl.roster.contact("erin@example.org").groups == ["Pals"]
    assert ctl.roster.contact("grace@example.org").groups == ["Pals"]


def test_incoming_message_without_window_notifies_and_counts(ctl):
    ctl.login()
    ctl.backend.messageReceived.emit("alice@example.org", "hello", True)
    assert ctl.roster.total_unread() == 1
    assert ctl.notified[-1] == ("Alice", "hello", "alice@example.org", "message")
    ctl.mark_read("alice@example.org")
    assert ctl.roster.total_unread() == 0


def test_chat_states_respect_setting(ctl):
    ctl.login()
    s = ctl.session("alice@example.org")
    s.textEdited("hel")
    assert ("chatstate", "alice@example.org", "composing") in ctl.backend.sent
    ctl.backend.sent.clear()
    ctl.settings.send_typing = False
    s.textEdited("")
    s.textEdited("x")
    assert not [x for x in ctl.backend.sent if x[0] == "chatstate"]


def test_remote_typing_hidden_when_disabled(ctl):
    ctl.login()
    s = ctl.session("alice@example.org")
    ctl.backend.chatStateReceived.emit("alice@example.org", "composing")
    assert s.remoteTyping
    ctl.settings.show_typing = False
    ctl.backend.chatStateReceived.emit("alice@example.org", "composing")
    assert not s.remoteTyping


def test_send_and_closing_forgets_conversation(ctl):
    ctl.login()
    s = ctl.session("alice@example.org")
    s.send("  hi  ")
    assert ("message", "alice@example.org", "hi", True) in ctl.backend.sent
    assert s.messages.rowCount() >= 2  # our message + mock reply (fast mode)
    s.windowClosed()
    assert "alice@example.org" not in ctl.sessions
    assert ctl.session("alice@example.org").messages.rowCount() == 0


def test_encryption_unavailable_contact(ctl):
    ctl.login()
    s = ctl.session("grace@example.org")
    assert not s.canEncrypt and not s.encrypted
    s.encrypted = True
    assert not s.encrypted


def test_incoming_file_is_not_downloaded_until_accepted(ctl, tmp_path):
    ctl.login()
    ctl.backend.simulate_file_offer("alice@example.org")
    s = ctl.sessions["alice@example.org"]
    t = next(iter(s.transfers.values()))
    assert t.state == "offered"
    assert not (tmp_path / "dl").exists()
    s.acceptTransfer(t)
    assert t.state == "completed"
    assert (tmp_path / "dl" / "mock-notes.txt").exists()
    # same name again → unique path
    assert ctl.unique_download_path("mock-notes.txt").name == "mock-notes (1).txt"
    assert ctl.unique_download_path("../../etc/passwd").parent == tmp_path / "dl"


def test_pause_resume_cancel(qapp, tmp_path):
    from jaba_da_hat import config
    from jaba_da_hat.core import AppController

    c = AppController(MockBackend(fast=False), config.Settings(jid="me@example.org", password="x",
                                                               download_dir=str(tmp_path / "dl")), tmp_path / "c.json")
    c.backend._later = lambda ms, fn: fn()  # instant login, real timer for transfer progress
    c.login()
    c.backend.simulate_file_offer("alice@example.org")
    s = c.sessions["alice@example.org"]
    t = next(iter(s.transfers.values()))
    s.acceptTransfer(t)
    assert wait_until(lambda: t.done > 0)
    s.pauseTransfer(t)
    assert t.state == "paused"
    done = t.done
    wait_until(lambda: False, 300)
    assert t.done == done  # nothing moves while paused
    s.resumeTransfer(t)
    assert t.state == "active"
    assert wait_until(lambda: t.done > done)
    s.cancelTransfer(t)
    assert t.state == "cancelled"


def test_save_settings_persists(ctl, tmp_path):
    from jaba_da_hat import config

    ctl.saveSettings({"theme": "dark", "send_typing": False, "port": 5223.0, "start_at_login": False})
    loaded = config.load(tmp_path / "cfg.json")
    assert loaded.theme == "dark" and loaded.send_typing is False and loaded.port == 5223
    assert ctl.darkTheme


def test_dropped_connection_reconnects_with_backoff_and_one_notice(ctl, monkeypatch):
    from jaba_da_hat import core

    delays = []
    monkeypatch.setattr(core.QTimer, "singleShot", lambda ms, fn: delays.append(ms))
    ctl.login()
    assert ctl.connection_state == "online"
    ctl.backend.simulate_connection_drop()
    ctl._on_state("connecting")
    ctl._on_state("offline")  # the retry failed too
    assert delays == [3000, 10000]
    assert sum("reconnecting" in n["text"] for n in ctl.notifications._rows) == 1


def test_history_cursor_saved_when_going_offline(ctl):
    from datetime import datetime

    ctl.login()
    assert ctl.settings.history_since == ""
    ctl.logout()
    assert datetime.fromisoformat(ctl.settings.history_since).tzinfo is not None
    assert "history_since" not in ctl.settingsMap()  # internal, not shown in Settings


def test_best_presence_prefers_priority_then_reachability():
    from jaba_da_hat.backend.xmpp import best_presence

    assert best_presence({}) == ("offline", "")
    assert best_presence({"a": {"show": "away", "status": "x", "priority": 0},
                          "b": {"show": "", "status": "", "priority": 0}}) == ("available", "")
    assert best_presence({"a": {"show": "xa", "status": "zzz", "priority": 5},
                          "b": {"show": "chat", "status": "", "priority": 0}}) == ("xa", "zzz")
