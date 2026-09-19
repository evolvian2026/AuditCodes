"""PDF from the canonical HTML. WeasyPrint when available (full CSS, page numbers), else PyMuPDF's
Story engine, which needs no system libraries."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from ..audit.report import AuditReport
from ..models import Question
from .html import ExportOptions, export_html


def pdf_backend() -> str:
    try:
        import weasyprint  # noqa: F401

        return "weasyprint"
    except Exception:
        return "pymupdf"


def export_pdf(questions: list[Question], reports: dict[str, AuditReport] | None = None, options: ExportOptions | None = None, assets_dir: Path | None = None, backend: str | None = None) -> bytes:
    backend = backend or pdf_backend()
    options = options or ExportOptions()
    if backend == "weasyprint":
        import weasyprint

        html = export_html(questions, reports, options, assets_dir)
        return weasyprint.HTML(string=html, base_url=str(assets_dir) if assets_dir else None).write_pdf()
    return _pymupdf_pdf(questions, reports, options, assets_dir)


def _pymupdf_pdf(questions, reports, options: ExportOptions, assets_dir: Path | None) -> bytes:
    import pymupdf

    opts = ExportOptions(**{**options.__dict__, "wrap_code_at": options.wrap_code_at or 96})
    html = export_html(questions, reports, opts, assets_dir, css=_story_css())
    archive = pymupdf.Archive(str(assets_dir)) if assets_dir and assets_dir.is_dir() else None
    story = pymupdf.Story(html=html, archive=archive)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.pdf"
        writer = pymupdf.DocumentWriter(str(out))
        page_rect = pymupdf.paper_rect("a4")
        where = page_rect + (45, 50, -45, -55)
        more = 1
        while more:
            dev = writer.begin_page(page_rect)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
        writer.close()
        doc = pymupdf.open(str(out))
        n = len(doc)
        for i, page in enumerate(doc, start=1):
            page.insert_text((page_rect.width / 2 - 12, page_rect.height - 28), f"{i} / {n}", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
            page.insert_text((page_rect.width - 45 - pymupdf.get_text_length(opts.title, fontsize=8, fontname="helv"), 30), opts.title, fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
        buf = io.BytesIO()
        doc.save(buf, garbage=3, deflate=True)
        doc.close()
        return buf.getvalue()


def _story_css() -> str:
    # The Story engine supports a subset of CSS; keep it to fonts, colours, borders and spacing.
    return """
body { font-family: sans-serif; font-size: 9.5pt; color: #1f2933; }
h1.doc { font-size: 20pt; margin-bottom: 4pt; }
h1.q { font-size: 15pt; color: #1d4ed8; margin-top: 18pt; page-break-before: always; }
h2 { font-size: 11pt; color: #1d4ed8; margin-top: 10pt; margin-bottom: 3pt; }
h3 { font-size: 9.5pt; margin-top: 7pt; margin-bottom: 2pt; }
p { margin-top: 0; margin-bottom: 4pt; }
.meta { color: #444444; font-size: 8.5pt; }
.sub { color: #555555; }
pre { font-family: monospace; font-size: 7.6pt; background-color: #f3f4f6; border: 1px solid #d1d5db; padding: 4pt; margin-bottom: 5pt; white-space: pre; }
code { font-family: monospace; font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; margin-bottom: 6pt; }
th, td { border: 1px solid #d1d5db; padding: 3pt; vertical-align: top; }
th { background-color: #e5e7eb; }
.io td { border: none; padding: 0 3pt 0 0; }
.lbl { font-size: 8pt; color: #555555; }
.badge { font-size: 7.5pt; color: #374151; }
.sev-blocker { color: #b91c1c; } .sev-major { color: #b45309; } .sev-minor { color: #854d0e; } .sev-info { color: #075985; }
.generated { color: #065f46; }
.small { font-size: 8pt; color: #555555; }
"""
