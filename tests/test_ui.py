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
