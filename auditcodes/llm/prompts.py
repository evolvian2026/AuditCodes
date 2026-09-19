"""Prompts and output schemas for the model-driven audit stages.

System prompts are stable strings (cached across questions); everything question-specific goes
into the user turn. Output schemas are Pydantic models, enforced by structured outputs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..audit.report import Severity
from ..audit.rules import catalog_text
from ..models import Difficulty, Language, Question

# --- output schemas --------------------------------------------------------------------------


class ProposedPatch(BaseModel):
    path: str = Field(description="Dotted field path to replace, e.g. 'description_md', 'constraints', 'solutions.java', 'hidden_tests.3.stdout'")
    new_value: str = Field(description="The complete new value of the field")


class LLMFinding(BaseModel):
    rule_id: str
    severity: Severity
    component: str = Field(description="The field path the finding is about, e.g. 'input_format_md', 'solutions.java', 'hidden_tests.2'")
    title: str = Field(description="One line, specific to this question")
    message: str = Field(description="What is wrong and why it matters; concrete, 1-4 sentences")
    evidence: str | None = Field(description="A short quote from the question showing the problem, or null")
    patch: ProposedPatch | None = Field(description="A complete replacement value for one field that fixes the problem, or null when no safe textual fix exists")
    confidence: float = Field(description="0-1, how sure you are this is a real problem")


class DifficultyAssessment(BaseModel):
    assessed: Difficulty
    rationale: str


class StaticAuditOutput(BaseModel):
    findings: list[LLMFinding]
    difficulty: DifficultyAssessment
    summary: str = Field(description="2-3 sentences on the overall state of the question")


class ValidatorProgram(BaseModel):
    python_code: str = Field(description="Complete Python 3 program: reads a test input from stdin, exits 0 if valid, prints one reason and exits 1 if not")
    notes: str


class SplicedProgram(BaseModel):
    code: str = Field(description="The complete program: the driver with its stub implemented")
    issues: list[str] = Field(description="Mismatches noticed between the driver scaffold and the editorial (signature, types, imports); empty if none")


# --- system prompts (stable, cached) ---------------------------------------------------------

STATIC_AUDIT_SYSTEM = f"""You audit competitive-programming / coding-assessment questions for a question bank.

You receive one question with all its components, plus the results of actually compiling and running the reference solution against every test case. Those execution results are ground truth: do not dispute them, build on them. When the reference solution disagrees with an expected output, your job is to work out which side is wrong and say so with a patch.

Report findings against this rule catalog. Use only these rule ids; use OTHER-001 for anything else.
{catalog_text("static")}

Guidance:
- Be specific. A finding must name what is wrong in this question, quote it, and explain the consequence for a learner or for grading. Never report a rule just because it exists.
- Preserve the author's intent and the question's difficulty. A patch corrects or clarifies; it does not redesign the problem, change constraints to make a solution easier, or restyle text that was fine.
- A patch is the complete new value of exactly one field. For prose fields use Markdown (a single line break is kept as a line break). For 'constraints' give one constraint per line. For code fields give the complete corrected program, preserving the original layout and comments where they are correct. Do not patch the question id, number, or test inputs.
- Only propose code patches for defects you are sure of; every code patch will be compiled and run against the tests, and a patch that fails is shown as failed.
- When execution shows the reference solution failing a test, decide from the statement which side is wrong: propose a patch to that test's expected output only if you can compute the correct output by hand with certainty, otherwise patch the solution, otherwise explain and leave patch null.
- Do not repeat what execution already established (compile errors, failed tests, duplicate tests, counts): those findings already exist. Explain them or adjudicate them only through XCON/SOL/SAMP static rules when you add real information.
- Typographic characters in code (curly quotes, non-breaking spaces) are already handled; do not report them.
- Extraction artefacts in Markdown (bold markers around identifiers, odd spacing before commas) come from the source PDF; report them as FMT-001 only when they would confuse a reader in the exported document.
- Severity: blocker means the question cannot be used as published; major means it must be fixed before publishing; minor is worth fixing; info is an observation.
- Assess the real difficulty (easy / medium / hard) from the algorithmic insight required, constraint sizes and implementation effort, and compare with the label.
- Language-specific checks for the reference code: integer overflow, recursion depth, uninitialised values, off-by-one on bounds, undefined behaviour, exceptions on edge inputs, unclosed resources, debugging output.
"""

VALIDATOR_SYSTEM = """You write input validators for competitive-programming problems.

Given a problem's Input Explanation and Constraints (and samples to calibrate on), write a complete Python 3 program that reads one test input from standard input and checks that it follows the described format exactly and satisfies every stated constraint.

