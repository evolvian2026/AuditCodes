"""Audit orchestration for one question."""

from __future__ import annotations

import time
import traceback

from ..exec.runner import LocalRunner, Runner
from ..llm.client import LLM
from ..models import Question
from . import complete, driver, dynamic, oracle, repair, static, testgen, validator
from .report import AuditReport


def generated_cases(report: AuditReport) -> list:
    """Test cases produced by the generation stage (still a patch until accepted)."""
    from ..models import TestCase

    for f in report.findings:
        if f.patch and f.patch.op == "append" and f.patch.items:
            return [TestCase.model_validate(i) for i in f.patch.items]
    return []


def run_audit(q: Question, *, runner: Runner | None = None, llm: LLM | None = None, progress=None) -> AuditReport:
    """Run every stage; a failing stage is recorded and the others still run."""
    runner = runner or LocalRunner()
    report = AuditReport(question_id=q.id, llm_model=llm.model if llm else None)

    def stage(name: str, fn) -> None:
        if progress:
            progress(name)
        try:
            fn()
        except Exception:
            report.error = (report.error or "") + f"{name} failed:\n{traceback.format_exc()}\n"
            report.log(f"{name}: failed (see error)")

    verified: dict = {}

    def solutions() -> None:
        verified.update(dynamic.audit_solutions(q, runner, report))

    stage("metadata", lambda: dynamic.audit_metadata(q, report))
    stage("tests", lambda: dynamic.audit_tests(q, report))
    stage("solutions", solutions)
    if llm is None:
        report.log("model stages skipped: no ANTHROPIC_API_KEY configured (validator, driver check, static audit, oracle, test generation)")
    else:
        stage("validator", lambda: validator.check_inputs(q, llm, runner, report))
        stage("driver", lambda: driver.check_drivers(q, llm, runner, report, verified))
        stage("static", lambda: static.static_audit(q, llm, runner, report))
        holder: dict = {}

        def establish() -> None:
            holder["oracle"] = oracle.establish(q, llm, runner, report, verified)

        stage("oracle", establish)
        if holder.get("oracle") is not None:
            stage("generation", lambda: testgen.generate(q, llm, runner, report, holder["oracle"]))
            stage("languages", lambda: complete.complete_languages(q, llm, runner, report, holder["oracle"], generated_cases(report)))
    stage("projection", lambda: repair.project(q, report, runner))
    report.status = "failed" if report.error and not report.findings and not report.verifications else "done"
    report.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
    return report
