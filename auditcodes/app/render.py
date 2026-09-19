"""Rendering helpers shared by the review UI (and, later, the exporters)."""

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
