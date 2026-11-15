"""The own tray (StatusNotifierItem + dbusmenu), exercised over a private D-Bus daemon — never the real session."""

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from jeepney import DBusAddress, HeaderFields, MessageType, new_method_call, new_method_return
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QActionGroup, QColor, QPixmap
from PySide6.QtWidgets import QMenu

from jaba_da_hat.sni import DBusTray, MenuExporter, argb_pixmaps


@pytest.fixture
def private_bus(monkeypatch):
    p = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                         stdout=subprocess.PIPE, text=True)
    addr = p.stdout.readline().strip()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", addr)
    yield addr
    p.terminate()
    p.wait(5)


class FakeWatcher(threading.Thread):
    """Plays org.kde.StatusNotifierWatcher (the AppIndicator extension's side)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.registered = []
        self.ready = threading.Event()
        self.stop = False

    def run(self):
        conn = open_dbus_connection(bus="SESSION")
        conn.send_and_get_reply(message_bus.RequestName("org.kde.StatusNotifierWatcher"))
        self.ready.set()
        while not self.stop:
            try:
                msg = conn.receive(timeout=0.1)
            except TimeoutError:
                continue
            if msg.header.fields.get(HeaderFields.member) == "RegisterStatusNotifierItem":
                self.registered.append(msg.body[0])
                conn.send(new_method_return(msg))
        conn.close()


def wait_for(future_or_cond, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
        done = future_or_cond.done() if hasattr(future_or_cond, "done") else future_or_cond()
        if done:
            return future_or_cond.result() if hasattr(future_or_cond, "result") else True
        time.sleep(0.005)
    raise TimeoutError


def build_menu(clicked):
    m = QMenu()
    m.addAction("Header").setEnabled(False)
    sub = m.addMenu("Status")
    grp = QActionGroup(sub)
    for name in ("Available", "Away"):
        a = sub.addAction(name)
        a.setCheckable(True)
        a.setChecked(name == "Away")
        grp.addAction(a)
    m.addSeparator()
    m.addSection("Friends")
    m.addAction("snake_case@example.org", lambda: clicked.append("contact"))
    m.addAction("Close", lambda: clicked.append("close"))
    return m


def test_menu_exporter_layout(qapp):
    clicked = []
    ex = MenuExporter(build_menu(clicked))
    rev, (root, props, kids) = ex.layout(0, -1, [])
    labels = [k[1][1]["label"][1] for k in kids if "label" in k[1][1]]
    assert labels == ["Header", "Status", "Friends", "snake__case@example.org", "Close"]
    status = next(k[1] for k in kids if k[1][1].get("label") == ("s", "Status"))
    assert status[1]["children-display"] == ("s", "submenu")
    radios = [c[1][1] for c in status[2]]
    assert [r["toggle-type"][1] for r in radios] == ["radio", "radio"]
    assert [r["toggle-state"][1] for r in radios] == [0, 1]
    assert any(k[1][1].get("type") == ("s", "separator") for k in kids)
    close_id = next(k[1][0] for k in kids if k[1][1].get("label") == ("s", "Close"))
    assert ex.trigger(close_id)
    wait_for(lambda: clicked == ["close"])


def test_argb_conversion(qapp):
    pm = QPixmap(4, 4)
    pm.fill(QColor(255, 0, 0, 255))
    w, h, data = argb_pixmaps(pm)[0]
    assert (w, h) == (32, 32) and len(data) == 32 * 32 * 4
    assert data[:4] == bytes([255, 255, 0, 0])  # A, R, G, B


def test_tray_over_dbus(qapp, private_bus):
    watcher = FakeWatcher()
    watcher.start()
    assert watcher.ready.wait(5)

    clicked, activated = [], []
    menu = build_menu(clicked)
    tray = DBusTray(menu, "jaba-da-hat", "jaba-da-hat")
    tray.activated.connect(lambda: activated.append(True))
    rebuilds = []
    tray.menuAboutToShow.connect(lambda: rebuilds.append(True))
    pm = QPixmap(16, 16)
    pm.fill(QColor("#2ec27e"))
    tray.set_icon(pm)
    assert tray.start()
    wait_for(lambda: watcher.registered == [tray.bus_name] and tray.watcher_available)

    client = open_dbus_connection(bus="SESSION")
    sni = DBusAddress("/StatusNotifierItem", bus_name=tray.bus_name, interface="org.kde.StatusNotifierItem")
    dbm = DBusAddress("/MenuBar", bus_name=tray.bus_name, interface="com.canonical.dbusmenu")
    props = DBusAddress("/StatusNotifierItem", bus_name=tray.bus_name, interface="org.freedesktop.DBus.Properties")
    pool = ThreadPoolExecutor(1)

    def call(msg):
        return wait_for(pool.submit(client.send_and_get_reply, msg, timeout=5))

    icon = call(new_method_call(props, "Get", "ss", ("org.kde.StatusNotifierItem", "IconPixmap")))
    assert icon.body[0][0] == "a(iiay)" and [p[0] for p in icon.body[0][1]] == [32, 64, 128]
    assert call(new_method_call(props, "Get", "ss", ("org.kde.StatusNotifierItem", "Menu"))).body[0] == ("o", "/MenuBar")

    assert call(new_method_call(dbm, "AboutToShow", "i", (0,))).body == (True,)
    assert rebuilds
    rev, layout = call(new_method_call(dbm, "GetLayout", "iias", (0, -1, []))).body
    close_id = next(k[1][0] for k in layout[2] if k[1][1].get("label") == ("s", "Close"))
    call(new_method_call(dbm, "Event", "isvu", (close_id, "clicked", ("i", 0), 0)))
    wait_for(lambda: clicked == ["close"])

    call(new_method_call(sni, "Activate", "ii", (0, 0)))
    wait_for(lambda: activated == [True])

    # no activation-token support: the extension then never starts a busy-cursor sequence
    err = call(new_method_call(sni, "ProvideXdgActivationToken", "s", ("token",)))
    assert err.header.message_type == MessageType.error
    assert err.header.fields[HeaderFields.error_name] == "org.freedesktop.DBus.Error.UnknownMethod"

    tray.stop()
    client.close()
    watcher.stop = True
    watcher.join(2)
