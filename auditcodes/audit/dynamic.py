"""Execution-based checks: the only source of pass/fail verdicts in the audit."""

from __future__ import annotations

from ..exec.harness import CaseStatus, SuiteResult, run_stdio_suite
from ..exec.languages import DEFAULT_COMPILE_LIMITS, DEFAULT_RUN_LIMITS
from ..exec.runner import Limits, Runner
from ..ingest.normalize import normalize_code
from ..models import Difficulty, IOMode, Language, Question, TestCase
from .patches import fingerprint
from .report import AuditReport, Finding, FindingSource, Patch, Severity, VerificationSummary
from .rules import get_rule

_WORD_DIFFICULTY = {"easy": Difficulty.EASY, "moderate": Difficulty.MEDIUM, "medium": Difficulty.MEDIUM, "hard": Difficulty.HARD}


def finding(rule_id: str, component: str, message: str, *, severity: Severity | None = None, evidence: str | None = None, patch: Patch | None = None, title: str | None = None, source: FindingSource = FindingSource.DYNAMIC) -> Finding:
    rule = get_rule(rule_id)
    return Finding(rule_id=rule.id, severity=severity or rule.severity, source=source, component=component, title=title or rule.title, message=message, evidence=evidence, patch=patch)


def run_limits(q: Question) -> Limits:
    tl = q.time_limit_seconds or DEFAULT_RUN_LIMITS.cpu_seconds
    return Limits(cpu_seconds=tl, wall_seconds=max(10.0, 5 * tl), memory_bytes=DEFAULT_RUN_LIMITS.memory_bytes)


def summarize(suite: SuiteResult, q: Question, normalized: bool) -> VerificationSummary:
    labels = [c.label or f"Sample {i + 1}" for i, c in enumerate(q.samples)] + [c.label or f"Test {i + 1}" for i, c in enumerate(q.hidden_tests)]
    return VerificationSummary(
        language=suite.language.value,
        build_ok=suite.build.ok,
        build_log=suite.build.log[:4000],
        normalized=normalized,
        passed=suite.passed,
        total=len(suite.cases),
        all_passed=suite.all_passed,
        max_cpu_seconds=suite.max_cpu_seconds,
        cases=[
            {"index": c.index, "label": labels[c.index] if c.index < len(labels) else str(c.index), "status": c.status.value, "message": c.message,
             "expected": (c.expected or "")[:2000] if isinstance(c.expected, str) else c.expected, "actual": (c.actual or "")[:2000] if isinstance(c.actual, str) else c.actual,
             "stderr": c.stderr[:2000], "cpu_seconds": c.cpu_seconds}
            for c in suite.cases
        ],
    )


def audit_solutions(q: Question, runner: Runner, report: AuditReport) -> dict[Language, str]:
    """Build and run every editorial; returns the code actually verified per language (normalised when needed)."""
    verified_code: dict[Language, str] = {}
    if q.io_mode is not IOMode.STDIO:
        report.log("solutions: function-mode verification is not wired into the audit yet")
        return verified_code
    cases: list[TestCase] = [*q.samples, *q.hidden_tests]
    limits = run_limits(q)
    for lang, code in q.solutions.items():
        component = f"solutions.{lang.value}"
        suite = run_stdio_suite(lang, code, cases, runner=runner, limits=limits, compile_limits=DEFAULT_COMPILE_LIMITS)
        normalized = False
        if not suite.build.ok:
            norm = normalize_code(code)
            if norm.changed:
                retry = run_stdio_suite(lang, norm.text, cases, runner=runner, limits=limits, compile_limits=DEFAULT_COMPILE_LIMITS)
                if retry.build.ok:
                    report.add(finding(
                        "SOL-002", component,
                        f"The {lang.value} editorial does not compile as written because of {norm.describe()}. After normalising these characters it compiles" + (" and passes every test." if retry.all_passed else f" but passes only {retry.passed}/{len(retry.cases)} tests."),
                        evidence=_first_lines(suite.build.log, 6),
                        patch=Patch(path=component, new_value=norm.text, old_value=code, verified=True, verification_note="compiles" + (", all tests pass" if retry.all_passed else f", {retry.passed}/{len(retry.cases)} tests pass")),
                    ))
                    suite, normalized = retry, True
                    code = norm.text
            if not suite.build.ok:
                report.add(finding("SOL-001", component, f"The {lang.value} editorial fails to build.", evidence=_first_lines(suite.build.log, 12)))
                report.verifications[lang.value] = summarize(suite, q, normalized)
                continue
        verified_code[lang] = code
        report.verifications[lang.value] = summarize(suite, q, normalized)
        n_samples = len(q.samples)
        for c in suite.cases:
            if c.passed:
                continue
            is_sample = c.index < n_samples
            label = report.verifications[lang.value].cases[c.index]["label"]
            case_path = f"samples.{c.index}" if is_sample else f"hidden_tests.{c.index - n_samples}"
            if c.status is CaseStatus.FAILED:
                rule = "SOL-003" if is_sample else "SOL-004"
                report.add(finding(rule, case_path, f"The {lang.value} editorial's output on {label} differs from the expected output. Either the expected output or the editorial is wrong; the static audit adjudicates.",
                                   evidence=f"expected:\n{_clip(c.expected)}\n\ngot:\n{_clip(c.actual)}"))
            elif c.status is CaseStatus.TIMEOUT:
                report.add(finding("SOL-006", case_path, f"The {lang.value} editorial exceeded the time limit ({limits.cpu_seconds:g} s) on {label}: {c.message}.", evidence=_clip(c.stderr)))
            elif c.status is CaseStatus.MEMORY_LIMIT:
                report.add(finding("SOL-008", case_path, f"The {lang.value} editorial ran out of memory on {label}.", evidence=_clip(c.stderr)))
            else:
                report.add(finding("SOL-005", case_path, f"The {lang.value} editorial crashed on {label}: {c.message}.", evidence=_clip(c.stderr)))
        if suite.cases and q.time_limit_seconds and suite.max_cpu_seconds > 0.5 * q.time_limit_seconds and not any(c.status is CaseStatus.TIMEOUT for c in suite.cases):
            report.add(finding("SOL-007", component, f"The {lang.value} editorial needs up to {suite.max_cpu_seconds:.2f} s of CPU against a {q.time_limit_seconds:g} s limit. Solutions in slower languages, or slightly less efficient correct solutions, may fail."))
        report.log(f"solutions.{lang.value}: {suite.summary()}" + (" (normalised)" if normalized else ""))
    return verified_code


