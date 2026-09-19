"""Language completion: a verified solution — and a driver scaffold — in every supported language.

The model ports the verified editorial; the sandbox decides. A ported solution is offered only
when it passes every test, existing and generated, within its language's time allowance. A
scaffold is offered only when the program built from it passes the same tests and the scaffold's
fixed lines are exactly the lines of that program (so the stub is the only thing a candidate
must write).
"""

from __future__ import annotations

import os
import re

from ..exec.harness import CaseStatus, SuiteResult, run_stdio_suite
from ..exec.languages import LANGUAGES
from ..exec.runner import Runner
from ..llm.client import LLM, LLMError
from ..llm.prompts import DRIVER_SYSTEM, PORT_SOLUTION_SYSTEM, DriverScaffold, SolutionProgram, driver_user, port_solution_user
from ..models import IOMode, Language, Question, TestCase
from .dynamic import finding, run_limits
from .oracle import Oracle, _describe_failure
from .report import AuditReport, Patch

_ATTEMPTS = 2
_STUB_MARKER = re.compile(r"write\s+your\s+code\s+here", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"^(pass|\.\.\.|return\s*[^;]{0,12};?|return\s+(None|null|0|\"\"|''|nullptr);?)$")


def generate_drivers_enabled() -> bool:
    return os.environ.get("AUDITCODES_GENERATE_DRIVERS", "1") not in ("0", "false", "no")


def complete_languages(q: Question, llm: LLM, runner: Runner, report: AuditReport, oracle: Oracle, extra_cases: list[TestCase], drivers: bool | None = None) -> None:
    if q.io_mode is not IOMode.STDIO or oracle.primary is None:
        return
    drivers = generate_drivers_enabled() if drivers is None else drivers
    ref_lang, ref_code = oracle.primary
    cases = [*q.samples, *q.hidden_tests, *extra_cases]
    outcome: dict[str, dict] = {}
    solutions: dict[Language, str] = dict(q.solutions)
    if oracle.secondary:
        solutions[oracle.secondary[0]] = oracle.secondary[1]
        outcome[oracle.secondary[0].value] = {"solution": "independent solution (see GEN-001)"}
    for lang in Language:
        if lang in solutions:
            continue
        if not LANGUAGES[lang].available():
            report.add(finding("LANG-004", f"solutions.{lang.value}", f"No {LANGUAGES[lang].display_name} toolchain on this host; a {lang.value} solution could not be produced and verified."))
            outcome[lang.value] = {"solution": "toolchain unavailable"}
            continue
        code, note = _port(q, llm, runner, report, lang, ref_lang, ref_code, cases)
        outcome[lang.value] = {"solution": note}
        if code is not None:
            solutions[lang] = code
    if drivers:
        example = next(iter(q.drivers.items()), (None, None))
        for lang, code in solutions.items():
            if lang in q.drivers:
                continue
            outcome.setdefault(lang.value, {})["driver"] = _driver(q, llm, runner, report, lang, code, example, cases)
    report.languages = outcome
    report.log("languages: " + "; ".join(f"{l}: {o.get('solution', '-')}" + (f", driver {o['driver']}" if "driver" in o else "") for l, o in outcome.items()))


