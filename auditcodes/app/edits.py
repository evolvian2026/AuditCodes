"""Apply a reviewer's edit to one field of a question, addressed by a dotted path."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..models import Question

_READ_ONLY_PREFIXES = ("id", "number", "provenance", "metadata", "images", "io_mode")
_FLOAT_FIELDS = {"time_limit_seconds"}
_INT_FIELDS = {"points", "lod"}
_NULLABLE = {"difficulty", "area", "time_limit_seconds", "editorial_md", "sample_explanation_md", "input_format_md", "output_format_md", "points", "explanation", "label"}


class EditError(ValueError):
    pass


def apply_edit(question: Question, path: str, value: str) -> Question:
    parts = path.split(".") if path else []
    if not parts or any(path.startswith(p) for p in _READ_ONLY_PREFIXES):
        raise EditError(f"field {path!r} cannot be edited")
    data = question.model_dump(mode="json")
    value = value.replace("\r\n", "\n")
    if parts == ["constraints"]:
        data["constraints"] = [{"text": ln.strip()} for ln in value.split("\n") if ln.strip()]
    else:
        target: Any = data
        for key in parts[:-1]:
            target = _step(target, key)
        last = parts[-1]
        if isinstance(target, list):
            target[_index(target, last)] = value
        else:
            target[last] = _coerce(last, value)
    try:
        return Question.model_validate(data)
    except ValidationError as e:
        raise EditError("; ".join(err["msg"] for err in e.errors())) from e


def _step(target: Any, key: str) -> Any:
    if isinstance(target, list):
        return target[_index(target, key)]
    if key not in target or target[key] is None:
        raise EditError(f"unknown field {key!r}")
    return target[key]


def _index(target: list, key: str) -> int:
    try:
        i = int(key)
    except ValueError as e:
        raise EditError(f"bad index {key!r}") from e
    if not 0 <= i < len(target):
        raise EditError(f"index {i} out of range")
    return i


def _coerce(field: str, value: str) -> Any:
    stripped = value.strip()
    try:
        if field in _FLOAT_FIELDS:
            return float(stripped) if stripped else None
        if field in _INT_FIELDS:
            return int(stripped) if stripped else None
    except ValueError as e:
        raise EditError(f"{field}: {stripped!r} is not a number") from e
    if field in _NULLABLE and not stripped:
        return None
    return value
