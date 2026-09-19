"""Driver generation.

A driver reads the wire protocol from stdin, calls the solution with native values and prints the
result as JSON. It is generated from the ``IOSpec`` so that driver correctness is never a matter of
opinion: the same contract produces the same driver every time.

Solution conventions the generated drivers expect (the "canonical" form every solution is
normalised to before verification):

* Python      — a module-level ``def name(...)`` or ``class Solution`` with method ``name``
* JavaScript  — a top-level ``function name`` / ``var name = function`` or ``class Solution``
* C++         — ``class Solution { public: R name(...); }``; ``vector``/``string`` by value or ref
* Java        — ``class Solution { public R name(...) }``; ``list<T>`` is ``T[]``
* C           — free function; ``list<T>`` params become ``T* name, int nameSize`` (nested:
                ``T** name, int nameSize, int* nameColSize``); list returns take ``int* returnSize``
                (nested: also ``int** returnColumnSizes``) — the LeetCode convention.
"""

from __future__ import annotations

from ...models import IOSpec, Language
from ...types import TypeSpec
from . import c, cpp, java, javascript, python


class UnsupportedSignatureError(ValueError):
    """The IOSpec cannot be expressed in this language's calling convention."""


_GENERATORS = {
    Language.C: c,
    Language.CPP: cpp,
    Language.JAVA: java,
    Language.PYTHON: python,
    Language.JAVASCRIPT: javascript,
}


def generate(language: Language, io_spec: IOSpec, solution_code: str) -> dict[str, str]:
    """Return ``{filename: contents}`` for a complete, buildable workdir."""
    return _GENERATORS[language].generate(io_spec, solution_code)


def signature(language: Language, io_spec: IOSpec) -> str:
    """The exact signature a solution must implement in ``language``."""
    return _GENERATORS[language].signature(io_spec)


def collect_list_types(io_spec: IOSpec) -> list[TypeSpec]:
    """Every distinct list type used by params or return, inner types before outer ones."""
    seen: dict[str, TypeSpec] = {}

    def visit(t: TypeSpec) -> None:
        if t.is_list:
            visit(t.elem)
            seen.setdefault(str(t), t)

    for p in io_spec.params:
        visit(p.type_spec)
    visit(io_spec.return_spec)
    return list(seen.values())


__all__ = ["generate", "signature", "collect_list_types", "UnsupportedSignatureError"]
