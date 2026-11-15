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


def self_command() -> list[str]:
    """argv that starts *this* copy of the app — the packaged binary when frozen. See docs/comments-details.md [3]."""
    onefile = os.environ.get("NUITKA_ONEFILE_BINARY")
    if onefile:
        return [onefile]
    if "__compiled__" in globals():
        return [sys.argv[0]]
    return [sys.executable, "-m", "jaba_da_hat"]


def executable_command() -> str:
    return shlex.join(self_command())


def icon_file(size: int = 256) -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"


def desktop_entry(autostart: bool) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "GenericName=Jabber Messenger",
        "Comment=Jabber/XMPP instant messenger living in the system tray",
        f"Exec={executable_command()}{' --autostart' if autostart else ''}",
        f"Icon={icon_file()}",  # absolute path: no icon-theme cache involved — see docs/comments-details.md [8]
        "Terminal=false",
        "Categories=Network;InstantMessaging;Chat;",
        "Keywords=jabber;xmpp;chat;im;",
        "StartupNotify=false",
        f"StartupWMClass={APP_ID}",
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
            dst = icon_file(size)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            written.append(dst)
    app = applications_file()
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(desktop_entry(autostart=False), encoding="utf-8")
    written.append(app)
    _refresh_desktop_caches(data)
    return written


def _refresh_desktop_caches(data: Path) -> None:
    """Best effort: tell desktop-file-utils / GTK about the user-level changes (never touches system dirs)."""
    import shutil
    import subprocess

    cmds = []
    if shutil.which("update-desktop-database"):
        cmds.append(["update-desktop-database", str(data / "applications")])
    hicolor = data / "icons" / "hicolor"
    if shutil.which("gtk-update-icon-cache") and (hicolor / "index.theme").exists():
        cmds.append(["gtk-update-icon-cache", "-q", "-t", str(hicolor)])
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


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
    """Register if missing, re-register if an older version is registered.

    Returns "registered", "updated", "current" or "newer" (a newer version is registered — left alone).
    """
    installed = registered_version()
    if installed is None:
        install_desktop_entry()
        return "registered"
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
    candidates += [icon_file(s) for s in (64, 128, 256)]
    for p in candidates:
        if p.exists():
            p.unlink()
            removed.append(p)
    if removed:
        _refresh_desktop_caches(data)
    return removed
