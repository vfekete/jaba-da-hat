"""Load every QML window offscreen and fail on any QML warning or error."""

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine

from jaba_da_hat.tray import Tray, tray_pixmap


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
    ui.logout()
    tray.rebuild_menu()
    assert "Log on" in [a.text() for a in tray.menu.actions()]
    assert not tray_pixmap("offline", 3).isNull()
