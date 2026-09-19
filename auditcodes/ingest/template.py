"""Parser for the question-bank template (Kendo-generated "Coding Ques" export).

The layout is fixed and machine-generated, so the segmentation is deterministic: known bold
headings start sections, ``Q.<n>`` starts a question, ``Example n`` / ``Test Case n`` start test
cases. Deterministic parsing is reproducible, testable against a fixture, and free — an LLM is
only worth involving for documents that do not match a known template.

Everything the parser is unsure about becomes a warning and a lowered confidence on the affected
field, so the review screen can put the reviewer's attention where extraction may have gone wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Constraint, Difficulty, ImageAsset, IOMode, Language, Provenance, Question, TestCase, TestCategory, TestOrigin
from .layout import Document, ImageRef, Line, code_text
from .normalize import normalize_code

_LEFT_MARGIN_TOLERANCE = 12.0

_QUESTION_START = re.compile(r"^Q\.?\s*(\d+)\b\.?\s*(.*)$", re.IGNORECASE)
_TITLE_TAIL = re.compile(
    r"^(?P<title>.*?)\s*(?P<diff>Easy|Moderate|Medium|Hard)?\s*(?:[·•|\-–—]\s*)?(?:Time\s*limit\s*(?P<time>[\d.]+)\s*(?:s|sec|seconds?))?\s*$",
    re.IGNORECASE,
)
_DBNO = re.compile(r"DBNO\s*[:\-]?\s*([A-Za-z0-9_\-]+)", re.IGNORECASE)
_AREA = re.compile(r"Area\s*[:\-]?\s*(.+?)\s*(?=LOD\s*[:\-]|DBNO\s*[:\-]|$)", re.IGNORECASE)
_LOD = re.compile(r"LOD\s*[:\-]?\s*(\d+)", re.IGNORECASE)
_COUNT = re.compile(r"\((\d+)\)")
_LANG_TAIL = re.compile(r"[—–\-:·•|]\s*(?P<lang>[A-Za-z+#0-9 .]+)$")
_CASE_HEADER = re.compile(r"^(Example|Test\s*Case|Sample|Case)\s*(\d+)\s*(.*)$", re.IGNORECASE)
_LEVEL = re.compile(r"Level\s*[:\-]?\s*(\w+)", re.IGNORECASE)
_POINTS = re.compile(r"Points?\s*[:\-]?\s*(\d+)", re.IGNORECASE)

# (section key, regex over the heading text without markup)
_SECTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("description", re.compile(r"^problem\s+(statement|description)$", re.I)),
    ("input_format", re.compile(r"^input\s+(explanation|format|description)$", re.I)),
    ("output_format", re.compile(r"^output\s+(explanation|format|description)$", re.I)),
    ("constraints", re.compile(r"^constraints?$", re.I)),
    ("sample_explanation", re.compile(r"^sample\s+test\s+cases?\s+explanations?$", re.I)),
    ("samples", re.compile(r"^sample\s+test\s+cases?(\s*\(\d+\))?$", re.I)),
    ("driver", re.compile(r"^driver\s+code(\s*[—–\-:·•|].*)?$", re.I)),
    ("editorial", re.compile(r"^editorial(\s+explanation)?$", re.I)),
    ("solution", re.compile(r"^(code\s+editorial|editorial\s+code|solution(\s+code)?|reference\s+solution)(\s*[—–\-:·•|].*)?$", re.I)),
    ("hidden", re.compile(r"^hidden\s+test\s+cases?(\s*\(\d+\))?$", re.I)),
]

_EXPECTED_SECTIONS = ["description", "input_format", "output_format", "constraints", "samples", "driver", "editorial", "solution", "hidden"]

_LANGUAGE_NAMES = {
    "java": Language.JAVA,
    "python": Language.PYTHON,
    "python3": Language.PYTHON,
    "py": Language.PYTHON,
    "c++": Language.CPP,
    "cpp": Language.CPP,
    "c": Language.C,
    "javascript": Language.JAVASCRIPT,
    "js": Language.JAVASCRIPT,
    "node": Language.JAVASCRIPT,
    "nodejs": Language.JAVASCRIPT,
}

_LOD_DIFFICULTY = {33: Difficulty.EASY, 66: Difficulty.MEDIUM, 99: Difficulty.HARD}
_WORD_DIFFICULTY = {"easy": Difficulty.EASY, "moderate": Difficulty.MEDIUM, "medium": Difficulty.MEDIUM, "hard": Difficulty.HARD}


Item = Line | ImageRef


@dataclass
class _RawQuestion:
    number: int
    title_line: Line
    pages: set[int] = field(default_factory=set)
    preamble: list[Line] = field(default_factory=list)  # lines between the title and the first section
    sections: dict[str, list[Item]] = field(default_factory=dict)
    section_headings: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    questions: list[Question]
    warnings: list[str] = field(default_factory=list)
    title: str | None = None

    @property
    def total_warnings(self) -> int:
        return len(self.warnings) + sum(len(q.provenance.warnings) for q in self.questions)


def parse(doc: Document, assets_dir: Path | None = None) -> ExtractionResult:
    items = _ordered_items(doc)
    left_margin = min((ln.x0 for ln in doc.lines()), default=0.0)
    raws: list[_RawQuestion] = []
    doc_warnings: list[str] = []
    current: _RawQuestion | None = None
    section: str | None = None

    for item in items:
        if isinstance(item, ImageRef):
            if current is not None:
                current.sections.setdefault(section or "description", []).append(item)
                current.pages.add(item.page)
            continue
        ln = item
        plain = ln.text.strip()
        m = _QUESTION_START.match(plain)
        if m and ln.bold_prefix and not ln.mono:
            current = _RawQuestion(int(m.group(1)), ln)
            current.pages.add(ln.page)
            raws.append(current)
            section = None
            continue
        if current is None:
            if plain.lower() not in ("questions", "question", "coding questions"):
                doc_warnings.append(f"page {ln.page}: text before the first question ignored: {plain[:60]!r}")
            continue
        current.pages.add(ln.page)
        key = _section_key(ln, left_margin)
        if key is not None:
            if key in current.sections:
                current.warnings.append(f"duplicate section heading {plain!r} on page {ln.page}; content appended")
            section = key
            current.sections.setdefault(key, [])
            current.section_headings.setdefault(key, plain)
            if key not in current.order:
                current.order.append(key)
            continue
        if section is None:
            current.preamble.append(ln)
        else:
            if _looks_like_heading(ln, left_margin) and section not in ("samples", "hidden"):
                current.warnings.append(f"page {ln.page}: unrecognised heading-like line inside {section!r}: {plain[:60]!r}")
            current.sections[section].append(ln)

    questions = [_build(r, assets_dir) for r in raws]
    ids = [q.id for q in questions]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        doc_warnings.append(f"question id {dup!r} appears {ids.count(dup)} times")
    return ExtractionResult(questions, doc_warnings, doc.title)


def _looks_like_heading(ln: Line, left_margin: float) -> bool:
    text = ln.text.strip()
    return (
        ln.bold
        and not ln.mono
        and abs(ln.x0 - left_margin) <= _LEFT_MARGIN_TOLERANCE
        and len(text.split()) <= 6
        and "=" not in text
    )


def _ordered_items(doc: Document) -> list[Item]:
    items: list[Item] = []
    for page in doc.pages:
        merged: list[Item] = [*page.lines, *page.images]
        merged.sort(key=lambda it: it.y)
        items.extend(merged)
    return items


def _section_key(ln: Line, left_margin: float) -> str | None:
    if ln.mono or not ln.bold or abs(ln.x0 - left_margin) > _LEFT_MARGIN_TOLERANCE:
        return None
    text = ln.text.strip()
    for key, pattern in _SECTIONS:
        if pattern.match(text):
            return key
    return None


# --- building a Question ---------------------------------------------------------------------


def _build(raw: _RawQuestion, assets_dir: Path | None) -> Question:
    warnings = list(raw.warnings)
    confidence: dict[str, float] = {}
    metadata: dict[str, str] = {}
    images: list[ImageAsset] = []

    title, word_diff, time_limit = _parse_title(raw.title_line, warnings)
    dbno, area, lod = _parse_preamble(raw.preamble, warnings)
    if dbno is None:
        warnings.append("DBNO not found; using the question number as id")
        confidence["id"] = 0.0
    else:
        metadata["DBNO"] = dbno
        confidence["id"] = 1.0
    if area:
        metadata["Area"] = area
    if lod is not None:
        metadata["LOD"] = str(lod)
    if word_diff:
        metadata["Difficulty"] = word_diff

    difficulty = _LOD_DIFFICULTY.get(lod) if lod is not None else None
    if lod is not None and difficulty is None:
        warnings.append(f"LOD {lod} is not one of 33/66/99")
    if word_diff and difficulty and _WORD_DIFFICULTY.get(word_diff.lower()) != difficulty:
        warnings.append(f"title says {word_diff!r} but LOD {lod} means {difficulty.value}")
    if difficulty is None and word_diff:
        difficulty = _WORD_DIFFICULTY.get(word_diff.lower())

    for key in _EXPECTED_SECTIONS:
        if key not in raw.sections:
            warnings.append(f"section {key!r} not found")
            confidence[key] = 0.0

    def prose(key: str) -> str | None:
        items = raw.sections.get(key)
        if items is None:
            return None
        text = _prose_markdown(items, key, images, assets_dir, warnings)
        confidence[key] = _prose_confidence(items, text)
        return text

    description = prose("description") or ""
    input_format = prose("input_format")
    output_format = prose("output_format")
    sample_explanation = prose("sample_explanation")
    editorial = prose("editorial")

    constraints: list[Constraint] = []
    if "constraints" in raw.sections:
        lines = [it for it in raw.sections["constraints"] if isinstance(it, Line)]
        constraints = [Constraint(text=ln.text.strip()) for ln in lines if ln.text.strip()]
        confidence["constraints"] = 1.0 if constraints else 0.3
        if not constraints:
            warnings.append("constraints section is empty")

    drivers: dict[Language, str] = {}
    solutions: dict[Language, str] = {}
    for key, target in (("driver", drivers), ("solution", solutions)):
        if key not in raw.sections:
            continue
        lang = _language_from_heading(raw.section_headings.get(key, ""))
        code, conf = _code_block(raw.sections[key], key, warnings)
        if lang is None:
            warnings.append(f"could not determine the language of {raw.section_headings.get(key, key)!r}; code kept in metadata")
            metadata[f"{key}_code_unknown_language"] = code
            conf = 0.3
        else:
            target[lang] = code
        confidence[key] = conf

    samples = _cases(raw.sections.get("samples", []), "samples", TestCategory.SAMPLE, raw.section_headings.get("samples", ""), warnings, confidence)
    hidden = _cases(raw.sections.get("hidden", []), "hidden", TestCategory.ORIGINAL, raw.section_headings.get("hidden", ""), warnings, confidence)

    unknown = [k for k in raw.order if k not in _EXPECTED_SECTIONS and k != "sample_explanation"]
    for k in unknown:
        warnings.append(f"unexpected section {k!r}")

    return Question(
        id=dbno or f"Q{raw.number}",
        number=raw.number,
        title=title,
        io_mode=IOMode.STDIO,
        description_md=description,
        input_format_md=input_format,
        output_format_md=output_format,
        constraints=constraints,
        samples=samples,
        sample_explanation_md=sample_explanation,
        hidden_tests=hidden,
        drivers=drivers,
        editorial_md=editorial,
        solutions=solutions,
        difficulty=difficulty,
        lod=lod,
        area=area,
        time_limit_seconds=time_limit,
        images=images,
        metadata=metadata,
        provenance=Provenance(pages=sorted(raw.pages), confidence=confidence, warnings=warnings),
    )


def _parse_title(ln: Line, warnings: list[str]) -> tuple[str, str | None, float | None]:
    m = _QUESTION_START.match(ln.text.strip())
    rest = m.group(2) if m else ln.text.strip()
    t = _TITLE_TAIL.match(rest)
    if not t or not t.group("title"):
        warnings.append(f"could not split title line {rest!r}")
        return rest, None, None
    time_limit = float(t.group("time")) if t.group("time") else None
    if time_limit is None:
        warnings.append("time limit not found in the title line")
    return t.group("title").strip(), t.group("diff"), time_limit


def _parse_preamble(lines: list[Line], warnings: list[str]) -> tuple[str | None, str | None, int | None]:
    text = " ".join(ln.text.strip() for ln in lines)
    dbno = _DBNO.search(text)
    area = _AREA.search(text)
    lod = _LOD.search(text)
    leftover = _LOD.sub("", _AREA.sub("", _DBNO.sub("", text))).strip()
    if leftover:
        warnings.append(f"unparsed text between title and first section: {leftover[:60]!r}")
    return (dbno.group(1) if dbno else None, area.group(1).strip() if area else None, int(lod.group(1)) if lod else None)


def _language_from_heading(heading: str) -> Language | None:
    m = _LANG_TAIL.search(heading)
    if not m:
        return None
    return _LANGUAGE_NAMES.get(m.group("lang").strip().lower())


def _prose_markdown(items: list[Item], section: str, images: list[ImageAsset], assets_dir: Path | None, warnings: list[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    code_run: list[Line] = []

    def flush_prose() -> None:
        if current:
            paragraphs.append("\n".join(current))
            current.clear()

    def flush_code() -> None:
        if code_run:
            paragraphs.append("```\n" + code_text(code_run).rstrip("\n") + "\n```")
            code_run.clear()

    for it in items:
        if isinstance(it, ImageRef):
            flush_prose()
            flush_code()
            name = it.path.name if it.path else f"page{it.page}_image"
            images.append(ImageAsset(path=name, page=it.page, section=section))
            paragraphs.append(f"![Figure]({name})")
            continue
        if it.mono:
            flush_prose()
            code_run.append(it)
            continue
        flush_code()
        if it.blank_before and current:
            flush_prose()
        current.append(it.markdown.strip())
    flush_prose()
    flush_code()
    return "\n\n".join(paragraphs)


def _prose_confidence(items: list[Item], text: str) -> float:
    if not text.strip():
        return 0.3
    lines = [it for it in items if isinstance(it, Line)]
    return 0.85 if any(ln.joined for ln in lines) else 1.0


def _code_block(items: list[Item], section: str, warnings: list[str]) -> tuple[str, float]:
    lines = [it for it in items if isinstance(it, Line)]
    if any(isinstance(it, ImageRef) for it in items):
        warnings.append(f"image inside the {section} code section ignored")
    prose = [ln for ln in lines if not ln.mono]
    conf = 1.0
    if prose:
        warnings.append(f"{section}: {len(prose)} non-monospace line(s) inside the code block: {prose[0].text.strip()[:50]!r}")
        conf = 0.7
    code = code_text(lines)
    if not code.strip():
        warnings.append(f"{section}: code block is empty")
        return code, 0.0
    joined = [ln for ln in lines if ln.joined]
    if joined:
        warnings.append(f"{section}: {len(joined)} wrapped code line(s) re-joined (verify by compiling)")
        conf = min(conf, 0.85)
    norm = normalize_code(code)
    if norm.changed:
        warnings.append(f"{section}: typographic characters in code ({norm.describe()}); the code will not compile as written")
        conf = min(conf, 0.6)
    return code, conf


def _cases(items: list[Item], section: str, category: TestCategory, heading: str, warnings: list[str], confidence: dict[str, float]) -> list[TestCase]:
    lines = [it for it in items if isinstance(it, Line)]
    cases: list[TestCase] = []
    conf = 1.0
    cur: dict | None = None
    part: str | None = None

    def finish() -> None:
        nonlocal cur
        if cur is None:
            return
        stdin = code_text(cur["stdin"]) if cur["stdin"] else None
        stdout = code_text(cur["stdout"]) if cur["stdout"] else None
        if stdin is None or stdout is None:
            warnings.append(f"{section}: {cur['label']} is missing {'input' if stdin is None else 'output'}")
            stdin = stdin or ""
            stdout = stdout or ""
        cases.append(
            TestCase(
                stdin=stdin,
                stdout=stdout,
                label=cur["label"],
                points=cur["points"],
                difficulty=cur["difficulty"],
                category=category,
                origin=TestOrigin.SOURCE,
            )
        )
        cur = None

    for ln in lines:
        text = ln.text.strip()
        if not ln.mono:
            m = _CASE_HEADER.match(text)
            if m and ln.bold_prefix:
                finish()
                tail = m.group(3)
                level = _LEVEL.search(tail)
                points = _POINTS.search(tail)
                cur = {
                    "label": f"{m.group(1).title()} {m.group(2)}",
                    "stdin": [],
                    "stdout": [],
                    "points": int(points.group(1)) if points else None,
                    "difficulty": _WORD_DIFFICULTY.get(level.group(1).lower()) if level else None,
                }
                if level and cur["difficulty"] is None:
                    warnings.append(f"{section}: unknown level {level.group(1)!r} on {cur['label']}")
                part = None
                continue
            low = text.lower().rstrip(":")
            if low in ("input", "sample input", "stdin"):
                part = "stdin"
                continue
            if low in ("output", "sample output", "expected output", "stdout"):
                part = "stdout"
                continue
            if cur is None:
                warnings.append(f"{section}: text before the first case ignored: {text[:50]!r}")
            else:
                warnings.append(f"{section}: unexpected text in {cur['label']}: {text[:50]!r}")
            conf = min(conf, 0.7)
            continue
        if cur is None or part is None:
            warnings.append(f"{section}: data line outside Input/Output: {text[:50]!r}")
            conf = min(conf, 0.5)
            continue
        cur[part].append(ln)
    finish()

    declared = _COUNT.search(heading)
    if declared and int(declared.group(1)) != len(cases):
        warnings.append(f"{section}: heading declares {declared.group(1)} case(s) but {len(cases)} were parsed")
        conf = min(conf, 0.5)
    if not cases and items:
        conf = 0.2
    confidence[section] = conf if items else 0.0
    return cases
