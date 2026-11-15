"""Entry point: argument parsing, Qt setup, single-instance guard, signal handling."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import sys
from pathlib import Path

from jaba_da_hat import APP_ID, APP_NAME, __version__

log = logging.getLogger("jaba_da_hat")
TRAY_WAIT_MS = 30_000


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jdh", description=f"{APP_NAME} — Jabber/XMPP tray messenger")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("--mock", action="store_true",
                   help="use the built-in mock backend with fake contacts instead of a real XMPP server")
    p.add_argument("--ca-file", type=Path,
                   help="also trust this CA certificate (PEM), e.g. for the test server in tools/test-server.sh")
    p.add_argument("--config", type=Path, help="use this settings file instead of ~/.config/jaba-da-hat/config.json")
    p.add_argument("--autostart", action="store_true", help="started by the desktop session at login")
    p.add_argument("--register", "--install-desktop", dest="register", action="store_true",
                   help="register the app for the current user (.desktop launcher + icon), then exit")
    p.add_argument("--unregister", "--uninstall-desktop", dest="unregister", action="store_true",
                   help="unregister the app (remove launcher, autostart entry and icon), then exit")
    p.add_argument("--open", choices=("contacts", "settings", "notifications"),
                   help="open a window right after start (handy for testing)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--toast", nargs=2, metavar=("TITLE", "BODY"), help=argparse.SUPPRESS)  # internal helper
    p.add_argument("--toast-kind", default="message", help=argparse.SUPPRESS)
    p.add_argument("--toast-dark", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _single_instance(server_name: str, args):
    """Return a listening QLocalServer, or None if another instance already runs (it is asked to show itself)."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(300):
        sock.write((args.open or "contacts").encode())
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(server_name)  # stale socket from a crashed run
    server = QLocalServer()
    server.listen(server_name)
    return server


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # slixmpp's DEBUG log contains raw stanzas, including the SASL exchange — see docs/comments-details.md [18].
    logging.getLogger("slixmpp").setLevel(logging.INFO if args.verbose else logging.WARNING)

    if args.toast:
        from jaba_da_hat.toast import run_toast

        return run_toast(args.toast[0], args.toast[1], args.toast_kind, args.toast_dark)

    from jaba_da_hat import autostart

    if args.register:
        for p in autostart.install_desktop_entry():
            print(f"installed {p}")
        return 0
    if args.unregister:
        removed = autostart.uninstall_desktop_entry()
        for p in removed:
            print(f"removed {p}")
        if not removed:
            print(f"{APP_NAME} was not registered")
        return 0
    if autostart.is_packaged():  # from source the launcher would point at the venv, so only the binary does this
        installed = autostart.registered_version()
        result = autostart.refresh_registration()
        if result == "registered":
            log.info("registered %s %s for this user", APP_NAME, __version__)
            from jaba_da_hat import config

            if config.load(args.config).start_at_login:  # first start: "start at login" is on by default
                autostart.set_enabled(True)
        elif result == "updated":
            if installed == __version__:
                log.info("registration of %s refreshed (launcher entry was out of date)", __version__)
            else:
                log.info("re-registered: launcher updated from %s to %s", installed or "?", __version__)

    from PySide6.QtCore import QLoggingCategory, Qt, QTimer
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from jaba_da_hat import config
    from jaba_da_hat.core import AppController
    from jaba_da_hat.tray import Tray, app_pixmap

    # GNOME's file chooser through the desktop portal instead of GTK in-process — see docs/comments-details.md [9].
    os.environ.setdefault("QT_QPA_PLATFORMTHEME", "xdgdesktopportal")
    # Harmless Qt Wayland text-input chatter on every focus change.
    QLoggingCategory.setFilterRules("qt.qpa.wayland.textinput=false")
    QGuiApplication.setDesktopFileName(APP_ID)
    QQuickStyle.setStyle("Material")
    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_ID)
    qapp.setApplicationDisplayName(APP_NAME)
    qapp.setApplicationVersion(__version__)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(QIcon(app_pixmap(256)))

    instance = f"{APP_ID}-{os.getuid()}"
    if args.config:  # a separate settings file is a separate profile with its own instance
        instance += "-" + hashlib.sha1(str(args.config.resolve()).encode()).hexdigest()[:10]
    server = _single_instance(instance, args)
    if server is None:
        log.info("%s is already running — asked it to show itself", APP_NAME)
        return 0

    settings_path = args.config
    settings = config.load(settings_path)
    engine = QQmlApplicationEngine()
    if args.mock:
        from jaba_da_hat.backend.mock import MockBackend

        backend = MockBackend()
        log.info("%s %s starting with the MOCK backend (no real XMPP traffic)", APP_NAME, __version__)
    else:
        from jaba_da_hat.backend.xmpp import XmppBackend

        backend = XmppBackend(ca_file=str(args.ca_file) if args.ca_file else None)
        log.info("%s %s starting", APP_NAME, __version__)
    ctl = AppController(backend, settings, settings_path, engine)

    hints = qapp.styleHints()
    ctl.themeChanged.connect(lambda: _sync_theme(hints, ctl))
    hints.colorSchemeChanged.connect(lambda _s: ctl.set_system_dark(hints.colorScheme() == Qt.ColorScheme.Dark))
    _sync_theme(hints, ctl)

    tray = Tray(ctl)

    from jaba_da_hat.sysevents import SystemEvents

    sysevents = SystemEvents()
    sysevents.sleeping.connect(ctl.on_sleep)
    sysevents.networkChanged.connect(ctl.on_network)
    ctl.readyToSleep.connect(sysevents.release_sleep)
    sysevents.start()

    def on_peer():
        sock = server.nextPendingConnection()
        sock.waitForReadyRead(300)
        what = bytes(sock.readAll()).decode(errors="ignore")
        {"settings": ctl.openSettings, "notifications": ctl.openNotifications}.get(what, ctl.openContacts)()

    server.newConnection.connect(on_peer)

    if tray.show():
        def check_tray():
            if not tray.watcher_available:
                log.warning("no system tray found (on GNOME enable the AppIndicator extension); opening Contacts window")
                ctl.openContacts()

        QTimer.singleShot(TRAY_WAIT_MS if args.autostart else 3000, check_tray)
    else:
        ctl.openContacts()

    # Graceful exit on session logout / Ctrl+C via a self-pipe — see docs/comments-details.md [5].
    sig_notifier = _install_quit_signals(qapp)
    qapp.commitDataRequest.connect(lambda _m: ctl.shutdown())
    qapp.aboutToQuit.connect(ctl.shutdown)
    qapp.aboutToQuit.connect(tray.close_toast)
    qapp.aboutToQuit.connect(tray.hide)
    qapp.aboutToQuit.connect(sysevents.stop)

    if args.open:
        {"contacts": ctl.openContacts, "settings": ctl.openSettings,
         "notifications": ctl.openNotifications}[args.open]()
    QTimer.singleShot(0, ctl.start)
    rc = qapp.exec()
    backend.shutdown()  # waits briefly so the server gets our goodbye
    sig_notifier.setEnabled(False)
    server.close()
    return rc


def _install_quit_signals(qapp):
    """Quit the Qt loop on SIGTERM/SIGINT/SIGHUP; returns the notifier (keep a reference)."""
    from PySide6.QtCore import QSocketNotifier

    rfd, wfd = os.pipe()
    os.set_blocking(rfd, False)
    os.set_blocking(wfd, False)
    signal.set_wakeup_fd(wfd)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: None)  # a Python handler must exist for the wakeup fd to be written
    notifier = QSocketNotifier(rfd, QSocketNotifier.Type.Read)

    def on_signal():
        try:
            os.read(rfd, 64)
        except BlockingIOError:
            pass
        log.info("termination signal received, shutting down")
        qapp.quit()

    notifier.activated.connect(on_signal)
    return notifier


def _sync_theme(hints, ctl) -> None:
    """Push the theme choice to Qt (widgets, tray menu) and mirror the effective scheme into the controller."""
    from PySide6.QtCore import Qt

    wanted = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(ctl.settings.theme, Qt.ColorScheme.Unknown)
    hints.setColorScheme(wanted)
    ctl.set_system_dark(hints.colorScheme() == Qt.ColorScheme.Dark)


if __name__ == "__main__":
    sys.exit(main())
