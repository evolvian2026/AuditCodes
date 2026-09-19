"""The audit pipeline: execution-based findings on the real sample, model stages through a mock."""

import json
from pathlib import Path

import pytest

from auditcodes.audit import run_audit
from auditcodes.audit.patches import PatchError, apply_patch, fingerprint, shift_indices_after_removal, unified_diff
from auditcodes.audit.report import AuditReport, Finding, FindingSource, FindingStatus, Patch, Severity
from auditcodes.audit.rules import RULES, catalog_text, get_rule
from auditcodes.ingest import extract
from auditcodes.llm.client import MockLLM, strict_schema
from auditcodes.llm.prompts import LLMFinding, SplicedProgram, StaticAuditOutput, ValidatorProgram
from auditcodes.models import Language

from .conftest import require_language

FIXTURE = Path(__file__).parent / "fixtures" / "coding_ques_sample.pdf"


@pytest.fixture(scope="module")
def questions():
    return extract(FIXTURE).questions


@pytest.fixture(scope="module")
def q1_report(questions, runner):
    require_language(Language.JAVA)
    return run_audit(questions[0], runner=runner, llm=None)


@pytest.fixture(scope="module")
def q2_report(questions, runner):
    require_language(Language.JAVA)
    return run_audit(questions[1], runner=runner, llm=None)


def _by_rule(report: AuditReport, rule_id: str) -> list[Finding]:
    return [f for f in report.findings if f.rule_id == rule_id]


def test_rule_catalog_is_consistent():
    assert all(r.id == k for k, r in RULES.items())
    assert get_rule("NOPE-999").id == "OTHER-001"
    text = catalog_text("static")
    assert "STMT-001" in text and "SOL-001" not in text
    assert {r.kind for r in RULES.values()} == {"dynamic", "static"}


def test_q1_dynamic_findings(q1_report):
    r = q1_report
    assert r.status == "done" and r.error is None
    assert r.verifications["java"].all_passed and r.verifications["java"].normalized
    assert r.verifications["java"].total == 13
    (sol,) = _by_rule(r, "SOL-002")
    assert sol.severity is Severity.BLOCKER and sol.source is FindingSource.DYNAMIC
    assert sol.patch.path == "solutions.java" and sol.patch.verified is True and "’" not in sol.patch.new_value
    assert "16 typographic single quote" in sol.message
    (dup,) = _by_rule(r, "HIDE-001")
    assert dup.component == "hidden_tests.9" and dup.patch.op == "remove"
    assert "Test Case 10" in dup.message and "Test Case 9" in dup.message
    assert not _by_rule(r, "HIDE-003") and not _by_rule(r, "SOL-001") and not _by_rule(r, "SOL-003")
    assert r.counts() == {"blocker": 1, "major": 1, "minor": 0, "info": 0}
    assert "model stages skipped" in " ".join(r.stages)


def test_q2_dynamic_findings(q2_report):
    r = q2_report
    assert r.verifications["java"].all_passed and not r.verifications["java"].normalized
    assert {f.rule_id for f in r.findings} == {"HIDE-001", "HIDE-002", "HIDE-003"}
    (dup,) = _by_rule(r, "HIDE-001")
    assert dup.component == "hidden_tests.3" and "Test Case 2" in dup.message
    (samp,) = _by_rule(r, "HIDE-002")
    assert samp.component == "hidden_tests.4" and "Example 1" in samp.message
    assert "5 hidden test" in _by_rule(r, "HIDE-003")[0].message


def test_wrong_expected_output_is_a_blocker(questions, runner):
    q = questions[1].model_copy(deep=True)
    q.hidden_tests[0].stdout = "35862\n"
    r = run_audit(q, runner=runner, llm=None)
    (f,) = _by_rule(r, "SOL-004")
    assert f.severity is Severity.BLOCKER and f.component == "hidden_tests.0"
    assert "expected:\n35862" in f.evidence and "got:\n35861" in f.evidence
    assert r.verifications["java"].passed == 6


def test_broken_editorial_is_a_blocker(questions, runner):
    q = questions[1].model_copy(deep=True)
    q.solutions[Language.JAVA] = "public class Main { public static void main(String[] a) { int x = ; } }"
    r = run_audit(q, runner=runner, llm=None)
    (f,) = _by_rule(r, "SOL-001")
    assert f.severity is Severity.BLOCKER and "error" in f.evidence.lower()
    assert not r.verifications["java"].build_ok


