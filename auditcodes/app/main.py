"""FastAPI routes for the review app (server-rendered, HTMX for partial updates)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..audit import run_audit
from ..audit.patches import PatchError, apply_all, apply_patch, shift_indices_after_removal, unified_diff
from ..audit.report import AuditReport, FindingStatus
from ..audit.rules import get_rule
from ..exec.harness import SuiteResult, run_stdio_suite
from ..export import ExportOptions, export_docx, export_html, export_pdf, export_zip, pdf_backend
from ..exec.languages import LANGUAGES
from ..exec.runner import Limits, LocalRunner
from ..ingest.normalize import normalize_code
from ..llm.client import default_llm
from ..models import IOMode, Language, Question
from ..edits import EditError, apply_edit
from ..fields import SECTIONS, field_ctx
from ..render import confidence_class, diff_html, markdown
from .store import JobNotFound, JobStore
from .tasks import TaskRegistry

_HERE = Path(__file__).parent
_HTMX_CDN = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"

def create_app(data_dir: str | Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("AUDITCODES_DATA", "data/jobs"))
    store = JobStore(data_dir)
    tasks = TaskRegistry()
    runner = LocalRunner()
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    templates.env.filters["md"] = markdown
    templates.env.filters["conf"] = confidence_class
    templates.env.globals["htmx_src"] = "/static/htmx.min.js" if (_HERE / "static" / "htmx.min.js").exists() else _HTMX_CDN
    templates.env.globals["languages"] = [(l.value, LANGUAGES[l].display_name) for l in Language]
    templates.env.filters["diff"] = lambda old, new, path: diff_html(unified_diff(old, new, path))
    templates.env.globals["rule"] = get_rule
    llm = default_llm()
    templates.env.globals["llm_model"] = llm.model if llm else None
    templates.env.globals["pdf_backend"] = pdf_backend()

    app = FastAPI(title="AuditCodes")
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.state.store = store
    app.state.tasks = tasks

    def page(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    def get_job(job_id: str):
        try:
            return store.get(job_id)
        except JobNotFound:
            raise HTTPException(404, "job not found")

    def get_question(job_id: str, qid: str) -> Question:
        try:
            return store.question(job_id, qid)
        except JobNotFound:
            raise HTTPException(404, "question not found")

    # --- jobs ---------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html", jobs=store.list(), network_isolated=runner.network_isolated)

    @app.post("/jobs")
    async def upload(request: Request, file: UploadFile):
        data = await file.read()
        if not data or not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "upload a PDF file")
        try:
            meta = store.create(file.filename or "upload.pdf", data)
        except Exception as e:  # extraction failure is a user-visible outcome, not a 500
            return page(request, "index.html", jobs=store.list(), error=f"could not extract questions: {e}", network_isolated=runner.network_isolated)
        return RedirectResponse(f"/jobs/{meta.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job(request: Request, job_id: str):
        meta = get_job(job_id)
        questions = store.load_questions(job_id)
        reports = store.load_reports(job_id)
        rows = [
            {
                "q": q,
                "min_conf": min(q.provenance.confidence.values(), default=None),
                "languages": sorted({l.value for l in q.solutions} | {l.value for l in q.drivers}),
                "report": reports.get(q.id),
            }
            for q in questions
        ]
        return page(request, "job.html", job=meta, rows=rows, task=None)

    @app.post("/jobs/{job_id}/delete")
    def delete_job(job_id: str):
        get_job(job_id)
        store.delete(job_id)
        return RedirectResponse("/", status_code=303)

    def _export_options(job_id: str, hidden: str, drivers: str, solutions: str, audit: str, q: str | None) -> ExportOptions:
        meta = get_job(job_id)
        return ExportOptions(
            title=meta.title or Path(meta.filename).stem.replace("_", " ").title(),
            source_name=meta.filename,
            include_hidden=hidden != "0",
            include_drivers=drivers != "0",
            include_solutions=solutions != "0",
            include_audit=audit != "0",
            question_ids=[q] if q else None,
        )

    @app.get("/jobs/{job_id}/export.{fmt}")
    def export_document(job_id: str, fmt: str, hidden: str = "1", drivers: str = "1", solutions: str = "1", audit: str = "1", q: str | None = None):
        if fmt == "json":
            return FileResponse(store.questions_file(job_id), media_type="application/json", filename=f"{job_id}-questions.json")
        if fmt not in ("pdf", "docx", "html", "zip"):
            raise HTTPException(404)
        options = _export_options(job_id, hidden, drivers, solutions, audit, q)
        questions = store.load_questions(job_id)
        reports = store.load_reports(job_id)
        assets = store.assets_dir(job_id)
        stem = f"{job_id}-audited" + (f"-{q}" if q else "")
        if fmt == "html":
            return HTMLResponse(export_html(questions, reports, options, assets))
        if fmt == "pdf":
            data, media = export_pdf(questions, reports, options, assets), "application/pdf"
        elif fmt == "docx":
            data, media = export_docx(questions, reports, options, assets), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            data, media = export_zip(questions, reports, options, assets, store.source_pdf(job_id)), "application/zip"
        from fastapi.responses import Response

        return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'})

    @app.get("/jobs/{job_id}/source.pdf")
    def source_pdf(job_id: str):
        get_job(job_id)
        return FileResponse(store.source_pdf(job_id), media_type="application/pdf")

    @app.get("/jobs/{job_id}/assets/{name}")
    def asset(job_id: str, name: str):
        get_job(job_id)
        path = store.assets_dir(job_id) / Path(name).name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # --- questions ----------------------------------------------------------------------------

    @app.get("/jobs/{job_id}/q/{qid}", response_class=HTMLResponse)
    def question(request: Request, job_id: str, qid: str):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        questions = store.load_questions(job_id)
        ids = [x.id for x in questions]
        i = ids.index(qid)
        verifications = {
            lang.value: store.load_verification(job_id, qid, lang.value) for lang in q.solutions
        }
        return page(
            request,
            "question.html",
            job=meta,
            q=q,
            sections=_sections(q),
            prev_id=ids[i - 1] if i > 0 else None,
            next_id=ids[i + 1] if i + 1 < len(ids) else None,
            position=(i + 1, len(ids)),
            verifications=verifications,
            report=store.load_report(job_id, qid),
            task=None,
        )

    @app.post("/jobs/{job_id}/q/{qid}/edit", response_class=HTMLResponse)
    def edit(request: Request, job_id: str, qid: str, path: str = Form(...), value: str = Form("")):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        try:
            updated = apply_edit(q, path, value)
        except EditError as e:
            return page(request, "partials/editable.html", job=meta, q=q, **field_ctx(q, path), error=str(e))
        store.update_question(job_id, updated)
        return page(request, "partials/editable.html", job=meta, q=updated, **field_ctx(updated, path), saved=True)

    # --- verification -------------------------------------------------------------------------

    @app.post("/jobs/{job_id}/q/{qid}/verify", response_class=HTMLResponse)
    def verify(request: Request, job_id: str, qid: str, language: str = Form(...), normalize: str = Form("")):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        try:
            lang = Language(language)
        except ValueError:
            raise HTTPException(400, "unknown language")
        if lang not in q.solutions:
            raise HTTPException(400, "no solution in that language")
        if q.io_mode is not IOMode.STDIO:
            raise HTTPException(400, "only stdio questions can be verified here yet")
        code = q.solutions[lang]
        normalization = normalize_code(code) if normalize else None
        if normalization is not None and normalization.changed:
            code = normalization.text
        cases = [*q.samples, *q.hidden_tests]
        limits = Limits(cpu_seconds=q.time_limit_seconds or 2.0, wall_seconds=max(10.0, 5 * (q.time_limit_seconds or 2.0)))

        def work() -> dict[str, Any]:
            suite = run_stdio_suite(lang, code, cases, runner=runner, limits=limits)
            data = _suite_to_json(suite, q, normalization.describe() if normalization and normalization.changed else None)
            store.save_verification(job_id, qid, lang.value, data)
            return data

        task = tasks.submit("verify", work)
        return page(request, "partials/verify.html", job=meta, q=q, language=lang.value, task=task, result=None)

    @app.get("/jobs/{job_id}/q/{qid}/verify/{task_id}", response_class=HTMLResponse)
    def verify_status(request: Request, job_id: str, qid: str, task_id: str, language: str = ""):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return page(request, "partials/verify.html", job=meta, q=q, language=language, task=task, result=task.result if task.status == "done" else None)

    # --- audit --------------------------------------------------------------------------------

    def audit_one(job_id: str, qid: str) -> AuditReport:
        q = store.question(job_id, qid)
        report = run_audit(q, runner=runner, llm=llm)
        store.save_report(job_id, report)
        return report

    @app.post("/jobs/{job_id}/q/{qid}/audit", response_class=HTMLResponse)
    def audit_question(request: Request, job_id: str, qid: str):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        task = tasks.submit("audit", lambda: audit_one(job_id, qid))
        return page(request, "partials/audit.html", job=meta, q=q, task=task, report=None)

    @app.get("/jobs/{job_id}/q/{qid}/audit/{task_id}", response_class=HTMLResponse)
    def audit_status(request: Request, job_id: str, qid: str, task_id: str):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        if task.finished:
            return page(request, "partials/audit.html", job=meta, q=q, task=None if task.status == "done" else task, report=store.load_report(job_id, qid))
        return page(request, "partials/audit.html", job=meta, q=q, task=task, report=None)

    @app.post("/jobs/{job_id}/audit", response_class=HTMLResponse)
    def audit_all(request: Request, job_id: str):
        meta = get_job(job_id)
        ids = [q.id for q in store.load_questions(job_id)]
        progress = {"done": 0, "total": len(ids), "current": None}

        def work():
            for qid in ids:
                progress["current"] = qid
                audit_one(job_id, qid)
                progress["done"] += 1
            return progress

        task = tasks.submit("audit_all", work)
        task.result = progress  # visible while running
        return page(request, "partials/audit_all.html", job=meta, task=task)

    @app.get("/jobs/{job_id}/audit/{task_id}", response_class=HTMLResponse)
    def audit_all_status(request: Request, job_id: str, task_id: str):
        meta = get_job(job_id)
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return page(request, "partials/audit_all.html", job=meta, task=task)

    @app.post("/jobs/{job_id}/q/{qid}/findings/accept-verified", response_class=HTMLResponse)
    def accept_verified(request: Request, job_id: str, qid: str):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        report = store.load_report(job_id, qid)
        if report is None:
            raise HTTPException(404, "no audit report")
        auto = report.auto_applicable
        updated, applied, skipped = apply_all(q, auto)
        for f in applied:
            f.status, f.applied = FindingStatus.ACCEPTED, True
        for f, reason in skipped:
            f.resolution_note = f"could not apply: {reason}"
        if applied:
            store.update_question(job_id, updated)
        report.projection = None  # stale once patches are applied; a re-run recomputes it
        store.save_report(job_id, report)
        resp = page(request, "partials/audit.html", job=meta, q=updated, task=None, report=report,
                    error=("; ".join(f"{f.rule_id}: {reason}" for f, reason in skipped) or None))
        if applied:
            resp.headers["HX-Refresh"] = "true"
        return resp

    @app.post("/jobs/{job_id}/q/{qid}/findings/{fid}/{action}", response_class=HTMLResponse)
    def finding_action(request: Request, job_id: str, qid: str, fid: str, action: str):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        report = store.load_report(job_id, qid)
        f = report.get(fid) if report else None
        if f is None:
            raise HTTPException(404, "finding not found")
        error = None
        if action == "accept":
            if f.patch is None:
                error = "this finding has no patch to apply"
            else:
                try:
                    updated = apply_patch(q, f.patch)
                    if f.patch.op == "remove":
                        group, idx = f.patch.path.rsplit(".", 1)
                        shift_indices_after_removal(report.findings, group, int(idx))
                    store.update_question(job_id, updated)
                    q = updated
                    f.status, f.applied = FindingStatus.ACCEPTED, True
                    report.projection = None
                except PatchError as e:
                    error = str(e)
        elif action == "reject":
            f.status = FindingStatus.REJECTED
        elif action == "waive":
            f.status = FindingStatus.WAIVED
        elif action == "reopen":
            if f.applied:
                error = "an applied patch cannot be reopened; edit the field instead"
            else:
                f.status = FindingStatus.OPEN
        else:
            raise HTTPException(400, "unknown action")
        store.save_report(job_id, report)
        resp = page(request, "partials/audit.html", job=meta, q=q, task=None, report=report, error=error)
        if action == "accept" and not error:
            resp.headers["HX-Refresh"] = "true"  # the field content changed; reload the page
        return resp

    return app


def _sections(q: Question) -> list[dict[str, Any]]:
    out = []
    for path, label, kind in SECTIONS:
        ctx = field_ctx(q, path)
        ctx["label"] = label
        out.append(ctx)
    return out


def _suite_to_json(suite: SuiteResult, q: Question, normalization: str | None) -> dict[str, Any]:
    labels = [c.label or f"Sample {i + 1}" for i, c in enumerate(q.samples)] + [c.label or f"Hidden {i + 1}" for i, c in enumerate(q.hidden_tests)]
    return {
        "language": suite.language.value,
        "build_ok": suite.build.ok,
        "build_log": suite.build.log,
        "normalization": normalization,
        "passed": suite.passed,
        "total": len(suite.cases),
        "all_passed": suite.all_passed,
        "max_cpu_seconds": suite.max_cpu_seconds,
        "cases": [
            {**dataclasses.asdict(c), "status": c.status.value, "label": labels[c.index] if c.index < len(labels) else str(c.index + 1)}
            for c in suite.cases
        ],
    }
