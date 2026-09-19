"""Failure modes the audit relies on: wrong answers, crashes, timeouts, bad output, build errors."""

import pytest

from auditcodes.exec.harness import CaseStatus, run_suite
from auditcodes.exec.runner import Limits
from auditcodes.models import Checker, IOSpec, Language, Param, TestCase

from .conftest import require_language
from .problems import BY_NAME

ADD = BY_NAME["add"]

WRONG = {
    Language.PYTHON: "def add(a, b):\n    return a - b\n",
    Language.JAVASCRIPT: "var add = function(a, b) { return a - b; };\n",
    Language.CPP: "class Solution {\npublic:\n    int add(int a, int b) { return a - b; }\n};\n",
    Language.JAVA: "class Solution {\n    public int add(int a, int b) { return a - b; }\n}\n",
    Language.C: "int add(int a, int b) { return a - b; }\n",
}

CRASH = {
    Language.PYTHON: "def add(a, b):\n    return [][a]\n",
    Language.JAVASCRIPT: "var add = function(a, b) { throw new Error('boom'); };\n",
    Language.CPP: "class Solution {\npublic:\n    int add(int a, int b) { int* p = 0; return *p + a; }\n};\n",
    Language.JAVA: "class Solution {\n    public int add(int a, int b) { int[] x = new int[1]; return x[a + 5]; }\n}\n",
    Language.C: "int add(int a, int b) { int* p = 0; return *p + a; }\n",
}

BROKEN = {
    Language.PYTHON: "def add(a, b)\n    return a + b\n",
    Language.JAVASCRIPT: "var add = function(a, b) { return a + b; \n",
    Language.CPP: "class Solution {\npublic:\n    int add(int a, int b) { return a + b }\n};\n",
    Language.JAVA: "class Solution {\n    public int add(int a, int b) { return a + b }\n}\n",
    Language.C: "int add(int a, int b) { return a + b }\n",
}

LOOP = {
    Language.PYTHON: "def add(a, b):\n    while True: pass\n",
    Language.JAVASCRIPT: "var add = function(a, b) { while (true) {} };\n",
    Language.CPP: "class Solution {\npublic:\n    int add(int a, int b) { volatile int x = 0; while (true) x++; return x; }\n};\n",
    Language.JAVA: "class Solution {\n    public int add(int a, int b) { long x = 0; while (true) x++; }\n}\n",
    Language.C: "int add(int a, int b) { volatile int x = 0; while (1) x++; return x; }\n",
}

LANGS = list(Language)
IDS = [l.value for l in LANGS]


@pytest.mark.parametrize("language", LANGS, ids=IDS)
def test_wrong_answer_is_reported_with_actual_value(language, runner):
    require_language(language)
    suite = run_suite(language, ADD.io_spec, WRONG[language], ADD.cases, runner=runner)
    assert suite.build.ok, suite.build.log
    assert not suite.all_passed
    first = suite.cases[0]
    assert first.status is CaseStatus.FAILED
    assert first.expected == 3 and first.actual == -1
    assert suite.passed == 2  # (-5)-5 != 0, but x-0 == x+0 for the two boundary cases


@pytest.mark.parametrize("language", LANGS, ids=IDS)
def test_runtime_error_is_reported(language, runner):
    require_language(language)
    suite = run_suite(language, ADD.io_spec, CRASH[language], ADD.cases[:1], runner=runner)
    assert suite.build.ok, suite.build.log
    assert suite.cases[0].status is CaseStatus.RUNTIME_ERROR, suite.cases[0]


@pytest.mark.parametrize("language", LANGS, ids=IDS)
def test_build_error_is_reported(language, runner):
    require_language(language)
    suite = run_suite(language, ADD.io_spec, BROKEN[language], ADD.cases, runner=runner)
    assert not suite.build.ok
    assert suite.build.log.strip()
    assert suite.cases == []
    assert not suite.all_passed


@pytest.mark.parametrize("language", [Language.PYTHON, Language.C, Language.JAVA], ids=["python", "c", "java"])
def test_timeout_is_reported(language, runner):
    require_language(language)
    limits = Limits(cpu_seconds=1.0, wall_seconds=3.0)
    suite = run_suite(language, ADD.io_spec, LOOP[language], ADD.cases[:1], runner=runner, limits=limits)
    assert suite.build.ok, suite.build.log
    assert suite.cases[0].status is CaseStatus.TIMEOUT


def test_bad_output_is_distinguished_from_wrong_answer(runner):
    suite = run_suite(Language.PYTHON, ADD.io_spec, "def add(a, b):\n    return 'three'\n", ADD.cases[:1], runner=runner)
    assert suite.cases[0].status is CaseStatus.BAD_OUTPUT
    suite = run_suite(Language.JAVASCRIPT, ADD.io_spec, "var add = function(a, b) {};\n", ADD.cases[:1], runner=runner)
    assert suite.cases[0].status is CaseStatus.BAD_OUTPUT


def test_stop_on_failure(runner):
    suite = run_suite(Language.PYTHON, ADD.io_spec, WRONG[Language.PYTHON], ADD.cases, runner=runner, stop_on_failure=True)
    assert len(suite.cases) == 1


def test_missing_expectation_records_actual(runner):
    cases = [TestCase(args=[4, 5])]
    suite = run_suite(Language.PYTHON, ADD.io_spec, ADD.solutions[Language.PYTHON], cases, runner=runner)
    assert suite.cases[0].passed and suite.cases[0].actual == 9


def test_unordered_checker(runner):
    spec = IOSpec(function_name="pairs", params=[Param(name="n", type="int")], return_type="list<list<int>>")
    code = "def pairs(n):\n    return [[i, i + 1] for i in range(n)][::-1]\n"
    cases = [TestCase(args=[2], expected=[[0, 1], [1, 2]])]
    assert not run_suite(Language.PYTHON, spec, code, cases, runner=runner).all_passed
    assert run_suite(Language.PYTHON, spec, code, cases, runner=runner, checker=Checker.UNORDERED).all_passed


def test_solution_prelude_stdout_noise_is_bad_output(runner):
    """A solution that prints debug output corrupts the JSON channel — surfaced, not hidden."""
    code = "def add(a, b):\n    print('debug')\n    return a + b\n"
    suite = run_suite(Language.PYTHON, ADD.io_spec, code, ADD.cases[:1], runner=runner)
    assert suite.cases[0].status is CaseStatus.BAD_OUTPUT
