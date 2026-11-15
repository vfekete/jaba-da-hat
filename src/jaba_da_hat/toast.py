"""Own toast popup, run as a short-lived helper process: ``jdh --toast TITLE BODY``.

The helper uses XWayland so it can sit in the top-right corner — see docs/comments-details.md [7].
Exit codes: 0 = clicked, 1 = timed out, 2 = dismissed with ×.
"""

from __future__ import annotations

import os
import sys

TOAST_MS = 7000
WIDTH = 360
MARGIN = 12
PANEL_GUESS = 36  # GNOME's top bar when the screen reports no reserved area


def run_toast(title: str, body: str, kind: str = "message", dark: bool = False, timeout_ms: int = TOAST_MS) -> int:
    os.environ["QT_QPA_PLATFORM"] = os.environ.get("JDH_TOAST_PLATFORM", "xcb")  # override only for screenshots
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

    from jaba_da_hat.trayicons import render

    app = QApplication(sys.argv[:1])
    bg, fg, border = ("#2b2b2e", "#f6f5f4", "#c4451c") if dark else ("#ffffff", "#241f31", "#c4451c")

    class Toast(QWidget):
        def __init__(self):
            super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.X11BypassWindowManagerHint)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setFixedWidth(WIDTH)
            self.setCursor(Qt.PointingHandCursor)
            row = QHBoxLayout(self)
            row.setContentsMargins(14, 12, 8, 12)
            row.setSpacing(12)
            icon = QLabel()
            icon.setPixmap(render(kind if kind != "status" else "note", "available").scaled(
                40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            icon.setAlignment(Qt.AlignTop)
            row.addWidget(icon)
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(title)
            f = QFont(t.font())
            f.setBold(True)
            t.setFont(f)
            t.setStyleSheet(f"color:{fg}")
            b = QLabel(body)
            b.setWordWrap(True)
            b.setTextFormat(Qt.PlainText)
            b.setStyleSheet(f"color:{fg}")
            col.addWidget(t)
            col.addWidget(b)
            row.addLayout(col, 1)
            close = QPushButton("×")
            close.setFlat(True)
            close.setFixedSize(24, 24)
            close.setStyleSheet(f"color:{fg}; font-size:16px; border:none")
            close.clicked.connect(lambda: app.exit(2))
            row.addWidget(close, 0, Qt.AlignTop)

        def paintEvent(self, _ev):  # noqa: N802
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QColor(border))
            p.setBrush(QColor(bg))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)

        def mouseReleaseEvent(self, ev):  # noqa: N802
            if ev.button() == Qt.LeftButton:
                app.exit(0)

    w = Toast()
    w.adjustSize()
    screen = QGuiApplication.primaryScreen()
    full, avail = screen.geometry(), screen.availableGeometry()
    top = avail.top() if avail.top() > full.top() else full.top() + PANEL_GUESS
    w.move(full.right() - w.width() - MARGIN, top + MARGIN)
    w.show()
    grab = os.environ.get("JDH_TOAST_GRAB")
    if grab:  # tools/screenshots.py
        QTimer.singleShot(200, lambda: (w.grab().save(grab), app.exit(1)))
    QTimer.singleShot(timeout_ms, lambda: app.exit(1))
    return app.exec()


__all__ = ["run_toast"]
