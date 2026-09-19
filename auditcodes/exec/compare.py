"""Output comparison. Doubles always use a tolerance; the ``Checker`` chooses the structure rule."""

from __future__ import annotations

import json
import math
from typing import Any

from ..models import Checker
from ..types import TypeSpec

ABS_TOL = 1e-6
REL_TOL = 1e-6


def values_match(expected: Any, actual: Any, spec: TypeSpec, checker: Checker = Checker.EXACT) -> bool:
    if checker in (Checker.EXACT, Checker.FLOAT_TOL):
        return _equal(spec, expected, actual)
    if checker is Checker.UNORDERED:
        if not spec.is_list:
            return _equal(spec, expected, actual)
        if len(expected) != len(actual):
            return False
        exp_sorted = sorted(expected, key=lambda v: _canonical(spec.elem, v))
        act_sorted = sorted(actual, key=lambda v: _canonical(spec.elem, v))
        return all(_equal(spec.elem, e, a) for e, a in zip(exp_sorted, act_sorted))
    raise NotImplementedError(f"checker {checker.value} requires a checker program")


def _equal(spec: TypeSpec, a: Any, b: Any) -> bool:
    if spec.is_list:
        return len(a) == len(b) and all(_equal(spec.elem, x, y) for x, y in zip(a, b))
    if spec.kind == "double":
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def _canonical(spec: TypeSpec, v: Any) -> str:
    """Sort key that keeps near-equal doubles adjacent."""
    if spec.is_list:
        return "[" + ",".join(_canonical(spec.elem, x) for x in v) + "]"
    if spec.kind == "double":
        return f"{round(v, 6):.6f}"
    return json.dumps(v, ensure_ascii=False)
