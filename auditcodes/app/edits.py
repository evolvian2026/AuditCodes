"""Compatibility re-export; the edit logic lives in ``auditcodes.edits``."""

from ..edits import EditError, apply_edit

__all__ = ["EditError", "apply_edit"]
