"""ZIP bundle: canonical JSON, audit reports, assets and the rendered documents."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from pydantic import TypeAdapter

from ..audit.report import AuditReport
from ..models import Question
from .docx import export_docx
from .html import ExportOptions, export_html
from .pdf import export_pdf


def export_zip(questions: list[Question], reports: dict[str, AuditReport] | None = None, options: ExportOptions | None = None, assets_dir: Path | None = None, source_pdf: Path | None = None) -> bytes:
    options = options or ExportOptions()
    reports = reports or {}
    if options.question_ids:
        questions = [q for q in questions if q.id in options.question_ids]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("questions.json", TypeAdapter(list[Question]).dump_json(questions, indent=2))
        for qid, r in reports.items():
            if any(q.id == qid for q in questions):
                z.writestr(f"audit/{qid}.json", r.model_dump_json(indent=2))
        if assets_dir and assets_dir.is_dir():
            for f in sorted(assets_dir.iterdir()):
                if f.is_file():
                    z.write(f, f"assets/{f.name}")
        rel = ExportOptions(**{**options.__dict__, "image_mode": "relative"})
        z.writestr("audited.html", export_html(questions, reports, rel, assets_dir).replace('<img src="', '<img src="assets/'))
        z.writestr("audited.pdf", export_pdf(questions, reports, options, assets_dir))
        z.writestr("audited.docx", export_docx(questions, reports, options, assets_dir))
        if source_pdf and source_pdf.is_file():
            z.write(source_pdf, "source.pdf")
        z.writestr("README.txt", (
            "AuditCodes export\n\n"
            "questions.json  canonical questions (schema: auditcodes.models.Question) with every accepted change applied\n"
            "audit/*.json    audit report per question (findings, verification results, decisions)\n"
            "audited.pdf     formatted question bank" + (" with audit appendix" if options.include_audit else "") + "\n"
            "audited.docx    the same as a Word document\n"
            "audited.html    the same as HTML (images in assets/)\n"
            "assets/         figures extracted from the source document\n"
        ))
    return buf.getvalue()
