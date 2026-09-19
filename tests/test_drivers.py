"""Every reference problem, in every language, through the real toolchain."""

import pytest

from auditcodes.exec import drivers
from auditcodes.exec.harness import CaseStatus, run_suite
from auditcodes.models import IOSpec, Language, Param

from .conftest import require_language
from .problems import PROBLEMS

ALL = [(p, lang) for p in PROBLEMS for lang in Language]


@pytest.mark.parametrize("problem,language", ALL, ids=[f"{p.name}-{l.value}" for p, l in ALL])
def test_reference_solutions_pass(problem, language, runner):
    require_language(language)
    suite = run_suite(language, problem.io_spec, problem.solutions[language], problem.cases, runner=runner)
    assert suite.build.ok, suite.build.log
    failures = [(c.index, c.status, c.message, c.stdout, c.stderr) for c in suite.cases if not c.passed]
    assert not failures, failures
    assert suite.all_passed


def test_c_rejects_deep_nesting():
    spec = IOSpec(function_name="f", params=[Param(name="a", type="list<list<list<int>>>")], return_type="int")
    with pytest.raises(drivers.UnsupportedSignatureError):
        drivers.signature(Language.C, spec)
    assert drivers.signature(Language.JAVA, spec)  # other languages are fine


def test_signatures_follow_conventions():
    spec = IOSpec(
        function_name="twoSum",
        params=[Param(name="nums", type="list<int>"), Param(name="target", type="int")],
        return_type="list<int>",
    )
    assert drivers.signature(Language.C, spec) == "int* twoSum(int* nums, int numsSize, int target, int* returnSize);"
    assert "vector<int> twoSum(vector<int>& nums, int target)" in drivers.signature(Language.CPP, spec)
    assert "public int[] twoSum(int[] nums, int target)" in drivers.signature(Language.JAVA, spec)
    assert drivers.signature(Language.PYTHON, spec) == "def twoSum(nums: list[int], target: int) -> list[int]:"
    assert "var twoSum = function(nums, target) {" in drivers.signature(Language.JAVASCRIPT, spec)


@pytest.mark.parametrize("language", list(Language), ids=[l.value for l in Language])
def test_generated_files_use_expected_names(language):
    spec = IOSpec(function_name="f", params=[Param(name="a", type="int")], return_type="int")
    files = drivers.generate(language, spec, "// solution")
    expected = {
        Language.C: {"main.c"},
        Language.CPP: {"main.cpp"},
        Language.JAVA: {"Main.java", "Solution.java"},
        Language.PYTHON: {"main.py", "solution.py"},
        Language.JAVASCRIPT: {"main.js"},
    }[language]
    assert set(files) == expected
