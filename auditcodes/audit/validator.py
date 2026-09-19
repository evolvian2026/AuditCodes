"""Constraint checking of test inputs through a model-written, execution-run validator program.

The model writes the validator once per question from the input format and constraints; the
sandbox runs it on every test input. Verdicts come from running it, never from the model reading
the inputs. A validator that rejects every sample or crashes is discarded, not trusted.
"""

from __future__ import annotations

from ..exec.harness import CaseStatus, run_stdio_suite
from ..exec.runner import Limits, Runner
from ..llm.client import LLM, LLMError
from ..llm.prompts import VALIDATOR_SYSTEM, ValidatorProgram, validator_user
from ..models import Language, Question, TestCase
from .dynamic import finding
from .report import AuditReport

_LIMITS = Limits(cpu_seconds=5.0, wall_seconds=20.0)


def check_inputs(q: Question, llm: LLM, runner: Runner, report: AuditReport) -> None:
    try:
        program = llm.structured(task="validator", system=VALIDATOR_SYSTEM, user=validator_user(q), schema=ValidatorProgram, max_tokens=8000)
    except LLMError as e:
        report.validator = {"established": False, "note": str(e)}
        report.log(f"validator: skipped ({e})")
        return
    cases = [TestCase(stdin=t.stdin, stdout=None) for t in [*q.samples, *q.hidden_tests]]
    suite = run_stdio_suite(Language.PYTHON, program.python_code, cases, runner=runner, limits=_LIMITS)
    info = {"established": False, "code": program.python_code, "notes": program.notes, "results": []}
    if not suite.build.ok:
        info["note"] = "validator does not run: " + suite.build.log[:500]
        report.validator = info
        report.add(finding("VAL-001", "hidden_tests", "The generated input validator has a syntax error, so constraint checks were skipped.", evidence=suite.build.log[:500]))
        return
    verdicts: list[tuple[bool | None, str]] = []
    for c in suite.cases:
        if c.status is CaseStatus.PASSED:
            verdicts.append((True, ""))
        elif c.status is CaseStatus.RUNTIME_ERROR and "Traceback" not in c.stderr and c.stdout.strip():
            verdicts.append((False, c.stdout.strip().splitlines()[0][:200]))
        else:
            verdicts.append((None, (c.stderr or c.message).strip()[-300:]))
    info["results"] = [{"index": i, "valid": v, "reason": r} for i, (v, r) in enumerate(verdicts)]
    n_samples = len(q.samples)
    crashed = [i for i, (v, _) in enumerate(verdicts) if v is None]
    sample_rejects = [i for i, (v, _) in enumerate(verdicts[:n_samples]) if v is False]
    if crashed:
        info["note"] = f"validator crashed on {len(crashed)} input(s)"
        report.validator = info
        report.add(finding("VAL-001", "hidden_tests", f"The generated input validator crashed on {len(crashed)} input(s), so constraint checks were skipped.", evidence=verdicts[crashed[0]][1]))
        return
    if n_samples and len(sample_rejects) == n_samples:
        info["note"] = "validator rejects every sample; discarded"
        report.validator = info
        report.add(finding("VAL-001", "hidden_tests", "The generated input validator rejected every sample input, so it was discarded and constraint checks were skipped.", evidence=verdicts[0][1]))
        return
    info["established"] = True
    report.validator = info
    for i, (v, reason) in enumerate(verdicts):
        if v is not False:
            continue
        if i < n_samples:
            t = q.samples[i]
            report.add(finding("SAMP-005", f"samples.{i}", f"{t.label or 'Sample ' + str(i + 1)} input does not satisfy the stated constraints: {reason}", evidence=(t.stdin or "")[:300]))
        else:
            j = i - n_samples
            t = q.hidden_tests[j]
            report.add(finding("HIDE-005", f"hidden_tests.{j}", f"{t.label or 'Test ' + str(j + 1)} input does not satisfy the stated constraints: {reason}", evidence=(t.stdin or "")[:300]))
    rejected = sum(1 for v, _ in verdicts if v is False)
    report.log(f"validator: established; {rejected} of {len(verdicts)} inputs rejected")