def test_metadata_findings(questions, runner):
    q = questions[0].model_copy(update={"time_limit_seconds": None, "area": None, "metadata": {**questions[0].metadata, "Difficulty": "Hard"}})
    r = run_audit(q, runner=runner, llm=None)
    assert {f.rule_id for f in r.findings} >= {"META-001", "META-002", "DIFF-001"}
    assert "title says 'Hard'" in _by_rule(r, "DIFF-001")[0].message


# --- patches -------------------------------------------------------------------------------


def test_apply_and_remove_patches(questions):
    q = questions[0]
    q2 = apply_patch(q, Patch(path="title", new_value="Agent RA"))
    assert q2.title == "Agent RA" and q.title == "AGENT RA"
    removed = apply_patch(q, Patch(op="remove", path="hidden_tests.9", item_fingerprint=fingerprint(q.hidden_tests[9])))
    assert len(removed.hidden_tests) == 9
    # the fingerprint finds the item even if it has moved
    moved = apply_patch(q, Patch(op="remove", path="hidden_tests.0", item_fingerprint=fingerprint(q.hidden_tests[9])))
    assert len(moved.hidden_tests) == 9 and moved.hidden_tests[0] == q.hidden_tests[0]
    with pytest.raises(PatchError):
        apply_patch(q, Patch(op="remove", path="hidden_tests.0", item_fingerprint="nope"))
    with pytest.raises(PatchError):
        apply_patch(q, Patch(path="id", new_value="x"))
    with pytest.raises(PatchError):
        apply_patch(q, Patch(op="remove", path="solutions.java"))


def test_shift_indices_after_removal():
    fs = [
        Finding(rule_id="HIDE-005", severity=Severity.MAJOR, source=FindingSource.DYNAMIC, component="hidden_tests.7", title="t", message="m", patch=Patch(path="hidden_tests.7.stdout", new_value="x")),
        Finding(rule_id="HIDE-005", severity=Severity.MAJOR, source=FindingSource.DYNAMIC, component="hidden_tests.2", title="t", message="m"),
        Finding(rule_id="SAMP-005", severity=Severity.MAJOR, source=FindingSource.DYNAMIC, component="samples.7", title="t", message="m"),
    ]
    shift_indices_after_removal(fs, "hidden_tests", 4)
    assert fs[0].component == "hidden_tests.6" and fs[0].patch.path == "hidden_tests.6.stdout"
    assert fs[1].component == "hidden_tests.2" and fs[2].component == "samples.7"


def test_unified_diff_text():
    d = unified_diff("a\nb\nc\n", "a\nB\nc\n", "description_md")
    assert "-b" in d and "+B" in d and "description_md (proposed)" in d


def test_strict_schema_closes_objects():
    schema = strict_schema(StaticAuditOutput.model_json_schema())
    assert schema["additionalProperties"] is False and set(schema["required"]) == {"findings", "difficulty", "summary"}
    assert schema["$defs"]["LLMFinding"]["additionalProperties"] is False
    assert "patch" in schema["$defs"]["LLMFinding"]["required"]


# --- model stages via the mock ---------------------------------------------------------------

VALIDATOR_Q1 = """import sys
data = sys.stdin.read().split()
if len(data) != 1:
    print("expected exactly one token"); sys.exit(1)
s = data[0]
if not (1 <= len(s) <= 50):
    print("length must be 1..50"); sys.exit(1)
if set(s) - set("ab?"):
    print("characters must be a, b or ?"); sys.exit(1)
sys.exit(0)
"""


