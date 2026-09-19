"""Establishing the oracle: two independent implementations that agree on every existing test.

Expected outputs for new hidden tests must not come from a single unverified program. The
editorial is one implementation; the model writes a second one from the statement alone (it never
sees the editorial), in another language. Only when both pass every existing test is the pair
trusted to produce expected outputs, and even then every generated case requires their agreement.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..exec.harness import run_stdio_suite
from ..exec.languages import LANGUAGES
from ..exec.runner import Limits, Runner
from ..llm.client import LLM, LLMError
from ..llm.prompts import INDEPENDENT_SOLUTION_SYSTEM, SolutionProgram, independent_solution_user
from ..models import Language, Question
from .dynamic import finding, run_limits
from .report import AuditReport, Patch

_PREFERENCE = [Language.CPP, Language.PYTHON, Language.JAVA, Language.C, Language.JAVASCRIPT]
_MAX_LANGUAGES = 2
_ATTEMPTS = 2


@dataclass
class Oracle:
    primary: tuple[Language, str] | None
    secondary: tuple[Language, str] | None
    note: str = ""

    @property
    def established(self) -> bool:
        return self.primary is not None and self.secondary is not None


def cross_check_limits(q: Question) -> Limits:
    """The second implementation only needs to be correct, so it gets a generous time budget."""
    base = run_limits(q)
    return Limits(cpu_seconds=3 * base.cpu_seconds + 2, wall_seconds=3 * base.wall_seconds + 5, memory_bytes=base.memory_bytes)


def establish(q: Question, llm: LLM, runner: Runner, report: AuditReport, verified_code: dict[Language, str]) -> Oracle:
    primary = _pick_primary(q, report, verified_code)
    if primary is None:
        note = "the editorial does not pass every existing test, so it cannot serve as an oracle"
        report.oracle = {"established": False, "note": note}
        report.add(finding("GEN-005", "hidden_tests", f"Hidden-test generation skipped: {note}."))
        report.log("oracle: " + note)
        return Oracle(None, None, note)
    cases = [*q.samples, *q.hidden_tests]
    attempts: list[dict] = []
    candidates = [l for l in _PREFERENCE if l not in q.solutions and LANGUAGES[l].available()][:_MAX_LANGUAGES]
    for lang in candidates:
        previous: tuple[str, str] | None = None
        for attempt in range(_ATTEMPTS):
            try:
                out = llm.structured(task="independent_solution", system=INDEPENDENT_SOLUTION_SYSTEM, user=independent_solution_user(q, lang, previous), schema=SolutionProgram, max_tokens=16000)
            except LLMError as e:
                attempts.append({"language": lang.value, "attempt": attempt + 1, "result": f"model error: {e}"})
                break
            suite = run_stdio_suite(lang, out.code, cases, runner=runner, limits=cross_check_limits(q))
            if suite.all_passed:
                attempts.append({"language": lang.value, "attempt": attempt + 1, "result": "passes all existing tests"})
                report.oracle = {"established": True, "primary": primary[0].value, "secondary": lang.value, "approach": out.approach, "attempts": attempts}
                report.add(finding(
                    "GEN-001", f"solutions.{lang.value}",
                    f"An independent {LANGUAGES[lang].display_name} solution written from the statement alone passes all {len(cases)} existing tests, so the editorial and it agree everywhere the question already covers. Accepting adds it as a second reference solution. Approach: {out.approach}",
                    patch=Patch(path=f"solutions.{lang.value}", new_value=out.code, old_value="", verified=True, verification_note=f"passes {len(cases)}/{len(cases)} existing tests"),
                ))
                report.log(f"oracle: {primary[0].value} editorial + independent {lang.value} solution ({attempt + 1} attempt(s))")
                return Oracle(primary, (lang, out.code))
            failure = _describe_failure(suite)
            attempts.append({"language": lang.value, "attempt": attempt + 1, "result": failure[:300]})
            previous = (out.code, failure)
    if not any("model error" not in a["result"] for a in attempts):
        note = "no independent implementation could be obtained from the model" + (f" ({attempts[-1]['result']})" if attempts else " (no candidate language available)")
        report.oracle = {"established": False, "primary": primary[0].value, "note": note, "attempts": attempts}
        report.add(finding("GEN-005", "hidden_tests", f"Hidden-test generation skipped: {note}."))
        report.log("oracle: " + note)
        return Oracle(primary, None, note)
    note = "no independent implementation agreed with the editorial on every existing test"
    report.oracle = {"established": False, "primary": primary[0].value, "note": note, "attempts": attempts}
    report.add(finding("GEN-003", "hidden_tests", f"{note.capitalize()} after {len(attempts)} attempt(s) in {', '.join(sorted({a['language'] for a in attempts}))}. Either the problem is harder to implement from the statement than it looks (a clarity signal), or the editorial's behaviour is not what the statement describes. No hidden tests were generated.",
                       evidence="\n".join(f"{a['language']} attempt {a['attempt']}: {a['result']}" for a in attempts)))
    report.log("oracle: " + note)
    return Oracle(primary, None, note)


def _pick_primary(q: Question, report: AuditReport, verified_code: dict[Language, str]) -> tuple[Language, str] | None:
    for lang, code in verified_code.items():
        v = report.verifications.get(lang.value)
        if v and v.all_passed and v.total > 0:
            return lang, code
    return None


def _describe_failure(suite) -> str:
    if not suite.build.ok:
        return "does not compile:\n" + "\n".join(suite.build.log.strip().splitlines()[:8])
    for c in suite.cases:
        if not c.passed:
            exp = (c.expected or "").strip()[:300]
            got = (c.actual or "").strip()[:300]
            return f"{c.status.value} on test #{c.index + 1}{(': ' + c.message) if c.message else ''}\nexpected:\n{exp}\ngot:\n{got}" + (f"\nstderr:\n{c.stderr.strip()[-400:]}" if c.stderr.strip() else "")
    return "unknown failure"
