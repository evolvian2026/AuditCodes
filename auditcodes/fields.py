"""Field addressing shared by the review UI and the audit: which fields exist, how to show them."""

from __future__ import annotations

from typing import Any

from .exec.languages import LANGUAGES
from .models import Language, Question

SECTIONS: list[tuple[str, str, str]] = [
    # (field path, label, kind)
    ("description_md", "Problem Statement", "md"),
    ("input_format_md", "Input Explanation", "md"),
    ("output_format_md", "Output Explanation", "md"),
    ("constraints", "Constraints", "lines"),
    ("sample_explanation_md", "Sample Test Case Explanation", "md"),
    ("editorial_md", "Editorial", "md"),
]
CONF_KEY = {"description_md": "description", "input_format_md": "input_format", "output_format_md": "output_format", "sample_explanation_md": "sample_explanation", "editorial_md": "editorial", "constraints": "constraints"}


def field_ctx(q: Question, path: str) -> dict[str, Any]:
    """Label, kind, raw text and extraction confidence for one field path."""
    kind = "text"
    label = path
    conf = None
    raw: str
    for p, lbl, k in SECTIONS:
        if p == path:
            kind, label = k, lbl
            conf = q.provenance.confidence.get(CONF_KEY[p])
    if path == "constraints":
        raw = "\n".join(c.text for c in q.constraints)
    elif path.startswith(("drivers.", "solutions.")):
        group, lang = path.split(".", 1)
        raw = getattr(q, group).get(Language(lang), "")
        kind = "code"
        label = f"{'Driver Code' if group == 'drivers' else 'Code Editorial'} — {LANGUAGES[Language(lang)].display_name}"
        conf = q.provenance.confidence.get("driver" if group == "drivers" else "solution")
    elif path.startswith(("samples.", "hidden_tests.")):
        group, idx, field = path.split(".")
        case = getattr(q, group)[int(idx)]
        raw = getattr(case, field) or ""
        kind = "code"
        label = f"{case.label or (group[:-1].replace('_', ' ') + ' ' + str(int(idx) + 1))} — {field}"
        conf = q.provenance.confidence.get("samples" if group == "samples" else "hidden")
    else:
        value = getattr(q, path, None)
        raw = "" if value is None else (value.value if hasattr(value, "value") else str(value))
    return {"path": path, "label": label, "kind": kind, "raw": raw, "confidence": conf}