def _mock_for_q1(q):
    driver = q.drivers[Language.JAVA]
    body = "return new Main2().solve(ancientWord);"

    def splice(system, user):
        sol = q.solutions[Language.JAVA].replace("’", "'")
        # driver with the stub replaced by a call into the editorial's method, kept as a second class
        helper = sol.replace("import java.util.Scanner;", "").replace("public class Main", "class Main2").replace("public static String ancientTranslation", "public String solve")
        helper = helper[: helper.rfind("// Do not edit this part of code")] + "}\n"
        return SplicedProgram(code=driver.replace("//Write your code here", body) + "\n" + helper, issues=["the scaffold's stub has no return statement, so it does not compile until implemented"])

    def static(system, user):
        assert "execution results (ground truth)" in user and "13/13 tests pass" in user
        return StaticAuditOutput(
            findings=[
                LLMFinding(rule_id="STMT-003", severity=Severity.MINOR, component="editorial_md", title="Grammar in editorial", message="'Traverse all character' should be 'Traverse all characters'.", evidence="Traverse all character from left to right", patch={"path": "editorial_md", "new_value": q.editorial_md.replace("all character ", "all characters ")}, confidence=0.9),
                LLMFinding(rule_id="SOL-012", severity=Severity.MINOR, component="solutions.java", title="Debug print", message="Proposes a code change that breaks the output.", evidence=None, patch={"path": "solutions.java", "new_value": q.solutions[Language.JAVA].replace("’", "'").replace('return s.toString();', 'return s.toString() + "!";')}, confidence=0.4),
                LLMFinding(rule_id="NOT-A-RULE", severity=Severity.INFO, component="title", title="Unknown rule", message="maps to OTHER-001", evidence=None, patch=None, confidence=0.5),
                LLMFinding(rule_id="STMT-004", severity=Severity.INFO, component="title", title="No-op patch", message="identical value", evidence=None, patch={"path": "title", "new_value": q.title}, confidence=0.5),
            ],
            difficulty={"assessed": "medium", "rationale": "The greedy argument needs a short proof."},
            summary="Mostly fine.",
        )

    return MockLLM(responses={"validator": ValidatorProgram(python_code=VALIDATOR_Q1, notes="checks length and alphabet"), "splice": splice, "static_audit": static})


def test_model_stages_with_mock(questions, runner):
    require_language(Language.JAVA)
    q = questions[0]
    llm = _mock_for_q1(q)
    r = run_audit(q, runner=runner, llm=llm, progress=lambda n: None)
    assert r.status == "done", r.error
    assert [c.task for c in llm.calls] == ["validator", "splice", "static_audit"]
    # validator ran on every input and accepted them all
    assert r.validator["established"] and all(x["valid"] for x in r.validator["results"]) and len(r.validator["results"]) == 13
    assert not _by_rule(r, "HIDE-005") and not _by_rule(r, "VAL-001")
    # spliced driver compiled and passed, its reported issue became a static DRV-003
    assert r.driver_check["java"]["build_ok"] and r.driver_check["java"]["passed"] == 13
    (drv,) = _by_rule(r, "DRV-003")
    assert drv.source is FindingSource.STATIC and not _by_rule(r, "DRV-001") and not _by_rule(r, "DRV-002")
    # static findings: prose patch kept as a diff, code patch execution-checked and failed, unknown rule remapped, no-op dropped
    (gram,) = _by_rule(r, "STMT-003")
    assert gram.source is FindingSource.STATIC and gram.patch.verified is None and "characters" in gram.patch.new_value and gram.patch.old_value == q.editorial_md
    (bad,) = _by_rule(r, "SOL-012")
    assert bad.patch.verified is False and "0/13" in bad.patch.verification_note or "passes" in bad.patch.verification_note
    (other,) = _by_rule(r, "OTHER-001")
    assert other.title == "Unknown rule"
    (noop,) = _by_rule(r, "STMT-004")
    assert noop.patch is None and "identical" in noop.message
    (diff,) = _by_rule(r, "DIFF-002")
    assert diff.patch.new_value == "medium" and "assessed as medium" in diff.message.lower()
    assert r.llm_model == "mock"


