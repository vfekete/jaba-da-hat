"""XDG autostart and desktop-entry installation."""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

from jaba_da_hat import APP_ID, APP_NAME, __version__

ICON_NAME = APP_ID
RESOURCES = Path(__file__).resolve().parent / "resources"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or str(Path.home() / default))


def autostart_file() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{APP_ID}.desktop"


def applications_file() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "applications" / f"{APP_ID}.desktop"


def is_packaged() -> bool:
    """True when running as the Nuitka-built binary, not from source."""
    return bool(os.environ.get("NUITKA_ONEFILE_BINARY")) or "__compiled__" in globals()


def executable_command() -> str:
    """Command that starts *this* copy of the app — the packaged binary when frozen. See docs/comments-details.md [3]."""
    onefile = os.environ.get("NUITKA_ONEFILE_BINARY")
    if onefile:
        return shlex.quote(onefile)
    if "__compiled__" in globals():
        return shlex.quote(sys.argv[0])
    return f"{shlex.quote(sys.executable)} -m jaba_da_hat"


def desktop_entry(autostart: bool) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "GenericName=Jabber Messenger",
        "Comment=Jabber/XMPP instant messenger living in the system tray",
        f"Exec={executable_command()}{' --autostart' if autostart else ''}",
        f"Icon={ICON_NAME}",
        "Terminal=false",
        "Categories=Network;InstantMessaging;Chat;",
        "Keywords=jabber;xmpp;chat;im;",
        "StartupNotify=false",
        f"X-AppVersion={__version__}",
    ]
    if autostart:
        lines += ["X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-Delay=3"]
    return "\n".join(lines) + "\n"


def is_enabled() -> bool:
    f = autostart_file()
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return False
    return "X-GNOME-Autostart-enabled=false" not in text and "Hidden=true" not in text


def set_enabled(enabled: bool) -> None:
    f = autostart_file()
    if enabled:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(desktop_entry(autostart=True), encoding="utf-8")
    elif f.exists():
        f.unlink()


def install_desktop_entry() -> list[Path]:
    """Register: install the launcher .desktop file and the icon into the user's XDG data dir."""
    written = []
    data = _xdg("XDG_DATA_HOME", ".local/share")
    for size in (64, 128, 256):
        src = RESOURCES / f"icon-{size}.png"
        if src.exists():
            dst = data / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            written.append(dst)
    app = applications_file()
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(desktop_entry(autostart=False), encoding="utf-8")
    written.append(app)
    return written


def parse_version(text: str) -> tuple[int, ...]:
    """'1.2.3' / '1.2.3-rc1' → (1, 2, 3); unparsable → ()."""
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def registered_version() -> str | None:
    """Version recorded in the installed launcher, or None if the app is not registered."""
    try:
        text = applications_file().read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^X-AppVersion=(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def refresh_registration(current: str = __version__) -> str:
    """Re-register if an older version is registered. Returns "updated", "current", "newer" or "unregistered".

    Never registers on its own — that is what --register is for.
    """
    installed = registered_version()
    if installed is None:
        return "unregistered"
    have, want = parse_version(installed), parse_version(current)
    if have and have >= want:
        return "current" if have == want else "newer"
    autostart_was_on = is_enabled()
    install_desktop_entry()
    if autostart_was_on:
        set_enabled(True)  # its Exec= must point at the new binary too
    return "updated"


def uninstall_desktop_entry() -> list[Path]:
    """Unregister: remove launcher, autostart entry and icons."""
    removed = []
    data = _xdg("XDG_DATA_HOME", ".local/share")
    candidates = [applications_file(), autostart_file()]
    candidates += [data / "icons" / "hicolor" / f"{s}x{s}" / "apps" / f"{ICON_NAME}.png" for s in (64, 128, 256)]
    for p in candidates:
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed
