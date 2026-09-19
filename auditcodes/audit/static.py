"""The model-driven review: clarity, consistency, editorial quality, language-specific risks.

Everything here is a proposal. Patches on code are compiled and run before they are shown, and
carry the result; patches on prose are shown as diffs for a reviewer to accept.
"""

from __future__ import annotations

import re

from ..exec.harness import run_stdio_suite
from ..exec.runner import Runner
from ..llm.client import LLM, LLMError
from ..llm.prompts import STATIC_AUDIT_SYSTEM, StaticAuditOutput, static_audit_user
from ..models import IOMode, Language, Question
from .dynamic import finding, run_limits
from .patches import PatchError, apply_patch, current_value
from .report import AuditReport, Finding, FindingSource, Patch, Severity
from .rules import RULES, get_rule

_CODE_PATH = re.compile(r"^(solutions|drivers)\.([a-z]+)$")


def execution_summary(report: AuditReport) -> str:
    lines = []
    for lang, v in report.verifications.items():
        if not v.build_ok:
            lines.append(f"- {lang} editorial: DOES NOT BUILD\n  {v.build_log.strip().splitlines()[0] if v.build_log.strip() else ''}")
            continue
        lines.append(f"- {lang} editorial{' (after normalising typographic characters)' if v.normalized else ''}: {v.passed}/{v.total} tests pass; max CPU {v.max_cpu_seconds:.2f} s")
        for c in v.cases:
            if c["status"] != "passed":
                lines.append(f"  - {c['label']}: {c['status']} {c['message'] or ''}\n    expected: {_one(c['expected'])}\n    got: {_one(c['actual'])}")
    if report.validator:
        if report.validator.get("established"):
            rejected = [r for r in report.validator.get("results", []) if r["valid"] is False]
            lines.append(f"- input validator: {len(rejected)} input(s) violate the constraints" + (": " + "; ".join(f"#{r['index']} {r['reason']}" for r in rejected[:5]) if rejected else ""))
        else:
            lines.append(f"- input validator: not established ({report.validator.get('note', '')})")
    if report.driver_check:
        for lang, d in report.driver_check.items():
            lines.append(f"- {lang} driver + editorial: " + ("builds and passes " if d["build_ok"] else "DOES NOT BUILD; ") + (f"{d['passed']}/{d['total']}" if d["build_ok"] else ""))
    dyn = [f for f in report.findings if f.source is FindingSource.DYNAMIC]
    if dyn:
        lines.append("- findings already established by execution: " + ", ".join(sorted({f.rule_id for f in dyn})))
    return "\n".join(lines) or "(no execution results)"


def static_audit(q: Question, llm: LLM, runner: Runner, report: AuditReport) -> None:
    try:
        out = llm.structured(task="static_audit", system=STATIC_AUDIT_SYSTEM, user=static_audit_user(q, execution_summary(report)), schema=StaticAuditOutput, max_tokens=32000, effort="high")
    except LLMError as e:
        report.log(f"static audit: skipped ({e})")
        report.error = (report.error or "") + f"static audit failed: {e}\n"
        return
    for lf in out.findings:
        rule = get_rule(lf.rule_id)
        rule_id = rule.id if lf.rule_id in RULES else "OTHER-001"
        f = Finding(rule_id=rule_id, severity=lf.severity, source=FindingSource.STATIC, component=lf.component or rule.group,
                    title=lf.title, message=lf.message, evidence=lf.evidence, confidence=max(0.0, min(1.0, lf.confidence)))
        if lf.patch:
            f.patch = _validated_patch(q, lf.patch.path, lf.patch.new_value, runner, f)
        report.add(f)
    if out.difficulty and q.difficulty and out.difficulty.assessed != q.difficulty:
        report.add(finding("DIFF-002", "difficulty", f"Assessed as {out.difficulty.assessed.value}, labelled {q.difficulty.value}. {out.difficulty.rationale}", source=FindingSource.STATIC,
                           patch=Patch(path="difficulty", new_value=out.difficulty.assessed.value, old_value=q.difficulty.value)))
    report.log(f"static audit ({llm.model}): {len(out.findings)} finding(s); {out.summary}")


def _validated_patch(q: Question, path: str, new_value: str, runner: Runner, f: Finding) -> Patch | None:
    old = current_value(q, path)
    patch = Patch(path=path, new_value=new_value, old_value=old)
    if old is not None and old.rstrip("\n") == new_value.rstrip("\n"):
        f.message += " (The proposed value is identical to the current one; no patch.)"
        return None
    try:
        patched = apply_patch(q, patch)
    except PatchError as e:
        f.message += f" (A patch was proposed for {path!r} but could not be applied: {e})"
        return None
    m = _CODE_PATH.match(path)
    if m and q.io_mode is IOMode.STDIO and m.group(1) == "solutions":
        lang = Language(m.group(2))
        suite = run_stdio_suite(lang, new_value, [*q.samples, *q.hidden_tests], runner=runner, limits=run_limits(patched))
        patch.verified = suite.all_passed
        patch.verification_note = ("compiles and passes all tests" if suite.all_passed else ("does not compile" if not suite.build.ok else f"passes {suite.passed}/{len(suite.cases)} tests"))
        if not suite.all_passed:
            f.severity = f.severity if f.severity is not Severity.INFO else Severity.MINOR
    return patch


def _one(text) -> str:
    s = str(text or "").strip().replace("\n", " | ")
    return s if len(s) <= 200 else s[:200] + "..."
