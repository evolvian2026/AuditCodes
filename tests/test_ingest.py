"""Extraction against the real sample export and a synthetic template PDF."""

from pathlib import Path

import pytest

from auditcodes.exec.harness import run_stdio_suite
from auditcodes.exec.runner import Limits
from auditcodes.ingest import extract, layout
from auditcodes.ingest.normalize import normalize_code
from auditcodes.models import Difficulty, IOMode, Language, TestCategory, TestOrigin

from .conftest import require_language
from .pdf_builder import PY_SOLUTION, build_python_question

FIXTURE = Path(__file__).parent / "fixtures" / "coding_ques_sample.pdf"


@pytest.fixture(scope="module")
def sample():
    return extract(FIXTURE)


def test_sample_document_level(sample):
    assert sample.title == "Coding Ques"
    assert sample.warnings == []
    assert [q.id for q in sample.questions] == ["105376", "105402"]
    assert [q.number for q in sample.questions] == [1, 2]


def test_q1_metadata_and_sections(sample):
    q = sample.questions[0]
    assert q.title == "AGENT RA"
    assert q.io_mode is IOMode.STDIO
    assert q.difficulty is Difficulty.EASY and q.lod == 33
    assert q.area == "Greedy Algorithms"
    assert q.time_limit_seconds == 2.0
    assert q.metadata["DBNO"] == "105376" and q.metadata["Difficulty"] == "Easy"
    assert q.provenance.pages == [1, 2, 3, 4]
    assert q.description_md.startswith("Big ***agent RA*** is exploring")
    # inline code spans were pulled out of the sentence by naive extraction; they must be back in place
    assert "non-empty string **s** consisting of the characters **'a', 'b',** and **'?'.** The character" in q.input_format_md
    assert q.output_format_md.startswith("Output the word that is")
    assert [c.text for c in q.constraints] == ["Length of s is at most 50."]
    assert q.sample_explanation_md.startswith("Words **ababab , ababbb , bbabab** and **bbabbb** could be written on paper.")
    assert q.editorial_md.startswith("Solution is greedy.")
    # a wrapped prose line must be re-joined with a space, not glued
    assert "You can always put 'b'" in q.editorial_md
    assert "Coding Ques" not in q.description_md  # running footer removed


def test_q1_tests(sample):
    q = sample.questions[0]
    assert [(t.stdin, t.stdout) for t in q.samples] == [("?ba??b\n", "ababab\n"), ("a\n", "a\n"), ("?????a\n", "ababba\n")]
    assert all(t.category is TestCategory.SAMPLE and t.origin is TestOrigin.SOURCE for t in q.samples)
    assert len(q.hidden_tests) == 10
    t1, t8, t10 = q.hidden_tests[0], q.hidden_tests[7], q.hidden_tests[9]
    assert (t1.label, t1.difficulty, t1.points) == ("Test Case 1", Difficulty.EASY, 5)
    assert (t8.label, t8.difficulty, t8.points) == ("Test Case 8", Difficulty.HARD, 15)
    assert t8.stdin == "??b??\n" and t8.stdout == "abbab\n"
    assert t10.stdin == "?b?b?b?b?b\n"
    assert all(t.category is TestCategory.ORIGINAL for t in q.hidden_tests)


def test_q1_code_and_warnings(sample):
    q = sample.questions[0]
    driver = q.drivers[Language.JAVA]
    assert driver.startswith("import java.util.Scanner;\n\npublic class Main {\n    public static String ancientTranslation(String ancientWord) {\n        //Write your code here\n    }")
    sol = q.solutions[Language.JAVA]
    assert "\n                        if (s.charAt(i - 1) == \u2019b\u2019 &&" in sol  # faithful: curly quotes kept; 6 levels = 24 spaces
    assert sol.rstrip().endswith("}")
    assert q.provenance.confidence["solution"] == 0.6
    assert any("typographic characters in code" in w for w in q.provenance.warnings)
    assert q.provenance.confidence["hidden"] == 1.0 and q.provenance.confidence["samples"] == 1.0


