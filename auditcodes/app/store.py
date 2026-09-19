"""Filesystem job store.

One directory per uploaded document::

    <root>/<job_id>/source.pdf       the upload
                    meta.json        job metadata
                    questions.json   canonical questions (the source of truth for everything after)
                    assets/          images extracted from the PDF
                    verify/          last verification result per question/language

A JSON file per job keeps the canonical model as the only schema and makes every job inspectable
and portable; at 10-50 questions per document a database would add nothing but migrations.
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ..audit.report import AuditReport
from ..ingest import extract
from ..models import Question

_QUESTIONS = TypeAdapter(list[Question])


class JobNotFound(KeyError):
    pass


@dataclass
class JobMeta:
    id: str
    filename: str
    created_at: str
    title: str | None
    question_count: int
    warning_count: int
    document_warnings: list[str]

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "JobMeta":
        return cls(**data)


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- jobs ---------------------------------------------------------------------------------

    def create(self, filename: str, data: bytes) -> JobMeta:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        job_dir = self.root / job_id
        job_dir.mkdir()
        source = job_dir / "source.pdf"
        source.write_bytes(data)
        try:
            result = extract(source, job_dir / "assets")
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        self.save_questions(job_id, result.questions)
        meta = JobMeta(
            id=job_id,
            filename=filename,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            title=result.title,
            question_count=len(result.questions),
            warning_count=result.total_warnings,
            document_warnings=result.warnings,
        )
        (job_dir / "meta.json").write_text(json.dumps(meta.to_json(), indent=2))
        return meta

    def list(self) -> list[JobMeta]:
        metas = []
        for d in self.root.iterdir():
            f = d / "meta.json"
            if f.is_file():
                metas.append(JobMeta.from_json(json.loads(f.read_text())))
        return sorted(metas, key=lambda m: m.id, reverse=True)

    def get(self, job_id: str) -> JobMeta:
        f = self._dir(job_id) / "meta.json"
        if not f.is_file():
            raise JobNotFound(job_id)
        return JobMeta.from_json(json.loads(f.read_text()))

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self._dir(job_id), ignore_errors=True)

    # --- questions ----------------------------------------------------------------------------

    def load_questions(self, job_id: str) -> list[Question]:
        f = self._dir(job_id) / "questions.json"
        if not f.is_file():
            raise JobNotFound(job_id)
        return _QUESTIONS.validate_json(f.read_bytes())

    def save_questions(self, job_id: str, questions: list[Question]) -> None:
        f = self._dir(job_id) / "questions.json"
        f.write_bytes(_QUESTIONS.dump_json(questions, indent=2))
        self._refresh_meta(job_id, questions)

    def question(self, job_id: str, qid: str) -> Question:
        for q in self.load_questions(job_id):
            if q.id == qid:
                return q
        raise JobNotFound(f"{job_id}/{qid}")

    def update_question(self, job_id: str, updated: Question) -> None:
        questions = self.load_questions(job_id)
        for i, q in enumerate(questions):
            if q.id == updated.id:
                questions[i] = updated
                break
        else:
            raise JobNotFound(f"{job_id}/{updated.id}")
        self.save_questions(job_id, questions)

    # --- files --------------------------------------------------------------------------------

    def assets_dir(self, job_id: str) -> Path:
        return self._dir(job_id) / "assets"

    def source_pdf(self, job_id: str) -> Path:
        return self._dir(job_id) / "source.pdf"

    def questions_file(self, job_id: str) -> Path:
        return self._dir(job_id) / "questions.json"

    def save_verification(self, job_id: str, qid: str, key: str, data: dict[str, Any]) -> None:
        d = self._dir(job_id) / "verify"
        d.mkdir(exist_ok=True)
        (d / f"{qid}_{key}.json").write_text(json.dumps(data, indent=2))

    def load_verification(self, job_id: str, qid: str, key: str) -> dict[str, Any] | None:
        f = self._dir(job_id) / "verify" / f"{qid}_{key}.json"
        return json.loads(f.read_text()) if f.is_file() else None

    def save_report(self, job_id: str, report: AuditReport) -> None:
        d = self._dir(job_id) / "audit"
        d.mkdir(exist_ok=True)
        (d / f"{report.question_id}.json").write_bytes(report.model_dump_json(indent=2).encode())

    def load_report(self, job_id: str, qid: str) -> AuditReport | None:
        f = self._dir(job_id) / "audit" / f"{qid}.json"
        return AuditReport.model_validate_json(f.read_bytes()) if f.is_file() else None

    def load_reports(self, job_id: str) -> dict[str, AuditReport]:
        d = self._dir(job_id) / "audit"
        if not d.is_dir():
            return {}
        out = {}
        for f in d.glob("*.json"):
            r = AuditReport.model_validate_json(f.read_bytes())
            out[r.question_id] = r
        return out

    def _dir(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or ".." in job_id:
            raise JobNotFound(job_id)
        return self.root / job_id

    def _refresh_meta(self, job_id: str, questions: list[Question]) -> None:
        f = self._dir(job_id) / "meta.json"
        if not f.is_file():
            return
        meta = JobMeta.from_json(json.loads(f.read_text()))
        meta.question_count = len(questions)
        meta.warning_count = len(meta.document_warnings) + sum(len(q.provenance.warnings) for q in questions)
        f.write_text(json.dumps(meta.to_json(), indent=2))
