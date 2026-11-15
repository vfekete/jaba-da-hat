"""Vector-drawn tray icons: status circles, envelope (unread message), file (incoming file), note (notification).

Every icon fills its whole canvas — the panel scales it to its icon size, so no padding is wasted.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

SIZE = 128
PRESENCE_COLORS = {
    "available": "#2ec27e",
    "away": "#f5c211",
    "xa": "#e66100",
    "dnd": "#e01b24",
    "offline": "#9a9996",
}
OUTLINE = QColor("#ffffff")


def _pen(color, width: float, cap=Qt.RoundCap, join=Qt.RoundJoin) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(cap)
    pen.setJoinStyle(join)
    return pen


def _canvas(size: int) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _rotate(p: QPainter, size: int, angle: float) -> None:
    if angle:
        p.translate(size / 2, size / 2)
        p.rotate(angle)
        p.translate(-size / 2, -size / 2)


def _badge(p: QPainter, size: int, presence: str) -> None:
    """Small presence dot in the bottom-right corner of an event icon."""
    d = size * 0.34
    p.setPen(_pen(OUTLINE, size * 0.04))
    p.setBrush(QColor(PRESENCE_COLORS.get(presence, "#9a9996")))
    p.drawEllipse(QRectF(size - d - size * 0.02, size - d - size * 0.02, d, d))


def status_pixmap(presence: str, size: int = SIZE) -> QPixmap:
    pm, p = _canvas(size)
    w = size * 0.06
    p.setPen(_pen(OUTLINE, w))
    p.setBrush(QColor(PRESENCE_COLORS.get(presence, "#9a9996")))
    p.drawEllipse(QRectF(w / 2, w / 2, size - w, size - w))
    if presence == "dnd":  # white bar, readable without colour
        p.setPen(Qt.NoPen)
        p.setBrush(OUTLINE)
        p.drawRoundedRect(QRectF(size * 0.24, size * 0.43, size * 0.52, size * 0.14), size * 0.05, size * 0.05)
    elif presence in ("away", "xa"):  # clock hands
        p.setPen(_pen(OUTLINE, size * 0.09))
        c = QPointF(size / 2, size / 2)
        p.drawLine(c, QPointF(size / 2, size * 0.22))
        p.drawLine(c, QPointF(size * 0.72, size * 0.62))
    p.end()
    return pm


def envelope_pixmap(presence: str, angle: float = 0.0, size: int = SIZE) -> QPixmap:
    pm, p = _canvas(size)
    _rotate(p, size, angle)
    r = QRectF(size * 0.03, size * 0.17, size * 0.94, size * 0.66)
    p.setPen(_pen("#1c71d8", size * 0.05))
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(r, size * 0.06, size * 0.06)
    flap = QPainterPath(QPointF(r.left() + size * 0.03, r.top() + size * 0.03))
    flap.lineTo(QPointF(r.center().x(), r.top() + r.height() * 0.58))
    flap.lineTo(QPointF(r.right() - size * 0.03, r.top() + size * 0.03))
    p.setBrush(Qt.NoBrush)
    p.drawPath(flap)
    p.resetTransform()
    _badge(p, size, presence)
    p.end()
    return pm


def file_pixmap(presence: str, angle: float = 0.0, size: int = SIZE) -> QPixmap:
    pm, p = _canvas(size)
    _rotate(p, size, angle)
    l, t, r, b, fold = size * 0.16, size * 0.03, size * 0.84, size * 0.97, size * 0.24
    page = QPainterPath(QPointF(l, t))
    page.lineTo(r - fold, t)
    page.lineTo(r, t + fold)
    page.lineTo(r, b)
    page.lineTo(l, b)
    page.closeSubpath()
    p.setPen(_pen(QColor("#613583"), size * 0.05))
    p.setBrush(QColor("#ffffff"))
    p.drawPath(page)
    p.drawPolyline([QPointF(r - fold, t), QPointF(r - fold, t + fold), QPointF(r, t + fold)])
    # download arrow
    p.setPen(_pen(QColor("#613583"), size * 0.09))
    cx = size * 0.5
    p.drawLine(QPointF(cx, size * 0.36), QPointF(cx, size * 0.74))
    p.drawPolyline([QPointF(cx - size * 0.15, size * 0.6), QPointF(cx, size * 0.75), QPointF(cx + size * 0.15, size * 0.6)])
    p.resetTransform()
    _badge(p, size, presence)
    p.end()
    return pm


def note_pixmap(presence: str, angle: float = 0.0, size: int = SIZE) -> QPixmap:
    """Sticky note with an exclamation mark: a pending notification (authorization request, problem)."""
    pm, p = _canvas(size)
    _rotate(p, size, angle)
    p.setPen(_pen(QColor("#9c6e03"), size * 0.05))
    p.setBrush(QColor("#f9f06b"))
    p.drawRoundedRect(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88), size * 0.08, size * 0.08)
    p.setPen(_pen(QColor("#5e4502"), size * 0.12))
    p.drawLine(QPointF(size * 0.5, size * 0.24), QPointF(size * 0.5, size * 0.56))
    p.drawPoint(QPointF(size * 0.5, size * 0.76))
    p.resetTransform()
    _badge(p, size, presence)
    p.end()
    return pm


def tray_kind(unread: int, files: int, notifications: int) -> str:
    """Which icon the tray shows, in priority order."""
    if unread:
        return "message"
    if files:
        return "file"
    if notifications:
        return "note"
    return "status"


def render(kind: str, presence: str, angle: float = 0.0, size: int = SIZE) -> QPixmap:
    if kind == "message":
        return envelope_pixmap(presence, angle, size)
    if kind == "file":
        return file_pixmap(presence, angle, size)
    if kind == "note":
        return note_pixmap(presence, angle, size)
    return status_pixmap(presence, size)