def test_validator_rejections_and_distrust(questions, runner):
    q = questions[0].model_copy(deep=True)
    q.hidden_tests[0].stdin = "a" * 60 + "\n"  # longer than the constraint allows
    llm = MockLLM(responses={"validator": ValidatorProgram(python_code=VALIDATOR_Q1, notes=""), "splice": lambda s, u: (_ for _ in ()).throw(AssertionError("not called")),
                             "static_audit": StaticAuditOutput(findings=[], difficulty={"assessed": "easy", "rationale": ""}, summary="")})
    q.drivers = {}
    r = run_audit(q, runner=runner, llm=llm)
    (f,) = _by_rule(r, "HIDE-005")
    assert f.component == "hidden_tests.0" and "length must be 1..50" in f.message
    # a validator that rejects every sample is discarded
    llm2 = MockLLM(responses={"validator": ValidatorProgram(python_code="import sys\nprint('nope')\nsys.exit(1)\n", notes=""), "static_audit": llm.responses["static_audit"]})
    r2 = run_audit(q, runner=runner, llm=llm2)
    assert not r2.validator["established"] and _by_rule(r2, "VAL-001") and not _by_rule(r2, "HIDE-005")
    # a crashing validator is discarded too
    llm3 = MockLLM(responses={"validator": ValidatorProgram(python_code="import sys\nx = 1 / 0\n", notes=""), "static_audit": llm.responses["static_audit"]})
    r3 = run_audit(q, runner=runner, llm=llm3)
    assert not r3.validator["established"] and "crashed" in _by_rule(r3, "VAL-001")[0].message


def test_llm_failure_does_not_lose_execution_findings(questions, runner):
    from auditcodes.llm.client import LLMError

    def boom(system, user):
        raise LLMError("simulated outage")

    llm = MockLLM(responses={"validator": boom, "splice": boom, "static_audit": boom})
    r = run_audit(questions[1], runner=runner, llm=llm)
    assert r.status == "done"
    assert {f.rule_id for f in r.findings} == {"HIDE-001", "HIDE-002", "HIDE-003", "GEN-005"}
    assert "static audit failed" in (r.error or "")
    assert not r.validator["established"] and not r.oracle["established"]
    assert "could be obtained" in _by_rule(r, "GEN-005")[0].message


def test_report_roundtrip(q1_report):
    data = q1_report.model_dump_json()
    assert AuditReport.model_validate_json(data) == q1_report


# --- phase 4: oracle, generation, projection ------------------------------------------------

from auditcodes.audit.patches import apply_all, normalized_input  # noqa: E402
from auditcodes.audit.testgen import allocate, target_count  # noqa: E402
from auditcodes.llm.prompts import GeneratorProgram, SolutionProgram  # noqa: E402
from auditcodes.models import Difficulty, TestCase, TestCategory, TestOrigin  # noqa: E402

PY_SOLUTION_Q1 = """import sys
s = list(sys.stdin.read().split()[0])
n = len(s)
for i in range(n):
    if s[i] == '?':
        prev_a = i > 0 and s[i - 1] == 'a'
        next_a = i + 1 < n and s[i + 1] == 'a'
        s[i] = 'b' if (prev_a or next_a) else 'a'
print(''.join(s))
"""

WRONG_PY_SOLUTION_Q1 = "import sys\nprint(sys.stdin.read().split()[0].replace('?', 'b'))\n"

GENERATOR_Q1 = """import random, sys
cat, seed = sys.argv[1], int(sys.argv[2])
random.seed(seed)
def valid_word(n):
    w = []
    for i in range(n):
        w.append('b' if (w and w[-1] == 'a') else random.choice('ab'))
    return w
if cat == 'boundary_min':
    n = 1
elif cat == 'boundary_max':
    n = 50
elif cat == 'random_small':
    n = random.randint(1, 10)
elif cat == 'random_large':
    n = random.randint(25, 50)
else:
    n = random.randint(2, 50)
w = valid_word(n)
if cat == 'edge':
    mask = [True] * n if seed % 2 else [False] * n
elif cat == 'adversarial':
    mask = [i % 2 == 0 for i in range(n)]
elif cat == 'structured':
    mask = [(i % 3) != 1 for i in range(n)]
else:
    mask = [random.random() < 0.5 for _ in range(n)]
print(''.join('?' if m else c for c, m in zip(w, mask)))
"""


def _phase4_mock(q, solution=PY_SOLUTION_Q1, generator=GENERATOR_Q1):
    llm = _mock_for_q1(q)

    def independent(system, user):
        assert "solutions.java" not in user and "StringBuilder" not in user  # the editorial is never shown
        if "Python 3" in user:
            return SolutionProgram(code=solution, approach="Greedy left to right; O(n).")
        return SolutionProgram(code="this is not valid code in any language", approach="n/a")

    llm.responses["independent_solution"] = independent
    llm.responses["generator"] = GeneratorProgram(python_code=generator, notes="masks a valid word")
    return llm


