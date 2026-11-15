import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.config, ~/.local or ~/Downloads."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Material")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    yield app


def process_events(ms: int = 0):
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop

    QCoreApplication.processEvents(QEventLoop.AllEvents, ms)


def wait_until(cond, timeout_ms: int = 5000):
    import time

    from PySide6.QtCore import QCoreApplication, QEventLoop

    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def ctl(qapp, tmp_path):
    from jaba_da_hat import config
    from jaba_da_hat.backend.mock import MockBackend
    from jaba_da_hat.core import AppController

    settings = config.Settings(jid="me@example.org", password="mock", download_dir=str(tmp_path / "dl"),
                               start_at_login=False)
    c = AppController(MockBackend(fast=True), settings, tmp_path / "cfg.json")
    c.notified = []
    c.notify.connect(lambda *a: c.notified.append(a))
    return c
