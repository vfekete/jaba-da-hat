"""Load every QML window offscreen and fail on any QML warning or error."""

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine

from jaba_da_hat import trayicons
from jaba_da_hat.tray import Tray


@pytest.fixture
def qml_messages():
    msgs = []

    def handler(mode, ctx, text):
        if mode != QtMsgType.QtDebugMsg and "does not support" not in text:
            msgs.append(text)

    old = qInstallMessageHandler(handler)
    yield msgs
    qInstallMessageHandler(old)


@pytest.fixture
def ui(ctl):
    ctl.engine = QQmlApplicationEngine()
    ctl.login()
    return ctl


@pytest.mark.parametrize("opener", ["openContacts", "openSettings", "openNotifications"])
def test_singleton_windows_load(ui, qml_messages, opener):
    getattr(ui, opener)()
    from conftest import wait_until

    wait_until(lambda: False, 150)
    assert len(ui._singletons) == 1
    assert qml_messages == []


def test_chat_window_with_transfer(ui, qml_messages):
    from conftest import wait_until

    ui.backend.simulate_file_offer("alice@example.org")
    ui.openChat("alice@example.org")
    s = ui.sessions["alice@example.org"]
    s.send("look at https://example.org")
    wait_until(lambda: False, 150)
    win = ui.windows["alice@example.org"]
    assert win.property("visible")
    for theme in ("dark", "light"):
        ui.settings.theme = theme
        ui.themeChanged.emit()
        wait_until(lambda: False, 50)
    win.close()
    wait_until(lambda: "alice@example.org" not in ui.windows, 1000)
    assert "alice@example.org" not in ui.sessions
    assert qml_messages == []


def test_tray_menu_builds(ui):
    tray = Tray(ui)
    titles = [a.text() for a in tray.menu.actions()]
    assert any(t.startswith("Status:") for t in titles)
    assert "Log off" in titles and "Contacts" in titles and "Settings…" in titles
    assert titles[-1] == "Close"
    ui.logout()
    tray.rebuild_menu()
    assert "Log on" in [a.text() for a in tray.menu.actions()]
    for kind in ("status", "message", "file", "note"):
        assert not trayicons.render(kind, "dnd", 16).isNull()


def test_tray_icon_kind_follows_activity(ui):
    tray = Tray(ui)
    assert tray._kind == "status"
    ui.backend.simulate_subscription_request()
    assert tray._kind == "note"
    ui.backend.simulate_file_offer("alice@example.org")
    assert tray._kind == "file" and ui.pending_file_offers() == 1
    assert tray._wiggle.isActive()
    ui.backend.messageReceived.emit("bob@example.org", "hi", False)
    assert tray._kind == "message"
    ui.mark_read("bob@example.org")
    assert tray._kind == "file"
    s = ui.sessions["alice@example.org"]
    s.declineTransfer(next(iter(s.transfers.values())))
    assert tray._kind == "note"
    ui.notifications.clear()
    tray.refresh()
    assert tray._kind == "status"


def test_tray_activate_opens_all_waiting_chats(ui):
    tray = Tray(ui)
    tray.activate()
    assert ui.windows == {} and ui._singletons == {}  # nothing waiting → nothing happens
    ui.backend.messageReceived.emit("bob@example.org", "hi", False)
    ui.backend.messageReceived.emit("carol@example.net", "hello", False)
    ui.backend.messageReceived.emit("bob@example.org", "still there?", False)
    ui.backend.simulate_file_offer("erin@example.org")
    tray.activate()
    assert set(ui.windows) == {"bob@example.org", "carol@example.net", "erin@example.org"}
    assert ui.roster.total_unread() == 0
    assert ui.sessions["bob@example.org"].messages.rowCount() == 2


def test_toast_summary_and_click_opens_every_chat(ui):
    tray = Tray(ui)
    one = [("Alice", "hi", "alice@example.org", "message")]
    assert tray.summarize(one) == ("Alice", "hi", "message")
    events = [("Alice", "hi", "alice@example.org", "message"),
              ("Bob", "Wants to send you “a.txt” (1 KiB)", "bob@example.org", "file"),
              ("Alice", "second", "alice@example.org", "message"),
              ("Authorization request", "x wants…", "", "note")]
    title, body, kind = tray.summarize(events)
    assert title == "2 messages, 1 file, 1 notification from 2 contacts" and kind == "message"
    assert body.count("\n") == 3 and "Bob: Wants" in body
    tray.open_events(events)
    assert set(ui.windows) == {"alice@example.org", "bob@example.org"}
    assert "notifications" in ui._singletons


