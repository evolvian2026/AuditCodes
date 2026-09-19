"""Layout-aware text recovery from PDF pages.

Plain ``get_text()`` destroys exactly what an audit needs: inline code spans are pulled out of
their sentences, code indentation is lost, and lines that wrapped at the margin are
indistinguishable from real line breaks. This module rebuilds lines from span geometry instead:

* spans are grouped by baseline and ordered by x, so inline code/emphasis lands back in place;
* monospace lines keep their indentation, computed from the x offset in character widths;
* a line is joined to the previous one only when the previous line was *full* — its right edge
  plus the width of the next word would overflow the text column — otherwise the break is real;
* running headers/footers (text repeated at the same position on most pages) are dropped;
* images become placeholders positioned among the lines.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

_MONO_FLAG = 8
_ITALIC_FLAG = 2
_BOLD_FLAG = 16
_MONO_FONT = re.compile(r"courier|mono|consolas|menlo|code", re.IGNORECASE)


@dataclass
class Span:
    text: str
    x0: float
    x1: float
    y: float  # baseline
    size: float
    font: str
    bold: bool
    italic: bool
    mono: bool


@dataclass
class Line:
    page: int  # 1-based
    y: float
    x0: float
    x1: float
    spans: list[Span]
    mono: bool
    blank_before: int = 0  # blank lines inferred from the vertical gap above this line
    joined: bool = False  # this line absorbed a wrapped continuation
    indent: int = 0  # code indentation in characters (mono lines only)

    @property
    def text(self) -> str:
        """Plain text, emphasis dropped."""
        return _join_spans(self.spans, markup=False)

    @property
    def markdown(self) -> str:
        """Text with ``**bold**`` / ``*italic*`` markup (prose lines only)."""
        return _join_spans(self.spans, markup=True)

    @property
    def bold(self) -> bool:
        return all(s.bold for s in self.spans if s.text.strip())

    @property
    def bold_prefix(self) -> bool:
        """The line starts with bold text (a heading followed by regular text on the same line)."""
        for s in self.spans:
            if s.text.strip():
                return s.bold
        return False

    @property
    def size(self) -> float:
        return max(s.size for s in self.spans)


@dataclass
class ImageRef:
    page: int
    y: float
    xref: int
    bbox: tuple[float, float, float, float]
    path: Path | None = None


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)


@dataclass
class Document:
    path: Path
    pages: list[Page]
    title: str | None = None

    def lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]


def load(pdf_path: str | Path, assets_dir: Path | None = None) -> Document:
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    raw_pages: list[tuple[pymupdf.Page, list[Span], list[ImageRef]]] = []
    for pno, page in enumerate(doc, start=1):
        spans = _spans(page)
        images = [
            ImageRef(pno, page.get_image_bbox(img).y0, img[0], tuple(page.get_image_bbox(img)))
            for img in page.get_images(full=True)
        ]
        raw_pages.append((page, spans, images))

    decoration_text = _running_text(raw_pages)
    decoration_xrefs = _decoration_images(raw_pages)

    pages: list[Page] = []
    for page, spans, images in raw_pages:
        bands = _band(decoration_text)
        keep = [s for s in spans if not any(abs(s.y - y) <= 3 for y in bands)]
        lines = _group_lines(keep, page.number + 1)
        content_images = [im for im in images if im.xref not in decoration_xrefs and im.bbox[3] - im.bbox[1] > 2]
        pages.append(Page(page.number + 1, page.rect.width, page.rect.height, lines, content_images))

    _infer_gaps(pages)
    _join_wrapped(pages)
    if assets_dir is not None:
        _save_images(doc, pages, assets_dir, pdf_path.stem)
    return Document(pdf_path, pages, (doc.metadata or {}).get("title") or None)


# --- span extraction -------------------------------------------------------------------------


def _spans(page: pymupdf.Page) -> list[Span]:
    out: list[Span] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for s in line["spans"]:
                if not s["text"]:
                    continue
                flags = s["flags"]
                mono = bool(flags & _MONO_FLAG) or bool(_MONO_FONT.search(s["font"]))
                out.append(
                    Span(
                        text=s["text"],
                        x0=s["bbox"][0],
                        x1=s["bbox"][2],
                        y=s["origin"][1],
                        size=s["size"],
                        font=s["font"],
                        bold=bool(flags & _BOLD_FLAG) or "bold" in s["font"].lower(),
                        italic=bool(flags & _ITALIC_FLAG) or "ital" in s["font"].lower(),
                        mono=mono,
                    )
                )
    return out


def _running_text(raw_pages) -> set[tuple[int, str]]:
    """Text that repeats at the same baseline on most pages is a running header/footer."""
    if len(raw_pages) < 2:
        return set()
    counts: Counter[tuple[int, str]] = Counter()
    for _, spans, _ in raw_pages:
        for key in {(round(s.y), s.text.strip()) for s in spans if s.text.strip()}:
            counts[key] += 1
    threshold = max(2, len(raw_pages) - 1)
    return {k for k, c in counts.items() if c >= threshold}


def _band(decoration: set[tuple[int, str]]) -> set[int]:
    """Baselines occupied by running text — page numbers sit on them but change every page."""
    return {y for y, _ in decoration}


def _decoration_images(raw_pages) -> set[int]:
    if len(raw_pages) < 2:
        return set()
    counts: Counter[int] = Counter()
    for _, _, images in raw_pages:
        for xref in {im.xref for im in images}:
            counts[xref] += 1
    return {xref for xref, c in counts.items() if c >= max(2, len(raw_pages) - 1)}


# --- line assembly ---------------------------------------------------------------------------


def _group_lines(spans: list[Span], page_no: int) -> list[Line]:
    spans = sorted(spans, key=lambda s: (s.y, s.x0))
    lines: list[Line] = []
    for s in spans:
        if lines and abs(lines[-1].y - s.y) <= 2.0:
            lines[-1].spans.append(s)
        else:
            lines.append(Line(page_no, s.y, s.x0, s.x1, [s], s.mono))
    for ln in lines:
        ln.spans.sort(key=lambda s: s.x0)
        ln.x0 = min(s.x0 for s in ln.spans)
        ln.x1 = max(s.x1 for s in ln.spans)
        text_spans = [s for s in ln.spans if s.text.strip()]
        ln.mono = bool(text_spans) and sum(s.mono for s in text_spans) * 2 > len(text_spans)
    return [ln for ln in lines if ln.text.strip()]


def _join_spans(spans: list[Span], markup: bool) -> str:
    pieces: list[str] = []
    prev: Span | None = None
    for sp in spans:
        text = sp.text
        if prev is not None:
            gap = sp.x0 - prev.x1
            boundary_space = pieces[-1].endswith(" ") or text.startswith(" ")
            if gap > 0.25 * sp.size and not boundary_space and not text.lstrip()[:1] in _NO_SPACE_BEFORE:
                pieces.append(" ")
        pieces.append(text)
        prev = sp
    if not markup:
        return "".join(pieces)
    return _markup(spans, pieces)


_NO_SPACE_BEFORE = set(",.;:)]}!?")
_WORD_CHARS = re.compile(r"[\w'\"]")


def _has_word_chars(text: str) -> bool:
    return bool(_WORD_CHARS.search(text))


def _markup(spans: list[Span], pieces: list[str]) -> str:
    """Wrap runs of same-styled prose spans in ``**`` / ``*`` markers."""
    # pieces alternates inserted spaces and span texts; rebuild with styles attached
    styled: list[tuple[tuple[bool, bool], str]] = []
    si = 0
    for piece in pieces:
        if si < len(spans) and piece is spans[si].text:
            sp = spans[si]
            si += 1
            style = (False, False) if sp.mono else (sp.bold, sp.italic)
            if not _has_word_chars(piece):
                style = None  # punctuation-only spans take the style of their neighbours
            styled.append((style, piece))
        else:
            styled.append((None, piece))  # inserted space: neutral, merges into either side
    out: list[str] = []
    run_style: tuple[bool, bool] | None = None
    run: list[str] = []

    def flush() -> None:
        if not run:
            return
        text = "".join(run)
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        core = text.strip()
        if run_style and core and any(run_style):
            m = "***" if run_style == (True, True) else "**" if run_style[0] else "*"
            core = f"{m}{core}{m}"
        out.append(f"{lead}{core}{trail}")

    for style, piece in styled:
        if style is None or style == run_style or not piece.strip():
            run.append(piece)
            continue
        flush()
        run_style, run = style, [piece]
    flush()
    return "".join(out)


# --- geometry-driven structure ---------------------------------------------------------------


def _infer_gaps(pages: list[Page]) -> None:
    for page in pages:
        heights: list[float] = []
        for a, b in zip(page.lines, page.lines[1:]):
            if a.mono == b.mono:
                heights.append(b.y - a.y)
        for a, b in zip(page.lines, page.lines[1:]):
            step = _typical_step(heights, b.mono)
            gap = b.y - a.y
            if step and gap > 1.5 * step:
                b.blank_before = max(1, round(gap / step) - 1)


def _typical_step(heights: list[float], mono: bool) -> float | None:
    positive = [h for h in heights if 3 < h < 40]
    if not positive:
        return None
    return statistics.median(positive)


def _join_wrapped(pages: list[Page]) -> None:
    """Merge a line into the previous one only when the previous line was full."""
    for mono in (False, True):
        all_lines = [ln for p in pages for ln in p.lines if ln.mono == mono]
        if not all_lines:
            continue
        right_limit = max(ln.x1 for ln in all_lines)
        for page in pages:
            merged: list[Line] = []
            for ln in page.lines:
                if ln.mono != mono or not merged or merged[-1].mono != mono or ln.blank_before:
                    merged.append(ln)
                    continue
                prev = merged[-1]
                if _would_overflow(prev, ln, right_limit, mono):
                    prev.spans.extend([_space_span(prev), *ln.spans])
                    prev.x1 = ln.x1  # the right edge that matters next is the continuation's
                    prev.joined = True
                else:
                    merged.append(ln)
            page.lines = merged


def _would_overflow(prev: Line, cur: Line, right_limit: float, mono: bool) -> bool:
    first_word = cur.text.strip().split(" ", 1)[0]
    if not first_word:
        return False
    if mono:
        char_w = 0.6 * cur.size
        # A wrapped code line resumes at the block's left edge, below a line that was indented,
        # and the broken line cannot already be syntactically complete.
        if cur.x0 > prev.x0 - char_w or prev.text.rstrip()[-1:] in _CODE_LINE_ENDS:
            return False
    else:
        char_w = (cur.x1 - cur.x0) / max(1, len(cur.text))
    needed = (len(first_word) + 1) * char_w
    return prev.x1 + needed > right_limit + 0.5


_CODE_LINE_ENDS = set(";{}:")


def _space_span(prev: Line) -> Span:
    s = prev.spans[-1]
    return Span(" ", s.x1, s.x1, s.y, s.size, s.font, s.bold, s.italic, s.mono)


def _save_images(doc: pymupdf.Document, pages: list[Page], assets_dir: Path, stem: str) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        for i, im in enumerate(page.images, start=1):
            info = doc.extract_image(im.xref)
            if not info:
                continue
            path = assets_dir / f"{stem}_p{page.number}_{i}.{info['ext']}"
            path.write_bytes(info["image"])
            im.path = path


def code_text(lines: list[Line]) -> str:
    """Reassemble monospace lines into source text, recovering indentation from x offsets.

    The base column is the leftmost line of the *whole block*, so a block that continues on the
    next page keeps its indentation even when the page starts deep inside a nested scope.

    Generators indent by a fixed step that is rarely an exact number of character widths (19.3pt
    per level against 4.62pt per Courier-7.7 character gives 4, 8, 13, 17, 21 spaces if rounded
    naively). So the indentation *unit* is inferred from the block: the largest step that explains
    at least 90% of the lines as whole levels. Lines that do not sit on a level (a continuation
    aligned to a bracket, say) fall back to character-width rounding.
    """
    if not lines:
        return ""
    base = min(ln.x0 for ln in lines)
    offsets = [ln.x0 - base for ln in lines]
    unit = _indent_unit(offsets)
    out: list[str] = []
    for i, (ln, off) in enumerate(zip(lines, offsets)):
        char_w = 0.6 * ln.size
        if unit and _on_level(off, unit):
            ln.indent = round(off / unit) * max(1, round(unit / char_w))
        else:
            ln.indent = max(0, round(off / char_w)) if char_w else 0
        if i and ln.page == lines[i - 1].page:
            out.extend([""] * ln.blank_before)
        out.append(" " * ln.indent + ln.text.strip())
    return "\n".join(out) + "\n"


def _indent_unit(offsets: list[float]) -> float | None:
    candidates = sorted({round(o, 1) for o in offsets if o > 2.0}, reverse=True)
    for unit in candidates:
        fits = sum(1 for o in offsets if _on_level(o, unit))
        if fits >= 0.9 * len(offsets):
            return unit
    return None


def _on_level(offset: float, unit: float) -> bool:
    ratio = offset / unit
    return abs(ratio - round(ratio)) < 0.15
