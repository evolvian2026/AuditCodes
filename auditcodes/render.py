"""Rendering helpers shared by the review UI and the exporters."""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"breaks": True, "html": False})
_IMG_SRC = re.compile(r'<img src="([^"]+)"')


def markdown(text: str | None, asset_base: str = "") -> str:
    if not text:
        return ""
    out = _md.render(text)
    if asset_base:
        out = _IMG_SRC.sub(lambda m: f'<img src="{asset_base}/{m.group(1)}"' if "://" not in m.group(1) and not m.group(1).startswith("/") else m.group(0), out)
    return out


def confidence_class(value: float | None) -> str:
    if value is None:
        return "conf-none"
    if value >= 0.95:
        return "conf-ok"
    if value >= 0.7:
        return "conf-warn"
    return "conf-bad"


def escape(text: str | None) -> str:
    return html.escape(text or "")


def diff_html(diff_text: str) -> str:
    """Colour a unified diff; the text is escaped, classes drive the colours."""
    out = []
    for line in diff_text.split("\n"):
        cls = "diff-ctx"
        if line.startswith("+++") or line.startswith("---"):
            cls = "diff-file"
        elif line.startswith("@@"):
            cls = "diff-hunk"
        elif line.startswith("+"):
            cls = "diff-add"
        elif line.startswith("-"):
            cls = "diff-del"
        out.append(f'<span class="{cls}">{html.escape(line)}</span>')
    return "\n".join(out)
