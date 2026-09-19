"""Canonical HTML rendering of an audited question bank."""

from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..render import markdown
from ..audit.report import AuditReport
from ..exec.languages import LANGUAGES
from ..models import Language, Question

_HERE = Path(__file__).parent


@dataclass
class ExportOptions:
    title: str = "Audited Coding Questions"
    source_name: str | None = None
    include_hidden: bool = True
    include_drivers: bool = True
    include_solutions: bool = True
    include_editorial: bool = True
    include_audit: bool = True
    languages: list[Language] | None = None  # None = every language present
    image_mode: str = "data"  # "data": embed images as data URIs; "relative": keep file names
    wrap_code_at: int | None = None  # soft-wrap long code lines (for renderers without pre-wrap)
    question_ids: list[str] | None = None
    generated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M"))


CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; @bottom-center { content: counter(page) " / " counter(pages); font: 8pt sans-serif; color: #666; } @top-right { content: string(doctitle); font: 8pt sans-serif; color: #666; } }
body { font-family: "DejaVu Sans", Helvetica, Arial, sans-serif; font-size: 9.5pt; line-height: 1.4; color: #1f2933; }
h1.doc { string-set: doctitle content(); font-size: 22pt; margin: 0 0 4pt; }
.cover .sub { color: #555; margin: 0 0 14pt; }
h1.q { font-size: 15pt; margin: 0 0 3pt; border-bottom: 2px solid #1d4ed8; padding-bottom: 3pt; page-break-before: always; }
.meta { color: #444; font-size: 8.5pt; margin: 0 0 8pt; }
.meta span { margin-right: 10pt; }
h2 { font-size: 11pt; margin: 11pt 0 4pt; color: #1d4ed8; }
h3 { font-size: 9.5pt; margin: 8pt 0 3pt; }
p { margin: 0 0 5pt; }
ul, ol { margin: 0 0 5pt 16pt; padding: 0; }
pre { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 7.8pt; line-height: 1.3; background: #f3f4f6; border: 1px solid #d1d5db; padding: 5pt 6pt; margin: 0 0 6pt; white-space: pre-wrap; word-wrap: break-word; }
code { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; margin: 0 0 8pt; font-size: 8.5pt; }
th, td { border: 1px solid #d1d5db; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #e5e7eb; }
.io { width: 100%; margin: 0 0 6pt; }
.io td { width: 50%; border: none; padding: 0 4pt 0 0; }
.io td + td { padding: 0 0 0 4pt; }
.lbl { font-size: 8pt; color: #555; margin: 0 0 2pt; }
.badge { display: inline-block; font-size: 7.5pt; padding: 0 4pt; border: 1px solid #9ca3af; color: #374151; margin-left: 4pt; }
.sev-blocker { color: #b91c1c; font-weight: bold; } .sev-major { color: #b45309; font-weight: bold; } .sev-minor { color: #854d0e; } .sev-info { color: #075985; }
.generated { color: #065f46; }
img { max-width: 100%; }
.small { font-size: 8pt; color: #555; }
"""


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(_HERE / "templates")), autoescape=select_autoescape(["html"]))
    env.globals["language_name"] = lambda lang: LANGUAGES[Language(lang)].display_name if not isinstance(lang, Language) else LANGUAGES[lang].display_name
    return env


def export_html(questions: list[Question], reports: dict[str, AuditReport] | None = None, options: ExportOptions | None = None, assets_dir: Path | None = None, css: str | None = None) -> str:
    options = options or ExportOptions()
    reports = reports or {}
    if options.question_ids:
        questions = [q for q in questions if q.id in options.question_ids]
    env = _env()
    resolver = _image_resolver(assets_dir, options.image_mode)

    def md(text: str | None) -> str:
        html = markdown(text, "")
        return _rewrite_images(html, resolver)

    def code(text: str | None) -> str:
        text = (text or "").rstrip("\n")
        if options.wrap_code_at:
            text = "\n".join(_soft_wrap(ln, options.wrap_code_at) for ln in text.split("\n"))
        return text

    env.filters["md"] = md
    env.filters["code"] = code
    tpl = env.get_template("document.html")
    return tpl.render(
        css=CSS if css is None else css,
        options=options,
        questions=questions,
        reports=reports,
        languages_of=lambda q: _languages(q, options),
        summary=[_summary_row(q, reports.get(q.id)) for q in questions],
    )


def _languages(q: Question, options: ExportOptions) -> list[Language]:
    present = [l for l in Language if l in q.solutions or l in q.drivers]
    if options.languages:
        present = [l for l in present if l in options.languages]
    return present


def _summary_row(q: Question, report: AuditReport | None) -> dict:
    row = {
        "q": q,
        "languages": ", ".join(LANGUAGES[l].display_name for l in Language if l in q.solutions),
        "tests": f"{len(q.samples)} + {len(q.hidden_tests)}",
        "audit": "not audited",
        "counts": None,
    }
    if report:
        c = report.counts()
        row["counts"] = c
        applied = sum(1 for f in report.findings if f.applied)
        row["audit"] = f"{c['blocker']} blocker, {c['major']} major open; {applied} change(s) applied"
    return row


def _image_resolver(assets_dir: Path | None, mode: str):
    def resolve(name: str) -> str:
        if mode == "relative" or assets_dir is None or "://" in name or name.startswith("data:"):
            return name
        path = assets_dir / Path(name).name
        if not path.is_file():
            return name
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"

    return resolve


def _rewrite_images(html: str, resolve) -> str:
    import re

    return re.sub(r'<img src="([^"]+)"', lambda m: f'<img src="{resolve(m.group(1))}"', html)


def _soft_wrap(line: str, width: int) -> str:
    if len(line) <= width:
        return line
    indent = len(line) - len(line.lstrip(" "))
    out, cur = [], line
    while len(cur) > width:
        cut = cur.rfind(" ", indent + 1, width)
        if cut <= indent:
            cut = width
        out.append(cur[:cut])
        cur = " " * (indent + 4) + cur[cut:].lstrip(" ")
    out.append(cur)
    return "\n".join(out)