def test_chat_window_trust_dialog(ui, qml_messages):
    from PySide6.QtCore import QObject

    from conftest import wait_until

    ui.openChat("bob@example.org")
    s = ui.sessions["bob@example.org"]
    s.send("hello")
    wait_until(lambda: False, 150)
    win = ui.windows["bob@example.org"]
    dialog = next(o for o in win.findChildren(QObject) if o.property("title") == "Approve encryption devices")
    assert dialog.property("visible")
    s.approveDevices([s.trustRequest["devices"][0]["id"]] if s.trustRequest else [])
    assert wait_until(lambda: not dialog.property("visible"), 1500)
    assert ("message", "bob@example.org", "hello", True) in ui.backend.sent
    win.close()
    wait_until(lambda: "bob@example.org" not in ui.windows, 1000)
    assert qml_messages == []


def test_blocked_contacts_dialog(ui, qml_messages):
    from PySide6.QtCore import QObject

    from conftest import wait_until

    ui.removeAndBlock(["bob@example.org"])
    ui.openContacts()
    win = ui._singletons["contacts"]
    dialog = next(o for o in win.findChildren(QObject) if o.property("title") == "Blocked contacts")
    dialog.setProperty("visible", True)
    wait_until(lambda: False, 200)
    dialog.setProperty("chosen", {"bob@example.org": True})
    dialog.accept()
    assert wait_until(lambda: ui.blockedJids == [], 1000)
    assert qml_messages == []


def test_clear_removes_note_icon_but_keeps_pending_requests(ui):
    tray = Tray(ui)
    ui.backend.simulate_subscription_request()
    ui._on_error("Server closed the connection: test")
    assert tray._kind == "note"
    ui.clearNotifications()
    assert tray._kind == "status"  # Clear means "seen": the icon calms down
    assert [n["kind"] for n in ui.notifications.items()] == ["subscription"]  # still answerable
    assert ui.notifications.count == 1 and ui.notifications.unseen == 0
    ui.backend.simulate_subscription_request()  # something new: attention again
    assert tray._kind == "note"


def test_tray_menu_layout_and_contact_overflow(ui):
    from jaba_da_hat.models import Contact

    tray = Tray(ui)
    acts = tray.menu.actions()
    titles = [a.text() for a in acts]
    assert titles[0].startswith("Status:") and not any("—" in t for t in titles)  # no "app — JID" line
    i_contacts, i_notes = titles.index("Contacts"), titles.index("Notifications")
    assert i_contacts < i_notes < titles.index("Settings…") < titles.index("Log off")
    assert acts[-2].isSeparator() and titles[-1] == "Close"
    assert [a.isSeparator() for a in acts].count(True) >= 1
    opts = [a.text() for a in acts[i_contacts].menu().actions()]
    assert opts == ["Only online", "All contacts", "", "Manage contacts…"]
    assert "Friends" in titles  # group heading directly in the menu (mock roster has groups)

    many = [Contact(jid=f"user{i:02d}@example.org", name=f"User {i:02d}", groups=["Team"] if i < 15 else [],
                    presence="available") for i in range(25)]
    ui.roster.set_contacts(many)
    tray.rebuild_menu()
    acts = tray.menu.actions()
    shown = [a.text() for a in acts if a.text().startswith("User ")]
    assert shown == [f"User {i:02d}" for i in range(10)]
    more = next(a for a in acts if a.text() == "…").menu()
    rest = [a.text() for a in more.actions() if a.text().startswith("User ")]
    assert rest == [f"User {i:02d}" for i in range(10, 25)]
    assert "Team" in [a.text() for a in more.actions()] and "Ungrouped" in [a.text() for a in more.actions()]

    ui.roster.set_contacts(many[15:])  # nobody in a group: no headings, 10 fit without "…"
    tray.rebuild_menu()
    titles = [a.text() for a in tray.menu.actions()]
    assert "Ungrouped" not in titles and "…" not in titles and len([t for t in titles if t.startswith("User ")]) == 10