Rules:
- Exit with status 0 when the input is valid. When it is not, print one short reason to stdout and exit with status 1.
- Be strict about what is stated (counts, ranges, character sets, lengths) and tolerant about what is not (trailing whitespace, a missing final newline, Windows line endings).
- Read all of stdin first (sys.stdin.read()) and parse tokens or lines as the format demands.
- Use only the standard library. Never crash: wrap parsing so that malformed input produces a reason and exit code 1, not a traceback.
- If the constraints mention a variable without a bound, do not invent one.
"""

SPLICE_SYSTEM = """You complete driver-code scaffolds for coding-assessment questions.

You receive the driver code a candidate sees (a scaffold with a stub such as '// Write your code here' and a fixed main/IO section marked 'do not edit') and the reference solution (a complete program). Produce the program a correct candidate would submit: the scaffold with the stub implemented using the reference solution's logic.

Rules:
- Keep everything outside the stub exactly as in the driver: the class layout, the main method and its input reading and output printing, imports it already has. Add imports only if the implementation needs them.
- Take the logic from the reference solution; do not invent a new algorithm. Add helper methods/functions if the reference uses them.
- If the scaffold's function signature does not match how the reference solution is structured (different parameters, types or return type), implement the scaffold's signature anyway and list the mismatch in 'issues'.
- Output the complete program in 'code'. List anything a question author should fix in the scaffold in 'issues'.
"""

# --- user-turn builders ----------------------------------------------------------------------


def question_block(q: Question, include_code: bool = True, include_tests: bool = True, max_tests: int = 12) -> str:
    parts = [f"# Question {q.id}: {q.title}", ""]
    meta = [f"difficulty label: {q.difficulty.value if q.difficulty else 'unknown'}", f"LOD: {q.lod}", f"area: {q.area}", f"time limit: {q.time_limit_seconds} s", f"i/o mode: {q.io_mode.value}"]
    parts.append("; ".join(meta))
    parts += ["", "## description_md", q.description_md or "(empty)"]
    parts += ["", "## input_format_md", q.input_format_md or "(empty)"]
    parts += ["", "## output_format_md", q.output_format_md or "(empty)"]
    parts += ["", "## constraints", "\n".join(c.text for c in q.constraints) or "(empty)"]
    if include_tests:
        parts += ["", "## samples"]
        for i, t in enumerate(q.samples):
            parts += [f"### samples.{i} ({t.label or 'Example ' + str(i + 1)})", "input:", "```", (t.stdin or "").rstrip("\n"), "```", "expected output:", "```", (t.stdout or "").rstrip("\n"), "```"]
    parts += ["", "## sample_explanation_md", q.sample_explanation_md or "(empty)"]
    if include_code:
        for lang, code in q.drivers.items():
            parts += ["", f"## drivers.{lang.value}", "```", code.rstrip("\n"), "```"]
    parts += ["", "## editorial_md", q.editorial_md or "(empty)"]
    if include_code:
        for lang, code in q.solutions.items():
            parts += ["", f"## solutions.{lang.value}", "```", code.rstrip("\n"), "```"]
    if include_tests:
        parts += ["", f"## hidden_tests ({len(q.hidden_tests)} total; showing up to {max_tests})"]
        for i, t in enumerate(q.hidden_tests[:max_tests]):
            parts += [
                f"### hidden_tests.{i} ({t.label or 'Test ' + str(i + 1)}; level {t.difficulty.value if t.difficulty else '?'}; points {t.points})",
                "input:", "```", _clip(t.stdin or "", 1200), "```", "expected output:", "```", _clip(t.stdout or "", 600), "```",
            ]
    return "\n".join(parts)


def _clip(text: str, limit: int) -> str:
    text = text.rstrip("\n")
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


def static_audit_user(q: Question, execution_summary: str) -> str:
    return (
        question_block(q)
        + "\n\n## execution results (ground truth)\n"
        + execution_summary
        + "\n\nAudit this question against the catalog. Return findings, the difficulty assessment and a summary."
    )


def validator_user(q: Question) -> str:
    return (
        question_block(q, include_code=False, include_tests=True, max_tests=0)
        + "\n\nWrite the validator program for this input format."
    )


def splice_user(q: Question, language: Language) -> str:
    return (
        f"# Question {q.id}: {q.title}\n\n## input_format_md\n{q.input_format_md or ''}\n\n## output_format_md\n{q.output_format_md or ''}\n\n"
        f"## driver scaffold ({language.value})\n```\n{q.drivers[language].rstrip()}\n```\n\n"
        f"## reference solution ({language.value})\n```\n{q.solutions[language].rstrip()}\n```\n\n"
        "Produce the completed program."
    )