def audit_tests(q: Question, report: AuditReport) -> None:
    if not q.samples:
        report.add(finding("SAMP-003", "samples", "The question has no sample test case."))
    seen: dict[str, int] = {}
    for i, t in enumerate(q.samples):
        key = _norm(t.stdin)
        if key in seen:
            report.add(finding("SAMP-004", f"samples.{i}", f"{t.label or 'Sample ' + str(i + 1)} has the same input as {q.samples[seen[key]].label or 'Sample ' + str(seen[key] + 1)}.",
                               patch=Patch(op="remove", path=f"samples.{i}", item_fingerprint=fingerprint(t), verified=None)))
        else:
            seen[key] = i
    sample_inputs = {_norm(t.stdin): i for i, t in enumerate(q.samples)}
    hidden_seen: dict[str, int] = {}
    for i, t in enumerate(q.hidden_tests):
        key = _norm(t.stdin)
        label = t.label or f"Test {i + 1}"
        if key in hidden_seen:
            other = q.hidden_tests[hidden_seen[key]]
            report.add(finding("HIDE-001", f"hidden_tests.{i}", f"{label} has exactly the same input as {other.label or 'Test ' + str(hidden_seen[key] + 1)}; it adds no coverage but awards {t.points or 'its'} points a second time.",
                               evidence=_clip(t.stdin, 200), patch=Patch(op="remove", path=f"hidden_tests.{i}", item_fingerprint=fingerprint(t), verified=None)))
            continue
        hidden_seen[key] = i
        if key in sample_inputs:
            s = q.samples[sample_inputs[key]]
            report.add(finding("HIDE-002", f"hidden_tests.{i}", f"{label} is identical to visible {s.label or 'Sample ' + str(sample_inputs[key] + 1)}, so it does not test anything the candidate has not already seen.",
                               evidence=_clip(t.stdin, 200), patch=Patch(op="remove", path=f"hidden_tests.{i}", item_fingerprint=fingerprint(t), verified=None)))
        if t.difficulty is None or t.points is None:
            report.add(finding("HIDE-006", f"hidden_tests.{i}", f"{label} is missing {'a level' if t.difficulty is None else 'points'}."))
    n = len(q.hidden_tests)
    if n < 10:
        report.add(finding("HIDE-003", "hidden_tests", f"There are {n} hidden test(s); the target is 10-50 depending on difficulty. Test generation (a later stage) can add verified cases."))
    elif n > 50:
        report.add(finding("HIDE-004", "hidden_tests", f"There are {n} hidden tests, more than the 50 maximum."))
    report.log(f"tests: {len(q.samples)} samples, {n} hidden")


def audit_metadata(q: Question, report: AuditReport) -> None:
    word = q.metadata.get("Difficulty")
    if word and q.lod is not None and q.difficulty is not None and _WORD_DIFFICULTY.get(word.lower()) != q.difficulty:
        report.add(finding("DIFF-001", "difficulty", f"The title says {word!r} but LOD {q.lod} means {q.difficulty.value}."))
    if q.time_limit_seconds is None:
        report.add(finding("META-001", "time_limit_seconds", "No time limit is stated; verification used the default."))
    if not q.area:
        report.add(finding("META-002", "area", "No subject area is recorded."))


def _norm(text: str | None) -> str:
    return "\n".join(ln.rstrip() for ln in (text or "").replace("\r\n", "\n").strip("\n").split("\n"))


def _clip(text: str | None, limit: int = 600) -> str:
    text = (text or "").rstrip("\n")
    return text if len(text) <= limit else text[:limit] + "\n..."


def _first_lines(text: str, n: int) -> str:
    return "\n".join(text.strip().splitlines()[:n])