def test_allocation_and_targets(questions):
    assert target_count(questions[0]) == 15 and target_count(questions[1]) == 40
    assert sum(allocate(31).values()) == 31 and len(allocate(31)) == 7
    assert sum(allocate(6).values()) == 6 and allocate(0) == {}
    assert all(v >= 1 for v in allocate(7).values()) and len(allocate(7)) == 7


def test_oracle_and_generation_q1(questions, runner):
    require_language(Language.JAVA)
    q = questions[0]
    llm = _phase4_mock(q)
    r = run_audit(q, runner=runner, llm=llm)
    assert r.status == "done", r.error
    tasks = [c.task for c in llm.calls]
    # C++ is preferred first, fails twice (garbage code), then Python succeeds on the first attempt
    assert tasks.count("independent_solution") == 3 and tasks[-1] == "generator"
    assert r.oracle["established"] and r.oracle["primary"] == "java" and r.oracle["secondary"] == "python"
    (gen1,) = _by_rule(r, "GEN-001")
    assert gen1.patch.path == "solutions.python" and gen1.patch.verified and gen1.patch.new_value == PY_SOLUTION_Q1
    # 10 hidden tests, one duplicate -> 9 unique; easy target 15 -> 6 generated
    assert r.generation["target"] == 15 and r.generation["existing_unique"] == 9 and r.generation["needed"] == 6
    assert r.generation["generated"] == 6 and r.generation["disagreements"] == 0 and r.generation["too_slow"] == 0
    assert not _by_rule(r, "HIDE-003") and not _by_rule(r, "GEN-004") and not _by_rule(r, "GEN-003")
    (gen2,) = _by_rule(r, "GEN-002")
    items = [TestCase.model_validate(i) for i in gen2.patch.items]
    assert len(items) == 6 and gen2.patch.op == "append" and gen2.patch.verified
    assert {i.category for i in items} >= {TestCategory.BOUNDARY_MIN, TestCategory.BOUNDARY_MAX, TestCategory.EDGE}
    assert all(i.origin is TestOrigin.GENERATED and i.stdout and set(i.stdout.strip()) <= set("ab") for i in items)
    assert [i.label for i in items] == [f"Test Case {n}" for n in range(11, 17)]
    # points follow the question's own scheme (5/10/15 by level)
    assert all(i.points == {Difficulty.EASY: 5, Difficulty.MEDIUM: 10, Difficulty.HARD: 15}[i.difficulty] for i in items)
    existing = {normalized_input(t.stdin) for t in q.samples + q.hidden_tests}
    assert not any(normalized_input(i.stdin) in existing for i in items)
    # every generated input is a valid instance: length <= 50, alphabet ab?, and the expected output has no 'aa'
    for i in items:
        s = i.stdin.strip()
        assert 1 <= len(s) <= 50 and set(s) <= set("ab?") and "aa" not in i.stdout
    # the projection applies SOL-002, the duplicate removal, GEN-001 and the generated tests
    assert r.projection["remaining_execution"]["blocker"] == 0 and r.projection["remaining_execution"]["major"] == 0
    assert r.projection["hidden_tests"] == 15 and r.projection["solutions"] == ["java", "python"]
    assert set(r.projection["applied"]) == {"SOL-002", "HIDE-001", "GEN-001", "GEN-002"}
    assert r.projection["verifications"]["java"]["all_passed"] and r.projection["verifications"]["java"]["total"] == 18
    assert r.projection["verifications"]["python"]["all_passed"]


def test_disagreeing_independent_solution_blocks_generation(questions, runner):
    require_language(Language.JAVA)
    q = questions[0]
    llm = _phase4_mock(q, solution=WRONG_PY_SOLUTION_Q1)
    r = run_audit(q, runner=runner, llm=llm)
    assert not r.oracle["established"]
    (f,) = _by_rule(r, "GEN-003")
    assert f.severity is Severity.MAJOR and "expected" in f.evidence and "python attempt 2" in f.evidence
    assert r.generation is None and not _by_rule(r, "GEN-002") and not _by_rule(r, "GEN-001")
    assert "generator" not in [c.task for c in llm.calls]


