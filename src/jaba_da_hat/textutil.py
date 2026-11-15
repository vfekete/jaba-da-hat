"""Turning plain message text into safe display HTML."""

from __future__ import annotations

import html
import re

_URL_RE = re.compile(r"(?P<url>(?:https?|ftp|xmpp)://[^\s<>\"']+|www\.[^\s<>\"']+|xmpp:[^\s<>\"']+)", re.I)
_TRAILING = ".,;:!?)]}'\""


def linkify(text: str) -> str:
    """Escape *text* and wrap URLs in <a> tags.

    Only anchors are ever produced — never <img> or other tags that would make
    the view fetch remote content on its own.
    """
    out: list[str] = []
    pos = 0
    for m in _URL_RE.finditer(text):
        url = m.group("url")
        trail = ""
        while url and url[-1] in _TRAILING:
            trail = url[-1] + trail
            url = url[:-1]
        out.append(html.escape(text[pos : m.start()]))
        href = url if "://" in url or url.lower().startswith("xmpp:") else "https://" + url
        out.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(url)}</a>')
        out.append(html.escape(trail))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out).replace("\n", "<br>")


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
