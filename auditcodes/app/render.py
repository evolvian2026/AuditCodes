"""Compatibility re-export; rendering helpers live in ``auditcodes.render``."""

from ..render import confidence_class, diff_html, escape, markdown

__all__ = ["confidence_class", "diff_html", "escape", "markdown"]