def _port(q, llm, runner, report, lang: Language, ref_lang: Language, ref_code: str, cases) -> tuple[str | None, str]:
    component = f"solutions.{lang.value}"
    previous: tuple[str, str] | None = None
    last: SuiteResult | None = None
    for attempt in range(_ATTEMPTS):
        try:
            out = llm.structured(task="port_solution", system=PORT_SOLUTION_SYSTEM, user=port_solution_user(q, lang, ref_lang, ref_code, previous), schema=SolutionProgram, max_tokens=16000)
        except LLMError as e:
            report.add(finding("LANG-002", component, f"Could not obtain a {LANGUAGES[lang].display_name} solution from the model: {e}"))
            return None, f"model error: {e}"
        suite = run_stdio_suite(lang, out.code, cases, runner=runner, limits=run_limits(q))
        if suite.all_passed:
            report.add(finding(
                "LANG-001", component,
                f"A {LANGUAGES[lang].display_name} solution ported from the {ref_lang.value} editorial passes all {len(cases)} tests (max CPU {suite.max_cpu_seconds:.2f} s). Accepting adds it to the question.",
                patch=Patch(path=component, new_value=out.code, old_value="", verified=True, verification_note=f"passes {len(cases)}/{len(cases)} tests"),
            ))
            return out.code, f"added ({attempt + 1} attempt(s))"
        last = suite
        previous = (out.code, _describe_failure(suite))
    assert last is not None
    only_slow = last.build.ok and all(c.passed or c.status is CaseStatus.TIMEOUT for c in last.cases) and any(c.status is CaseStatus.TIMEOUT for c in last.cases)
    if only_slow:
        report.add(finding("LANG-003", component,
                           f"The {LANGUAGES[lang].display_name} port is correct on every test it finishes but exceeds the time limit on {sum(1 for c in last.cases if c.status is CaseStatus.TIMEOUT)} test(s), even with the {LANGUAGES[lang].time_factor:g}x allowance for {lang.value}. Either the limit is tight for this language or the constraints need lowering.",
                           evidence=_describe_failure(last)))
        return None, "too slow"
    report.add(finding("LANG-002", component, f"No passing {LANGUAGES[lang].display_name} solution after {_ATTEMPTS} attempts; the question ships without one until an author supplies it.", evidence=_describe_failure(last)))
    return None, "failed"


def _driver(q, llm, runner, report, lang: Language, solution: str, example: tuple, cases) -> str:
    component = f"drivers.{lang.value}"
    try:
        out = llm.structured(task="driver_scaffold", system=DRIVER_SYSTEM, user=driver_user(q, lang, solution, example[0], example[1]), schema=DriverScaffold, max_tokens=16000)
    except LLMError as e:
        report.add(finding("DRV-006", component, f"Could not obtain a {LANGUAGES[lang].display_name} driver scaffold from the model: {e}"))
        return "model error"
    problems = scaffold_problems(out.driver, out.filled)
    if not problems:
        suite = run_stdio_suite(lang, out.filled, cases, runner=runner, limits=run_limits(q))
        if not suite.all_passed:
            problems.append("the filled program does not pass every test: " + _describe_failure(suite).splitlines()[0])
    if problems:
        report.add(finding("DRV-006", component, f"A {LANGUAGES[lang].display_name} driver scaffold was produced but not offered: " + "; ".join(problems) + ".", evidence=out.driver[:1500]))
        return "unverified"
    report.add(finding(
        "DRV-005", component,
        f"A {LANGUAGES[lang].display_name} driver scaffold in the house style of the existing driver. Its fixed lines are exactly those of a program that passes all {len(cases)} tests; the stub is the only part a candidate writes.",
        patch=Patch(path=component, new_value=out.driver, old_value="", verified=True, verification_note=f"filled scaffold passes {len(cases)}/{len(cases)} tests"),
    ))
    return "added"


def scaffold_problems(driver: str, filled: str) -> list[str]:
    """Mechanical checks that the scaffold's fixed part is the filled program's fixed part."""
    problems = []
    d_lines = [ln.rstrip() for ln in driver.strip("\n").split("\n")]
    f_lines = [ln.rstrip() for ln in filled.strip("\n").split("\n")]
    if not any(_STUB_MARKER.search(ln) for ln in d_lines):
        problems.append("the scaffold has no 'Write your code here' stub marker")
    if not any("do not edit" in ln.lower() for ln in d_lines):
        problems.append("the scaffold has no 'Do not edit this part of code' marker")
    fixed = []
    in_stub = False
    for ln in d_lines:
        if _STUB_MARKER.search(ln):
            in_stub = True  # the marker and the placeholder lines right after it are the stub
            continue
        if in_stub and _PLACEHOLDER.match(ln.strip()):
            continue
        in_stub = False
        if ln.strip():
            fixed.append(ln)
    i = 0
    missing = None
    for ln in fixed:
        while i < len(f_lines) and f_lines[i] != ln:
            i += 1
        if i == len(f_lines):
            missing = ln
            break
        i += 1
    if missing is not None:
        problems.append(f"scaffold line not found in the filled program: {missing.strip()[:60]!r}")
    return problems
