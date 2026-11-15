"""Persistent user settings stored as JSON in the XDG config directory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from jaba_da_hat import APP_ID

THEMES = ("system", "light", "dark")


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
    # Privacy
    send_typing: bool = True
    show_typing: bool = True
    # Files
    download_dir: str = field(default_factory=default_download_dir)
    # Contacts
    show_offline: bool = False
    # Groups with no members exist only locally; XMPP stores groups per contact — see docs/comments-details.md [1].
    local_groups: list[str] = field(default_factory=list)

    def has_credentials(self) -> bool:
        return bool(self.jid and "@" in self.jid and self.password)


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
