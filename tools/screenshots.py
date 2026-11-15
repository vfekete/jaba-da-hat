"""Render README screenshots offscreen using the mock backend (no real data, no network).

Usage: QT_QPA_PLATFORM=offscreen uv run python tools/screenshots.py [out_dir]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from jaba_da_hat import config
from jaba_da_hat.backend.mock import MockBackend
from jaba_da_hat.core import AppController
from jaba_da_hat.tray import Tray

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def grab(win, name: str) -> None:
    wait(400)
    img = win.grabWindow()
    img.save(str(OUT / f"{name}.png"))
    print(OUT / f"{name}.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Material")
    app = QApplication(sys.argv[:1])
    app.setFont(QGuiApplication.font())
    tmp = Path(tempfile.mkdtemp())
    settings = config.Settings(jid="me@example.org", password="mock", download_dir=str(tmp / "dl"))
    backend = MockBackend(fast=True)
    engine = QQmlApplicationEngine()
    ctl = AppController(backend, settings, tmp / "config.json", engine)
    ctl.login()
    ctl.addGroup("Family")

    for theme in ("light", "dark"):
        ctl.settings.theme = theme
        ctl.themeChanged.emit()
        app.styleHints().setColorScheme(
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ColorScheme.Dark if theme == "dark"
            else __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ColorScheme.Light)

        ctl.openContacts()
        contacts = ctl._singletons["contacts"]
        grab(contacts, f"contacts-{theme}")

        if theme == "light":
            s = ctl.session("alice@example.org")
            s.receive("Hi! Did you see the plan at https://example.org/plan ?", True)
            s._messages.add_text("out", "Yes — looks good. Sending the notes now.", True)
            backend.simulate_file_offer("alice@example.org")
            s.receive("Picture from the trip: https://example.org/img/trip.jpg (only a link — nothing is loaded)", True)
            s.set_remote_typing(True)
        ctl.openChat("alice@example.org")
        grab(ctl.windows["alice@example.org"], f"chat-{theme}")

        ctl.openSettings()
        grab(ctl._singletons["settings"], f"settings-{theme}")

    backend.simulate_subscription_request()
    ctl.openNotifications()
    grab(ctl._singletons["notifications"], "notifications-dark")

    tray = Tray(ctl)
    tray.rebuild_menu()
    tray.menu.adjustSize()
    tray.menu.show()
    wait(300)
    tray.menu.grab().save(str(OUT / "tray-menu.png"))
    print(OUT / "tray-menu.png")
    tray.menu.hide()

    import os
    import subprocess

    for theme in ("light", "dark"):
        env = dict(os.environ, JDH_TOAST_PLATFORM="offscreen", JDH_TOAST_GRAB=str(OUT / f"toast-{theme}.png"))
        if theme == "light":
            cmd = [sys.executable, "-m", "jaba_da_hat", "--toast", "Alice", "Hi! Did you see the plan at https://example.org/plan ?"]
        else:
            title, body, _k = Tray.summarize([("Alice", "Hi! Did you see the plan?", "alice@example.org", "message"),
                                              ("Bob", "Wants to send you “notes.txt” (12 KiB)", "bob@example.org", "file"),
                                              ("Carol", "Lunch?", "carol@example.net", "message")])
            cmd = [sys.executable, "-m", "jaba_da_hat", "--toast", title, body]
        subprocess.run(cmd + (["--toast-dark"] if theme == "dark" else []), env=env, timeout=30, check=False)
        print(OUT / f"toast-{theme}.png")

    from jaba_da_hat import trayicons

    strip = QPixmap(5 * 72, 64)
    strip.fill(Qt.transparent)
    p = QPainter(strip)
    for i, (kind, presence) in enumerate([("status", "available"), ("status", "away"), ("status", "dnd"),
                                          ("message", "available"), ("file", "available")]):
        p.drawPixmap(i * 72, 0, trayicons.render(kind, presence, 0, 64))
    p.end()
    strip.save(str(OUT / "tray-icons.png"))
    print(OUT / "tray-icons.png")


if __name__ == "__main__":
    main()