def test_generation_reports_disagreement_on_new_inputs(questions, runner):
    """A second implementation that agrees on the existing tests but not on all inputs."""
    require_language(Language.JAVA)
    q = questions[0]
    # correct except when the answer is 'abab', which no existing test produces
    tricky = PY_SOLUTION_Q1.replace("print(''.join(s))", "print('bbbb' if ''.join(s) == 'abab' else ''.join(s))")
    gen = GENERATOR_Q1.replace("n = random.randint(2, 50)", "n = 4")  # edge with an odd seed -> '????' -> 'abab'
    llm = _phase4_mock(q, solution=tricky, generator=gen)
    r = run_audit(q, runner=runner, llm=llm)
    assert r.oracle["established"], r.oracle
    (f,) = _by_rule(r, "GEN-004")
    assert "abab" in f.evidence and "bbbb" in f.evidence and f.severity is Severity.MAJOR
    assert r.generation["disagreements"] >= 1
    accepted = _by_rule(r, "GEN-002")
    assert not accepted or all(i["stdin"].strip() != "????" for i in accepted[0].patch.items)  # the disputed input is never added


def test_generation_skipped_when_editorial_fails(questions, runner):
    q = questions[0].model_copy(deep=True)
    q.hidden_tests[0].stdout = "wrong\n"
    llm = _phase4_mock(q)
    r = run_audit(q, runner=runner, llm=llm)
    assert not r.oracle["established"] and _by_rule(r, "GEN-005")
    assert "independent_solution" not in [c.task for c in llm.calls]


def test_generation_when_target_already_met(questions, runner):
    q = questions[0].model_copy(deep=True)
    q.hidden_tests = q.hidden_tests[:9] + [TestCase(stdin=f"{'?' * k}\n", stdout="x\n") for k in range(3, 10)]  # 16 unique
    llm = _phase4_mock(q)
    llm.responses["independent_solution"] = SolutionProgram(code=PY_SOLUTION_Q1, approach="")
    r = run_audit(q, runner=runner, llm=llm)
    # the editorial fails the fake tests, so the oracle is refused before anything is generated
    assert not r.oracle["established"]


def test_broken_generator_program(questions, runner):
    require_language(Language.JAVA)
    q = questions[0]
    llm = _phase4_mock(q, generator="import sys\nsys.exit(3)\n")
    r = run_audit(q, runner=runner, llm=llm)
    assert r.oracle["established"]
    assert [c.task for c in llm.calls].count("generator") == 2  # retried once with the failure shown
    assert r.generation["generated"] == 0 and any("did not run" in f.message for f in _by_rule(r, "GEN-005"))


def test_apply_append_and_apply_all(questions):
    q = questions[0]
    items = [TestCase(stdin="?a\n", stdout="ba\n", origin=TestOrigin.GENERATED).model_dump(mode="json"),
             TestCase(stdin=q.hidden_tests[0].stdin, stdout="dup\n").model_dump(mode="json")]
    q2 = apply_patch(q, Patch(op="append", path="hidden_tests", items=items))
    assert len(q2.hidden_tests) == 11 and q2.hidden_tests[-1].stdin == "?a\n"  # duplicate of an existing input skipped
    with pytest.raises(PatchError):
        apply_patch(q, Patch(op="append", path="samples", items=items))
    fs = [
        Finding(rule_id="HIDE-001", severity=Severity.MAJOR, source=FindingSource.DYNAMIC, component="hidden_tests.9", title="t", message="m", patch=Patch(op="remove", path="hidden_tests.9", item_fingerprint=fingerprint(q.hidden_tests[9]))),
        Finding(rule_id="GEN-002", severity=Severity.INFO, source=FindingSource.DYNAMIC, component="hidden_tests", title="t", message="m", patch=Patch(op="append", path="hidden_tests", items=items[:1])),
        Finding(rule_id="X", severity=Severity.INFO, source=FindingSource.DYNAMIC, component="title", title="t", message="m", patch=Patch(path="title", new_value="New")),
        Finding(rule_id="Y", severity=Severity.INFO, source=FindingSource.DYNAMIC, component="hidden_tests.0", title="t", message="m", patch=Patch(op="remove", path="hidden_tests.0", item_fingerprint="gone")),
    ]
    patched, applied, skipped = apply_all(q, fs)
    assert patched.title == "New" and len(patched.hidden_tests) == 10  # -1 duplicate +1 generated
    assert [f.rule_id for f in applied] == ["X", "HIDE-001", "GEN-002"] and skipped[0][0].rule_id == "Y"
