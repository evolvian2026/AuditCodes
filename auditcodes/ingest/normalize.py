"""Typographic clean-up for code recovered from documents.

PDF generators and word processors replace ``'`` with ``’`` and ``"`` with ``“ ”``, insert
non-breaking spaces, and use ``–`` for ``-``. Any of these makes source code uncompilable. The
extractor keeps the text as found; this module produces the *candidate* fix and a description of
what it changed so the audit can propose it and execution can confirm it.
"""

from __future__ import annotations

from dataclasses import dataclass

_REPLACEMENTS = {
    "‘": "'",  # ‘
    "’": "'",  # ’
    "‚": "'",  # ‚
    "‛": "'",  # ‛
    "“": '"',  # “
    "”": '"',  # ”
    "„": '"',  # „
    "′": "'",  # ′
    "″": '"',  # ″
    " ": " ",  # non-breaking space
    " ": " ",
    " ": " ",
    "–": "-",  # en dash
    "—": "-",  # em dash
    "−": "-",  # minus sign
    "×": "*",  # ×  (only meaningful in code; prose keeps it)
    "ﬁ": "fi",  # ligatures
    "ﬂ": "fl",
    "…": "...",
}

_NAMES = {
    "'": "typographic single quote",
    '"': "typographic double quote",
    " ": "non-breaking space",
    "-": "typographic dash",
    "*": "multiplication sign",
    "fi": "ligature",
    "fl": "ligature",
    "...": "ellipsis",
}


@dataclass
class Normalization:
    text: str
    changes: dict[str, int]  # description -> count

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def describe(self) -> str:
        return ", ".join(f"{n} {desc}" for desc, n in sorted(self.changes.items()))


def normalize_code(code: str) -> Normalization:
    changes: dict[str, int] = {}
    out: list[str] = []
    for ch in code:
        rep = _REPLACEMENTS.get(ch)
        if rep is None:
            out.append(ch)
            continue
        out.append(rep)
        desc = f"{_NAMES.get(rep, 'character')} {ch!r} -> {rep!r}"
        changes[desc] = changes.get(desc, 0) + 1
    return Normalization("".join(out), changes)
