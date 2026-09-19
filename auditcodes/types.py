"""Type language shared by the question model, the I/O protocol and the driver generators.

Grammar::

    T := int | long | double | bool | string | list<T>

``int`` is a signed 32-bit integer, ``long`` a signed 64-bit integer, ``double`` a finite IEEE-754
double. Every language mapping (C ``int*`` + size, Java ``int[]``, C++ ``vector<int>``, ...) is
derived from this one grammar, so a test case is written once as JSON and drives all languages.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

SCALAR_KINDS = ("int", "long", "double", "bool", "string")

INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class InvalidTypeError(ValueError):
    """The type string does not match the grammar."""


class ValueTypeError(ValueError):
    """A value does not fit the declared type (wrong shape, out of range, non-finite...)."""


@dataclass(frozen=True)
class TypeSpec:
    kind: str
    elem: "TypeSpec | None" = None

    def __str__(self) -> str:
        return self.kind if self.elem is None else f"list<{self.elem}>"

    @property
    def is_list(self) -> bool:
        return self.kind == "list"

    @property
    def depth(self) -> int:
        """Nesting depth: 0 for scalars, 1 for list<scalar>, 2 for list<list<scalar>>..."""
        return 0 if self.elem is None else 1 + self.elem.depth

    @property
    def base(self) -> "TypeSpec":
        """The innermost scalar type."""
        return self if self.elem is None else self.elem.base

    @property
    def mangled(self) -> str:
        """Identifier-safe name, e.g. ``list_list_int`` — used to name generated helpers."""
        return self.kind if self.elem is None else f"list_{self.elem.mangled}"


def parse_type(text: str) -> TypeSpec:
    src = text.replace(" ", "")
    spec, end = _parse(src, 0)
    if end != len(src):
        raise InvalidTypeError(f"trailing characters in type {text!r}")
    return spec


def _parse(src: str, i: int) -> tuple[TypeSpec, int]:
    if src.startswith("list<", i):
        inner, j = _parse(src, i + 5)
        if j >= len(src) or src[j] != ">":
            raise InvalidTypeError(f"missing '>' in type {src!r}")
        return TypeSpec("list", inner), j + 1
    m = _IDENT.match(src, i)
    if not m or m.group() not in SCALAR_KINDS:
        raise InvalidTypeError(f"unknown type at position {i} in {src!r} (expected one of {SCALAR_KINDS} or list<...>)")
    return TypeSpec(m.group()), m.end()


def coerce(spec: TypeSpec, value: Any, path: str = "value") -> Any:
    """Validate ``value`` against ``spec`` and return it in canonical Python form.

    Strict on purpose: ``True`` is not an ``int``, ``3.0`` is not an ``int``, and integers must fit
    their declared width, because a test case that violates these will break at least one language.
    """
    kind = spec.kind
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            raise ValueTypeError(f"{path}: expected {spec}, got {type(value).__name__}")
        return [coerce(spec.elem, v, f"{path}[{i}]") for i, v in enumerate(value)]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueTypeError(f"{path}: expected bool, got {type(value).__name__}")
        return value
    if kind in ("int", "long"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueTypeError(f"{path}: expected {kind}, got {type(value).__name__}")
        lo, hi = (INT32_MIN, INT32_MAX) if kind == "int" else (INT64_MIN, INT64_MAX)
        if not lo <= value <= hi:
            raise ValueTypeError(f"{path}: {value} does not fit in {kind} [{lo}, {hi}]")
        return value
    if kind == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueTypeError(f"{path}: expected double, got {type(value).__name__}")
        f = float(value)
        if not math.isfinite(f):
            raise ValueTypeError(f"{path}: double must be finite, got {value!r}")
        return f
    if kind == "string":
        if not isinstance(value, str):
            raise ValueTypeError(f"{path}: expected string, got {type(value).__name__}")
        return value
    raise InvalidTypeError(f"unhandled type kind {kind!r}")
