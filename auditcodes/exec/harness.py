"""Build a solution with its generated driver and run it against test cases.

This is the ground-truth engine: every statement the audit makes about code correctness comes from
a ``SuiteResult`` produced here, never from reading the code.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from ..models import Checker, IOSpec, Language, TestCase
from . import drivers
from .compare import values_match
from .languages import DEFAULT_COMPILE_LIMITS, DEFAULT_RUN_LIMITS, LanguageSpec, get_spec, java_main_class, program_filename
from .protocol import OutputDecodeError, decode_output, encode_args
from .runner import Limits, LocalRunner, Runner, RunResult, Status

_EXCERPT = 4000


class CaseStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"  # ran fine, wrong answer
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    MEMORY_LIMIT = "memory_limit"
    OUTPUT_LIMIT = "output_limit"
    BAD_OUTPUT = "bad_output"  # ran fine, but stdout was not a value of the return type
    INTERNAL_ERROR = "internal_error"


_STATUS_MAP = {
    Status.TIMEOUT: CaseStatus.TIMEOUT,
    Status.MEMORY_LIMIT: CaseStatus.MEMORY_LIMIT,
    Status.OUTPUT_LIMIT: CaseStatus.OUTPUT_LIMIT,
    Status.RUNTIME_ERROR: CaseStatus.RUNTIME_ERROR,
    Status.INTERNAL_ERROR: CaseStatus.INTERNAL_ERROR,
}


@dataclass
class BuildResult:
    ok: bool
    log: str = ""
    result: RunResult | None = None


@dataclass
class CaseResult:
    index: int
    status: CaseStatus
    expected: Any
    actual: Any = None
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    wall_seconds: float = 0.0
    cpu_seconds: float = 0.0
    max_rss_bytes: int = 0

    @property
    def passed(self) -> bool:
        return self.status is CaseStatus.PASSED


@dataclass
class SuiteResult:
    language: Language
    build: BuildResult
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def all_passed(self) -> bool:
        return self.build.ok and bool(self.cases) and all(c.passed for c in self.cases)

    @property
    def max_cpu_seconds(self) -> float:
        return max((c.cpu_seconds for c in self.cases), default=0.0)

    def summary(self) -> str:
        if not self.build.ok:
            return f"{self.language.value}: build failed"
        return f"{self.language.value}: {self.passed}/{len(self.cases)} passed"


def write_workdir(language: Language, io_spec: IOSpec, solution_code: str, workdir: Path) -> list[str]:
    """Write the generated driver + solution files; returns the source file names."""
    workdir.mkdir(parents=True, exist_ok=True)
    files = drivers.generate(language, io_spec, solution_code)
    for name, contents in files.items():
        (workdir / name).write_text(contents, encoding="utf-8")
    return list(files)


def write_program(language: Language, program_code: str, workdir: Path) -> list[str]:
    """Write a complete stdio program; returns the source file names."""
    workdir.mkdir(parents=True, exist_ok=True)
    name = program_filename(language, program_code)
    (workdir / name).write_text(program_code if program_code.endswith("\n") else program_code + "\n", encoding="utf-8")
    return [name]


def build(language: Language, workdir: Path, runner: Runner, limits: Limits = DEFAULT_COMPILE_LIMITS, sources: list[str] | None = None) -> BuildResult:
    spec = get_spec(language)
    cmd = spec.compile_command(limits, sources or [spec.program_filename])
    if cmd is None:
        return BuildResult(ok=True)
    result = runner.run(cmd, cwd=workdir, limits=spec.compile_limits(limits))
    log = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if result.status is Status.INTERNAL_ERROR:
        log = result.reason
    return BuildResult(ok=result.ok, log=log, result=result)


def run_one(
    language: Language,
    io_spec: IOSpec,
    workdir: Path,
    args: list[Any],
    runner: Runner,
    limits: Limits = DEFAULT_RUN_LIMITS,
) -> tuple[RunResult, Any, str | None]:
    """Run the built solution once. Returns (run result, decoded value or None, decode error)."""
    spec = get_spec(language)
    result = runner.run(spec.run_command(limits), cwd=workdir, stdin=encode_args(io_spec, args), limits=spec.run_limits(limits))
    if not result.ok:
        return result, None, None
    try:
        return result, decode_output(io_spec.return_spec, result.stdout), None
    except OutputDecodeError as e:
        return result, None, str(e)


def run_suite(
    language: Language,
    io_spec: IOSpec,
    solution_code: str,
    cases: Sequence[TestCase],
    *,
    runner: Runner | None = None,
    limits: Limits = DEFAULT_RUN_LIMITS,
    compile_limits: Limits = DEFAULT_COMPILE_LIMITS,
    checker: Checker = Checker.EXACT,
    stop_on_failure: bool = False,
    workdir: Path | None = None,
) -> SuiteResult:
    runner = runner or LocalRunner()
    own_dir = workdir is None
    workdir = workdir or Path(tempfile.mkdtemp(prefix="auditcodes-"))
    try:
        sources = write_workdir(language, io_spec, solution_code, workdir)
        build_result = build(language, workdir, runner, compile_limits, sources)
        suite = SuiteResult(language=language, build=build_result)
        if not build_result.ok:
            return suite
        for i, case in enumerate(cases):
            suite.cases.append(_run_case(language, io_spec, workdir, i, case, runner, limits, checker))
            if stop_on_failure and not suite.cases[-1].passed:
                break
        return suite
    finally:
        if own_dir:
            shutil.rmtree(workdir, ignore_errors=True)


def _run_case(language, io_spec, workdir, index, case: TestCase, runner, limits, checker) -> CaseResult:
    result, actual, decode_error = run_one(language, io_spec, workdir, case.args, runner, limits)
    common = dict(
        index=index,
        expected=case.expected,
        stdout=result.stdout_text(_EXCERPT),
        stderr=result.stderr_text(_EXCERPT),
        wall_seconds=result.wall_seconds,
        cpu_seconds=result.cpu_seconds,
        max_rss_bytes=result.max_rss_bytes,
    )
    if not result.ok:
        return CaseResult(status=_STATUS_MAP[result.status], message=result.reason, **common)
    if decode_error is not None:
        return CaseResult(status=CaseStatus.BAD_OUTPUT, message=decode_error, **common)
    if case.expected is None:
        # No expectation recorded (e.g. oracle run): report the value, count as passed.
        return CaseResult(status=CaseStatus.PASSED, actual=actual, **common)
    if values_match(case.expected, actual, io_spec.return_spec, checker):
        return CaseResult(status=CaseStatus.PASSED, actual=actual, **common)
    return CaseResult(status=CaseStatus.FAILED, actual=actual, message="wrong answer", **common)


# --- stdio mode ------------------------------------------------------------------------------


def outputs_equal_text(expected: str, actual: str) -> bool:
    """Judge-style comparison: trailing whitespace on each line and trailing blank lines ignored."""
    return _norm_text(expected) == _norm_text(actual)


def _norm_text(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def run_stdio_suite(
    language: Language,
    program_code: str,
    cases: Sequence[TestCase],
    *,
    runner: Runner | None = None,
    limits: Limits = DEFAULT_RUN_LIMITS,
    compile_limits: Limits = DEFAULT_COMPILE_LIMITS,
    stop_on_failure: bool = False,
    workdir: Path | None = None,
) -> SuiteResult:
    """Build a complete program and run it on raw stdin/stdout test cases."""
    runner = runner or LocalRunner()
    own_dir = workdir is None
    workdir = workdir or Path(tempfile.mkdtemp(prefix="auditcodes-"))
    spec = get_spec(language)
    main_class = java_main_class(program_code) if language is Language.JAVA else "Main"
    try:
        sources = write_program(language, program_code, workdir)
        build_result = build(language, workdir, runner, compile_limits, sources)
        suite = SuiteResult(language=language, build=build_result)
        if not build_result.ok:
            return suite
        for i, case in enumerate(cases):
            stdin = (case.stdin or "")
            if stdin and not stdin.endswith("\n"):
                stdin += "\n"
            result = runner.run(spec.run_command(limits, main_class), cwd=workdir, stdin=stdin.encode("utf-8"), limits=spec.run_limits(limits))
            common = dict(
                index=i,
                expected=case.stdout,
                stdout=result.stdout_text(_EXCERPT),
                stderr=result.stderr_text(_EXCERPT),
                wall_seconds=result.wall_seconds,
                cpu_seconds=result.cpu_seconds,
                max_rss_bytes=result.max_rss_bytes,
            )
            actual = result.stdout.decode("utf-8", errors="replace")
            if not result.ok:
                cr = CaseResult(status=_STATUS_MAP[result.status], message=result.reason, actual=actual, **common)
            elif case.stdout is None or outputs_equal_text(case.stdout, actual):
                cr = CaseResult(status=CaseStatus.PASSED, actual=actual, **common)
            else:
                cr = CaseResult(status=CaseStatus.FAILED, actual=actual, message="wrong answer", **common)
            suite.cases.append(cr)
            if stop_on_failure and not cr.passed:
                break
        return suite
    finally:
        if own_dir:
            shutil.rmtree(workdir, ignore_errors=True)
