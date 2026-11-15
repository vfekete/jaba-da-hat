"""Regenerate the package's scaled icons from assets/app-icon.png."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "app-icon.png"
DST = ROOT / "src" / "jaba_da_hat" / "resources"

app = QGuiApplication(sys.argv[:1])
img = QImage(str(SRC))
if img.isNull():
    sys.exit(f"cannot read {SRC}")
DST.mkdir(parents=True, exist_ok=True)
for size in (32, 64, 128, 256):
    out = DST / f"icon-{size}.png"
    img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(out))
    print(out.relative_to(ROOT))
