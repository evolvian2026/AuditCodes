"""Repair projection: what the question looks like once every verified patch is accepted.

The reviewer still decides, but they should see the end state: apply every execution-verified
or structural patch to a working copy, re-run the execution audit, and report what remains.
"""

from __future__ import annotations

from ..exec.runner import Runner
from ..models import Question
from . import dynamic
from .patches import apply_all
from .report import AuditReport, FindingSource


def project(q: Question, report: AuditReport, runner: Runner) -> None:
    auto = report.auto_applicable
    if not auto:
        report.projection = None
        return
    copies = [f.model_copy(deep=True) for f in auto]
    patched, applied, skipped = apply_all(q, copies)
    trial = AuditReport(question_id=q.id)
    dynamic.audit_metadata(patched, trial)
    dynamic.audit_tests(patched, trial)
    dynamic.audit_solutions(patched, runner, trial)
    remaining_static = [f for f in report.findings if f.open and f.source is FindingSource.STATIC and f.patch is None]
    report.projection = {
        "applied": [f.rule_id for f in applied],
        "skipped": [(f.rule_id, reason) for f, reason in skipped],
        "hidden_tests": len(patched.hidden_tests),
        "solutions": sorted(l.value for l in patched.solutions),
        "remaining_execution": trial.counts(),
        "remaining_execution_rules": sorted({f.rule_id for f in trial.findings}),
        "remaining_review_items": len(remaining_static),
        "verifications": {lang: {"all_passed": v.all_passed, "passed": v.passed, "total": v.total} for lang, v in trial.verifications.items()},
    }
    report.log(f"projection: after accepting {len(applied)} verified patch(es): {trial.counts()['blocker']} blocker, {trial.counts()['major']} major execution finding(s) remain; {len(remaining_static)} review item(s) without a patch")
