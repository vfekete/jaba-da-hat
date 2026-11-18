"""Persistent user settings stored as JSON in the XDG config directory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from jaba_da_hat import APP_ID

THEMES = ("system", "light", "dark")
NOTIFICATION_STYLES = ("toast", "gnome", "both")
ZOOM_MIN, ZOOM_MAX = -5, 10  # 59 % … 259 %


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_ID


def default_download_dir() -> str:
    return str(Path.home() / "Downloads")


@dataclass
class Settings:
    # Account
    jid: str = ""
    password: str = ""
    server: str = ""  # optional host override; empty = DNS SRV lookup from the JID domain
    port: int = 5222
    resource: str = "jaba-da-hat"
    require_tls: bool = True
    use_encryption: bool = True  # OMEMO by default for new chats
    # Behaviour
    auto_login: bool = True
    auto_reconnect: bool = True
    start_at_login: bool = True
    # Appearance
    theme: str = "system"
    notification_style: str = "toast"  # toast | gnome | both
    ui_font_family: str = ""  # "" = system default
    ui_font_size: float = 0.0  # points; 0 = default
    message_font_family: str = ""
    message_font_size: float = 0.0
    zoom: int = 0  # Ctrl +/−/0 steps of 10 %, applies to both fonts
    # Privacy
    send_typing: bool = True
    show_typing: bool = True
    # Files
    download_dir: str = field(default_factory=default_download_dir)
    # Contacts
    show_offline: bool = False
    # Groups with no members exist only locally; XMPP stores groups per contact — see docs/comments-details.md [1].
    local_groups: list[str] = field(default_factory=list)
    # When we were last connected (UTC, ISO 8601): server history newer than this is shown at login.
    history_since: str = ""

    def normalize(self) -> None:
        """Accept "host:port" (or "[v6]:port") typed into the server field."""
        self.server, self.port = split_server(self.server, self.port)

    def has_credentials(self) -> bool:
        return bool(self.jid and "@" in self.jid and self.password)


def split_server(server: str, port: int) -> tuple[str, int]:
    """Split an optional ":port" off a server address; a bare IPv6 address is left alone."""
    server = (server or "").strip()
    host, sep, tail = server.rpartition(":")
    if sep and tail.isdigit() and 0 < int(tail) < 65536 and (host.count(":") == 0 or host.startswith("[")):
        return host.strip("[]"), int(tail)
    return server.strip("[]") if server.startswith("[") else server, port


def load(path: Path | None = None) -> Settings:
    path = path or config_dir() / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    s = Settings(**{k: v for k, v in raw.items() if k in known})
    if s.theme not in THEMES:
        s.theme = "system"
    s.normalize()
    if s.notification_style not in NOTIFICATION_STYLES:
        s.notification_style = "toast"
    s.zoom = max(ZOOM_MIN, min(ZOOM_MAX, int(s.zoom or 0)))
    return s


def save(settings: Settings, path: Path | None = None) -> Path:
    """Write settings atomically with mode 0600 (the file contains the password)."""
    path = path or config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(asdict(settings), fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path
