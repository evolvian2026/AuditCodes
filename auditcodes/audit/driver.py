"""Driver-code verification: the scaffold a candidate sees must work with the editorial's logic.

The model splices the editorial's implementation into the driver's stub (an editing task, not a
judgement); the sandbox then compiles and runs the result on every test. If that fails, the
driver's scaffolding disagrees with the editorial or the tests.
"""

from __future__ import annotations

from ..exec.harness import run_stdio_suite
from ..llm.client import LLM, LLMError
from ..llm.prompts import SPLICE_SYSTEM, SplicedProgram, splice_user
from ..models import IOMode, Language, Question
from ..exec.runner import Runner
from .dynamic import finding, run_limits, summarize
from .report import AuditReport, FindingSource, Severity


def check_drivers(q: Question, llm: LLM, runner: Runner, report: AuditReport, verified_code: dict[Language, str]) -> None:
    if q.io_mode is not IOMode.STDIO:
        return
    results: dict[str, dict] = {}
    for lang, driver in q.drivers.items():
        component = f"drivers.{lang.value}"
        if lang not in q.solutions:
            report.add(finding("DRV-004", component, f"There is no {lang.value} editorial, so the {lang.value} driver could not be exercised."))
            continue
        if lang not in verified_code:
            report.add(finding("DRV-004", component, f"The {lang.value} editorial does not build, so the {lang.value} driver could not be exercised against it."))
            continue
        q_verified = q.model_copy(update={"solutions": {**q.solutions, lang: verified_code[lang]}})
        try:
            spliced = llm.structured(task="splice", system=SPLICE_SYSTEM, user=splice_user(q_verified, lang), schema=SplicedProgram, max_tokens=16000)
        except LLMError as e:
            report.log(f"driver.{lang.value}: splice skipped ({e})")
            continue
        suite = run_stdio_suite(lang, spliced.code, [*q.samples, *q.hidden_tests], runner=runner, limits=run_limits(q))
        summary = summarize(suite, q, False)
        results[lang.value] = {"code": spliced.code, "issues": spliced.issues, "build_ok": suite.build.ok, "passed": suite.passed, "total": len(suite.cases)}
        if not suite.build.ok:
            report.add(finding("DRV-001", component, f"The {lang.value} driver with the editorial's logic filled into its stub does not compile. The scaffold and the editorial disagree on structure (signature, types, imports or class layout).",
                               evidence="\n".join(suite.build.log.strip().splitlines()[:10])))
        elif not suite.all_passed:
            failed = [c for c in summary.cases if c["status"] != "passed"]
            report.add(finding("DRV-002", component, f"The {lang.value} driver with the editorial's logic passes only {suite.passed}/{len(suite.cases)} tests, although the editorial itself passes. The driver's input reading or output printing does not match the tests.",
                               evidence=f"{failed[0]['label']}: expected\n{failed[0]['expected']}\ngot\n{failed[0]['actual']}"))
        for issue in spliced.issues:
            report.add(finding("DRV-003", component, issue, source=FindingSource.STATIC, severity=Severity.MINOR))
        report.log(f"driver.{lang.value}: spliced program {suite.summary()}")
    report.driver_check = results or None
