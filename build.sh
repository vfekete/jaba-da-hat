#!/usr/bin/env bash
# Build a single self-contained executable: build/jdh-<version>
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP=jdh
PKG=src/jaba_da_hat
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }

# 1. version: pyproject.toml and the package must agree
VERSION=$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)
PKG_VERSION=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$PKG/__init__.py")
[[ -n "$VERSION" ]] || { echo "cannot read version from pyproject.toml" >&2; exit 1; }
if [[ "$VERSION" != "$PKG_VERSION" ]]; then
    echo "version mismatch: pyproject.toml=$VERSION, $PKG/__init__.py=$PKG_VERSION" >&2
    exit 1
fi
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+].*)?$ ]] || { echo "not a SemVer version: $VERSION" >&2; exit 1; }

# 2. inputs the binary depends on
[[ -f assets/app-icon.png ]] || { echo "missing assets/app-icon.png" >&2; exit 1; }
ls "$PKG"/qml/*.qml >/dev/null

# 3. build-only dependencies live in their own group, inside the project venv
uv sync --quiet --group build

# 4. regenerate the scaled icons bundled with the package
QT_QPA_PLATFORM=offscreen uv run --quiet python tools/gen_icons.py >/dev/null

# 5. clean leftovers of an interrupted build
WORK=build/.nuitka
rm -rf "$WORK"
mkdir -p build

OUT="build/$APP-$VERSION"
echo "building $OUT …"
# QML and icons are loaded by path at runtime, so they're bundled explicitly — see docs/comments-details.md [6].
uv run --quiet python -m nuitka \
    --mode=app \
    --python-flag=-m \
    --enable-plugin=pyside6 \
    --include-qt-plugins=qml,platformthemes,wayland-shell-integration,wayland-decoration-client,wayland-graphics-integration-client,platforminputcontexts \
    --include-package=jaba_da_hat \
    --include-data-dir="$PKG/qml=jaba_da_hat/qml" \
    --include-data-dir="$PKG/resources=jaba_da_hat/resources" \
    --linux-app-icon=assets/app-icon.png \
    --product-name=jaba-da-hat \
    --product-version="$VERSION" \
    --file-version="$VERSION" \
    --onefile-tempdir-spec="{CACHE_DIR}/jaba-da-hat/$VERSION" \
    --output-dir="$WORK" \
    --output-filename="$APP-$VERSION" \
    --assume-yes-for-downloads \
    --quiet \
    "$PKG"

mv -f "$WORK/$APP-$VERSION" "$OUT"
chmod +x "$OUT"
rm -rf "$WORK"

# 6. smoke test: the binary must at least report its version
"./$OUT" --version
echo "done: $OUT  ($(du -h "$OUT" | cut -f1))"
echo "install launcher + icon for your user with:  ./$OUT --install-desktop"