def test_q2_multiline_cases_and_wrapped_code(sample):
    q = sample.questions[1]
    assert q.title == "Sunehri and her Bag" and q.difficulty is Difficulty.HARD and q.lod == 99
    assert q.area == "Bit Manipulation" and q.time_limit_seconds == 1.0
    assert q.samples[0].stdin == "2\n4\n2 5 4 6\n1 1 1 1\n5\n13 3 9 4 2\n1 1 1 1 1\n"
    assert q.samples[0].stdout == "1\n14\n"
    assert len(q.hidden_tests) == 5
    assert q.hidden_tests[2].stdout.split() == ["36550", "35861", "12581", "30629", "18337", "8899", "30304", "5397", "20343", "17182"]
    assert q.hidden_tests[2].stdin.count("\n") == 31  # 1 + 10 * 3 lines, spanning a page break
    # the signature that wrapped at the right margin is one line again
    assert "static List<Integer> calculateMinimum(int T, List<Integer> N, List<List<Integer>> A, List<List<Integer>> P) {" in q.solutions[Language.JAVA]
    assert "List<List<Integer>> P) {" in q.drivers[Language.JAVA]
    assert any("wrapped code line" in w for w in q.provenance.warnings)
    assert [c.text for c in q.constraints][0].startswith("1 ")


def test_q1_editorial_fails_raw_and_passes_normalized(sample):
    require_language(Language.JAVA)
    q = sample.questions[0]
    cases = q.samples + q.hidden_tests
    limits = Limits(cpu_seconds=q.time_limit_seconds, wall_seconds=10)
    raw = run_stdio_suite(Language.JAVA, q.solutions[Language.JAVA], cases, limits=limits)
    assert not raw.build.ok and "\\u2019" in raw.build.log
    norm = normalize_code(q.solutions[Language.JAVA])
    assert norm.changes == {"typographic single quote '’' -> \"'\"": 16}
    fixed = run_stdio_suite(Language.JAVA, norm.text, cases, limits=limits)
    assert fixed.all_passed, [(c.index, c.status, c.message) for c in fixed.cases if not c.passed]
    assert len(fixed.cases) == 13


def test_q2_editorial_passes(sample):
    require_language(Language.JAVA)
    q = sample.questions[1]
    suite = run_stdio_suite(Language.JAVA, q.solutions[Language.JAVA], q.samples + q.hidden_tests, limits=Limits(cpu_seconds=2, wall_seconds=15))
    assert suite.all_passed, suite.build.log or [(c.index, c.status, c.message) for c in suite.cases if not c.passed]


def test_synthetic_python_question(tmp_path):
    pdf = build_python_question(tmp_path / "synthetic.pdf")
    assets = tmp_path / "assets"
    result = extract(pdf, assets)
    assert result.warnings == []
    assert len(result.questions) == 1
    q = result.questions[0]
    assert q.id == "555" and q.number == 7 and q.title == "Indent Check"
    assert q.provenance.warnings == []
    # Python indentation recovered from x offsets, across the whole block
    assert q.solutions[Language.PYTHON] == PY_SOLUTION
    assert "    # Write your code here\n    pass" in q.drivers[Language.PYTHON]
    # figure placed between the two description paragraphs and saved to the assets dir
    assert len(q.images) == 1 and q.images[0].section == "description"
    assert (assets / q.images[0].path).exists()
    assert q.description_md == f"Print the sum of the two numbers shown in the figure.\n\n![Figure]({q.images[0].path})\n\nThat is all there is to it."
    assert [(t.stdin, t.stdout) for t in q.samples] == [("1 2\n", "3\n")]
    assert [(t.label, t.difficulty, t.points) for t in q.hidden_tests] == [("Test Case 1", Difficulty.EASY, 5), ("Test Case 2", Difficulty.HARD, 15)]
    # the running footer never leaks into content, even though the document spans pages
    doc = layout.load(pdf)
    assert len(doc.pages) >= 2
    assert not any("Coding Ques" in ln.text for ln in doc.lines())
    suite = run_stdio_suite(Language.PYTHON, q.solutions[Language.PYTHON], q.samples + q.hidden_tests)
    assert suite.all_passed


def test_layout_line_markup_handles_punctuation_runs():
    # bold word followed by an italic-only period must not produce "****."
    from auditcodes.ingest.layout import Line, Span

    spans = [
        Span("with ", 0, 20, 10, 9, "Times", False, False, False),
        Span("N integers", 21, 60, 10, 9, "Times-BoldItalic", True, True, False),
        Span(".", 60, 62, 10, 9, "Times-Italic", False, True, False),
        Span(" Due to", 63, 90, 10, 9, "Times", False, False, False),
    ]
    assert Line(1, 10, 0, 90, spans, False).markdown == "with ***N integers.*** Due to"
