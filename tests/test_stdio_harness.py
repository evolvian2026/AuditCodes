import pytest

from auditcodes.exec.harness import CaseStatus, outputs_equal_text, run_stdio_suite
from auditcodes.exec.languages import java_main_class, program_filename
from auditcodes.exec.runner import Limits
from auditcodes.models import Language, TestCase

from .conftest import require_language

CASES = [TestCase(stdin="1 2\n", stdout="3\n"), TestCase(stdin="10 -4\n", stdout="6\n"), TestCase(stdin="0 0", stdout="0")]

PROGRAMS = {
    Language.PYTHON: "a, b = map(int, input().split())\nprint(a + b)\n",
    Language.JAVASCRIPT: "const [a, b] = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(a + b);\n",
    Language.C: "#include <stdio.h>\nint main(void) { int a, b; scanf(\"%d %d\", &a, &b); printf(\"%d\\n\", a + b); return 0; }\n",
    Language.CPP: "#include <iostream>\nint main() { long long a, b; std::cin >> a >> b; std::cout << a + b << std::endl; }\n",
    Language.JAVA: "import java.util.*;\npublic class Adder {\n    public static void main(String[] args) { Scanner s = new Scanner(System.in); System.out.println(s.nextInt() + s.nextInt()); }\n}\n",
}


@pytest.mark.parametrize("language", list(Language), ids=[l.value for l in Language])
def test_full_programs_pass(language, runner):
    require_language(language)
    suite = run_stdio_suite(language, PROGRAMS[language], CASES, runner=runner)
    assert suite.build.ok, suite.build.log
    assert suite.all_passed, [(c.index, c.status, c.actual, c.stderr) for c in suite.cases]


def test_wrong_output_and_runtime_error(runner):
    suite = run_stdio_suite(Language.PYTHON, "print(int(input().split()[0]) * 2)\n", CASES, runner=runner)
    assert [c.status for c in suite.cases] == [CaseStatus.FAILED, CaseStatus.FAILED, CaseStatus.PASSED]
    assert suite.cases[0].actual == "2\n" and suite.cases[0].expected == "3\n"
    suite = run_stdio_suite(Language.PYTHON, "raise ValueError('no')\n", CASES[:1], runner=runner)
    assert suite.cases[0].status is CaseStatus.RUNTIME_ERROR and "ValueError" in suite.cases[0].stderr


def test_timeout_uses_time_limit(runner):
    suite = run_stdio_suite(Language.PYTHON, "while True: pass\n", CASES[:1], runner=runner, limits=Limits(cpu_seconds=0.5, wall_seconds=3))
    assert suite.cases[0].status is CaseStatus.TIMEOUT


def test_java_program_file_follows_public_class():
    assert java_main_class(PROGRAMS[Language.JAVA]) == "Adder"
    assert program_filename(Language.JAVA, PROGRAMS[Language.JAVA]) == "Adder.java"
    assert program_filename(Language.JAVA, "class Main { }") == "Main.java"
    assert program_filename(Language.JAVA, "// nothing") == "Main.java"
    assert program_filename(Language.C, "") == "main.c"


def test_output_comparison_is_judge_style():
    assert outputs_equal_text("1\n14\n", "1\n14")
    assert outputs_equal_text("a b  \n\n", "a b\n")
    assert outputs_equal_text("x\r\ny\r\n", "x\ny\n")
    assert not outputs_equal_text("1\n14\n", "1\n 14\n")
    assert not outputs_equal_text("1\n14\n", "14\n1\n")
