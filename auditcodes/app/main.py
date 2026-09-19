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

from ..exec.harness import SuiteResult, run_stdio_suite
from ..exec.languages import LANGUAGES
from ..exec.runner import Limits, LocalRunner
from ..ingest.normalize import normalize_code
from ..models import IOMode, Language, Question
from .edits import EditError, apply_edit
from .render import confidence_class, markdown
from .store import JobNotFound, JobStore
from .tasks import TaskRegistry

_HERE = Path(__file__).parent
_HTMX_CDN = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"

SECTIONS: list[tuple[str, str, str]] = [
    # (field path, label, kind)
    ("description_md", "Problem Statement", "md"),
    ("input_format_md", "Input Explanation", "md"),
    ("output_format_md", "Output Explanation", "md"),
    ("constraints", "Constraints", "lines"),
    ("sample_explanation_md", "Sample Test Case Explanation", "md"),
    ("editorial_md", "Editorial", "md"),
]
_CONF_KEY = {"description_md": "description", "input_format_md": "input_format", "output_format_md": "output_format", "sample_explanation_md": "sample_explanation", "editorial_md": "editorial", "constraints": "constraints"}


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
        rows = [
            {
                "q": q,
                "min_conf": min(q.provenance.confidence.values(), default=None),
                "languages": sorted({l.value for l in q.solutions} | {l.value for l in q.drivers}),
            }
            for q in questions
        ]
        return page(request, "job.html", job=meta, rows=rows)

    @app.post("/jobs/{job_id}/delete")
    def delete_job(job_id: str):
        get_job(job_id)
        store.delete(job_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/jobs/{job_id}/export.json")
    def export_json(job_id: str):
        get_job(job_id)
        return FileResponse(store.questions_file(job_id), media_type="application/json", filename=f"{job_id}-questions.json")

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
        )

    @app.post("/jobs/{job_id}/q/{qid}/edit", response_class=HTMLResponse)
    def edit(request: Request, job_id: str, qid: str, path: str = Form(...), value: str = Form("")):
        meta = get_job(job_id)
        q = get_question(job_id, qid)
        try:
            updated = apply_edit(q, path, value)
        except EditError as e:
            return page(request, "partials/editable.html", job=meta, q=q, **_field_ctx(q, path), error=str(e))
        store.update_question(job_id, updated)
        return page(request, "partials/editable.html", job=meta, q=updated, **_field_ctx(updated, path), saved=True)

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

    return app


def _sections(q: Question) -> list[dict[str, Any]]:
    out = []
    for path, label, kind in SECTIONS:
        ctx = _field_ctx(q, path)
        ctx["label"] = label
        out.append(ctx)
    return out


def _field_ctx(q: Question, path: str) -> dict[str, Any]:
    """Everything the editable partial needs for one field."""
    kind = "text"
    label = path
    conf = None
    raw: str
    for p, lbl, k in SECTIONS:
        if p == path:
            kind, label = k, lbl
            conf = q.provenance.confidence.get(_CONF_KEY[p])
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
