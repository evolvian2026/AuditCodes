"""Applying patches to questions, and diffs for showing them."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from ..edits import EditError, apply_edit
from ..fields import field_ctx
from ..models import Question
from .report import Finding, Patch

_ALLOWED_SET = re.compile(
    r"^(title|description_md|input_format_md|output_format_md|constraints|sample_explanation_md|editorial_md"
    r"|difficulty|area|time_limit_seconds|solutions\.[a-z]+|drivers\.[a-z]+"
    r"|(samples|hidden_tests)\.\d+\.(stdin|stdout|explanation))$"
)
_ALLOWED_REMOVE = re.compile(r"^(samples|hidden_tests)\.(\d+)$")


class PatchError(ValueError):
    pass


def fingerprint(item: Any) -> str:
    data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def current_value(question: Question, path: str) -> str | None:
    """The field's present value as the edit form would show it (None if the path is unknown)."""
    try:
        return field_ctx(question, path)["raw"]
    except (AttributeError, IndexError, KeyError, ValueError):
        return None


def apply_patch(question: Question, patch: Patch) -> Question:
    if patch.op == "set":
        if not _ALLOWED_SET.match(patch.path):
            raise PatchError(f"patches cannot set {patch.path!r}")
        if patch.new_value is None:
            raise PatchError("set patch has no new value")
        try:
            return apply_edit(question, patch.path, patch.new_value)
        except EditError as e:
            raise PatchError(str(e)) from e
    m = _ALLOWED_REMOVE.match(patch.path)
    if not m:
        raise PatchError(f"patches cannot remove {patch.path!r}")
    group, idx = m.group(1), int(m.group(2))
    items = list(getattr(question, group))
    target = None
    if patch.item_fingerprint:
        target = next((i for i, it in enumerate(items) if fingerprint(it) == patch.item_fingerprint), None)
    if target is None and idx < len(items) and (patch.item_fingerprint is None or fingerprint(items[idx]) == patch.item_fingerprint):
        target = idx
    if target is None:
        raise PatchError("the test case this patch removes is no longer present")
    del items[target]
    return question.model_copy(update={group: items})


def shift_indices_after_removal(findings: list[Finding], group: str, removed_index: int) -> None:
    """Keep other findings' index-addressed paths valid after a list item was removed."""
    pat = re.compile(rf"^{group}\.(\d+)(\..*)?$")
    for f in findings:
        for path_holder in ([f.patch] if f.patch else []):
            m = pat.match(path_holder.path)
            if m and int(m.group(1)) > removed_index:
                path_holder.path = f"{group}.{int(m.group(1)) - 1}{m.group(2) or ''}"
        m = pat.match(f.component)
        if m and int(m.group(1)) > removed_index:
            f.component = f"{group}.{int(m.group(1)) - 1}{m.group(2) or ''}"


def unified_diff(old: str | None, new: str | None, path: str) -> str:
    a = (old or "").splitlines()
    b = (new or "").splitlines()
    return "\n".join(difflib.unified_diff(a, b, fromfile=f"{path} (current)", tofile=f"{path} (proposed)", lineterm="", n=2))
