"""The rule catalog.

Stable IDs keep reports comparable across runs and let a reviewer suppress a rule. ``dynamic``
rules are established by execution and are never subject to opinion; ``static`` rules are
proposed by the language model and stay proposals until a reviewer accepts them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .report import Severity


@dataclass(frozen=True)
class Rule:
    id: str
    group: str
    title: str
    severity: Severity
    kind: Literal["dynamic", "static"]
    description: str


_RULES: list[Rule] = [
    # --- statement -----------------------------------------------------------------------------
    Rule("STMT-001", "statement", "Ambiguous or unclear statement", Severity.MAJOR, "static",
         "The problem statement can be read in more than one way, or a reader cannot tell what to compute."),
    Rule("STMT-002", "statement", "Missing information", Severity.MAJOR, "static",
         "Something needed to solve the problem is not stated: input format details, separators, what to print for edge cases, ordering, ties."),
    Rule("STMT-003", "statement", "Language, grammar or typo", Severity.MINOR, "static",
         "Spelling, grammar or wording problems that hurt readability but not meaning."),
    Rule("STMT-004", "statement", "Title problem", Severity.MINOR, "static",
         "The title is missing, misleading, or not in title case."),
    Rule("STMT-005", "statement", "Input/output explanation inconsistent with statement", Severity.MAJOR, "static",
         "The Input/Output Explanation contradicts the problem statement, the samples, or the driver's actual reading of input."),
    # --- constraints ---------------------------------------------------------------------------
    Rule("CONS-001", "constraints", "Missing constraint", Severity.MAJOR, "static",
         "A variable in the input has no bound (size, value range, length, character set)."),
    Rule("CONS-002", "constraints", "Constraints contradict the statement or samples", Severity.MAJOR, "static",
         "A constraint disagrees with the statement, a sample, or a hidden test."),
    Rule("CONS-003", "constraints", "Constraint formatting", Severity.MINOR, "static",
         "Constraints are hard to read: missing spaces, inconsistent notation, unclear variable names."),
    Rule("CONS-004", "constraints", "Constraints too loose or too tight for the time limit", Severity.MAJOR, "static",
         "The bounds and the time limit do not match the intended algorithm's complexity."),
    # --- samples -------------------------------------------------------------------------------
    Rule("SAMP-001", "samples", "Sample explanation does not explain the sample", Severity.MAJOR, "static",
         "The explanation is missing, explains a different case, or its arithmetic/logic is wrong."),
    Rule("SAMP-002", "samples", "Sample formatting problem", Severity.MINOR, "static",
         "Sample input/output formatting is inconsistent with the Input/Output Explanation."),
    Rule("SAMP-003", "samples", "No sample test cases", Severity.BLOCKER, "dynamic",
         "The question has no sample test case."),
    Rule("SAMP-004", "samples", "Duplicate sample", Severity.MINOR, "dynamic",
         "Two samples are identical."),
    Rule("SAMP-005", "samples", "Sample input violates the constraints", Severity.MAJOR, "dynamic",
         "The input validator (derived from the input format and constraints) rejects a sample input."),
    # --- hidden tests --------------------------------------------------------------------------
    Rule("HIDE-001", "hidden_tests", "Duplicate hidden test", Severity.MAJOR, "dynamic",
         "Two hidden tests have identical input; the duplicate adds no coverage but awards points twice."),
    Rule("HIDE-002", "hidden_tests", "Hidden test duplicates a sample", Severity.MAJOR, "dynamic",
         "A hidden test is identical to a visible sample, so it does not test anything hidden."),
    Rule("HIDE-003", "hidden_tests", "Too few hidden tests", Severity.MAJOR, "dynamic",
         "Fewer than 10 hidden tests; the audit target is 10-50 depending on difficulty."),
    Rule("HIDE-004", "hidden_tests", "Too many hidden tests", Severity.MINOR, "dynamic",
         "More than 50 hidden tests."),
    Rule("HIDE-005", "hidden_tests", "Hidden test input violates the constraints", Severity.MAJOR, "dynamic",
         "The input validator rejects a hidden test input, so the test is outside the problem as stated."),
    Rule("HIDE-006", "hidden_tests", "Hidden test metadata missing", Severity.MINOR, "dynamic",
         "A hidden test has no level or no points."),
    Rule("HIDE-007", "hidden_tests", "Weak coverage", Severity.MINOR, "static",
         "The hidden tests miss an obvious class of input: minimum size, maximum size, an edge case the statement calls out."),
    # --- driver --------------------------------------------------------------------------------
    Rule("DRV-001", "driver", "Driver + editorial does not compile", Severity.MAJOR, "dynamic",
         "Filling the driver's stub with the editorial's implementation gives a program that does not compile; the driver's scaffolding and the editorial disagree (signature, imports, class layout)."),
    Rule("DRV-002", "driver", "Driver + editorial fails tests", Severity.MAJOR, "dynamic",
         "The driver's input reading or output printing does not match the tests, even though the editorial's logic passes them."),
    Rule("DRV-003", "driver", "Driver scaffold problem", Severity.MINOR, "static",
         "The stub is unclear, the 'do not edit' region is not marked, or the signature does not match the Input/Output Explanation."),
    Rule("DRV-004", "driver", "Driver cannot be verified", Severity.INFO, "dynamic",
         "No editorial exists in the driver's language, so the driver could not be exercised."),
    # --- solution ------------------------------------------------------------------------------
    Rule("SOL-001", "solution", "Editorial code does not compile", Severity.BLOCKER, "dynamic",
         "The reference solution fails to build."),
    Rule("SOL-002", "solution", "Typographic characters in code", Severity.BLOCKER, "dynamic",
         "The code contains curly quotes, non-breaking spaces or typographic dashes and does not compile as written; the normalised code does."),
    Rule("SOL-003", "solution", "Editorial fails a sample", Severity.BLOCKER, "dynamic",
         "The reference solution's output differs from a sample's expected output. Either the sample or the solution is wrong."),
    Rule("SOL-004", "solution", "Editorial fails a hidden test", Severity.BLOCKER, "dynamic",
         "The reference solution's output differs from a hidden test's expected output. Either the test or the solution is wrong."),
    Rule("SOL-005", "solution", "Editorial crashes", Severity.BLOCKER, "dynamic",
         "The reference solution exits with an error on a test."),
    Rule("SOL-006", "solution", "Editorial exceeds the time limit", Severity.BLOCKER, "dynamic",
         "The reference solution runs longer than the question's time limit on a test."),
    Rule("SOL-007", "solution", "Editorial close to the time limit", Severity.MINOR, "dynamic",
         "The reference solution uses more than half the time limit on some test; contestants' solutions in slower languages may not fit."),
    Rule("SOL-008", "solution", "Editorial exceeds the memory limit", Severity.BLOCKER, "dynamic",
         "The reference solution runs out of memory on a test."),
    Rule("SOL-010", "solution", "Language-specific risk in the editorial", Severity.MINOR, "static",
         "Integer overflow, recursion depth, uninitialised memory, unclosed resources, undefined behaviour, or a construct that works only by accident."),
    Rule("SOL-011", "solution", "Editorial is not the intended algorithm", Severity.MAJOR, "static",
         "The code solves the problem by a method that the constraints do not allow (e.g. brute force under limits that need an O(n log n) solution), or contradicts the editorial text."),
    Rule("SOL-012", "solution", "Editorial code quality", Severity.MINOR, "static",
         "Dead code, misleading names or comments, debugging output, inconsistent style that would confuse a learner."),
    # --- editorial text ------------------------------------------------------------------------
    Rule("EDIT-001", "editorial", "Editorial missing or not an explanation", Severity.MAJOR, "static",
         "The Editorial section does not explain the solution approach (it is empty, a copy of another section, or only restates the samples)."),
    Rule("EDIT-002", "editorial", "Editorial disagrees with the code", Severity.MAJOR, "static",
         "The approach described does not match what the reference code does."),
    Rule("EDIT-003", "editorial", "Editorial lacks complexity or key insight", Severity.MINOR, "static",
         "The editorial does not state the time/space complexity or the observation the solution relies on."),
    # --- cross-component consistency -----------------------------------------------------------
    Rule("XCON-001", "consistency", "Statement, tests, driver and solution disagree", Severity.MAJOR, "static",
         "Components describe different problems: e.g. the statement says T test cases but the driver reads one, the driver's function signature does not fit the input, the output format differs between explanation and samples."),
    # --- formatting ----------------------------------------------------------------------------
    Rule("FMT-001", "formatting", "Formatting artefact", Severity.MINOR, "static",
         "Extraction or authoring artefacts: stray emphasis, broken lists, inconsistent code formatting, missing line breaks."),
    # --- difficulty ----------------------------------------------------------------------------
    Rule("DIFF-001", "difficulty", "Difficulty labels disagree", Severity.MINOR, "dynamic",
         "The title's difficulty word and the LOD number do not agree."),
    Rule("DIFF-002", "difficulty", "Assessed difficulty differs from the label", Severity.MINOR, "static",
         "The problem's actual difficulty (algorithmic insight, constraint size, implementation effort) does not match the assigned level."),
    Rule("DIFF-003", "difficulty", "Test case level does not match its content", Severity.MINOR, "static",
         "A hidden test labelled Easy/Medium/Hard is not meaningfully harder or easier than its label suggests."),
    # --- metadata ------------------------------------------------------------------------------
    Rule("META-001", "metadata", "Time limit missing", Severity.MINOR, "dynamic", "The question has no time limit."),
    Rule("META-002", "metadata", "Area missing", Severity.INFO, "dynamic", "The question has no subject area."),
    # --- generation ----------------------------------------------------------------------------
    Rule("GEN-001", "generation", "Independent solution added", Severity.INFO, "dynamic",
         "A solution in another language was written from the statement alone and passes every existing test; it is the second implementation the oracle needs and can be kept as an additional reference solution."),
    Rule("GEN-002", "generation", "Hidden tests generated", Severity.INFO, "dynamic",
         "New hidden tests were generated: inputs from a generator program, validated against the constraints, with expected outputs on which two independent implementations agree."),
    Rule("GEN-003", "generation", "Oracle not established", Severity.MAJOR, "dynamic",
         "No second implementation agreeing with the editorial on every existing test could be obtained, so expected outputs for new tests cannot be trusted and none were generated."),
    Rule("GEN-004", "generation", "Implementations disagree on a generated input", Severity.MAJOR, "dynamic",
         "The editorial and the independent solution give different outputs for an input that satisfies the constraints; one of them is wrong on inputs outside the existing tests."),
    Rule("GEN-005", "generation", "Generation skipped", Severity.INFO, "dynamic",
         "Hidden-test generation did not run: the editorial fails existing tests, no model is configured, or the generator program did not work."),
    # --- language coverage ---------------------------------------------------------------------
    Rule("LANG-001", "languages", "Solution added in another language", Severity.INFO, "dynamic",
         "A solution in a language the question lacked was written from the editorial and the statement and passes every test (existing and generated) within that language's time allowance."),
    Rule("LANG-002", "languages", "No passing solution in a language", Severity.MAJOR, "dynamic",
         "Repeated attempts to produce a solution in this language failed to compile or gave wrong answers; the question ships without that language until an author supplies one."),
    Rule("LANG-003", "languages", "Solution in a language exceeds the time limit", Severity.MAJOR, "dynamic",
         "A correct solution in this language cannot meet the time limit even with the usual per-language allowance; candidates using it are disadvantaged unless the limit or constraints change."),
    Rule("LANG-004", "languages", "Toolchain unavailable", Severity.INFO, "dynamic",
         "The language's compiler or runtime is not installed on this host, so no solution could be verified for it."),
    Rule("DRV-005", "driver", "Driver scaffold added in another language", Severity.INFO, "dynamic",
         "A driver scaffold (stub plus fixed input/output section) was written for a language that had none; its fixed parts are exactly those of a complete program that passes every test."),
    Rule("DRV-006", "driver", "Driver scaffold could not be verified", Severity.MINOR, "dynamic",
         "A driver scaffold was produced but the program built from it does not pass the tests, or the scaffold's fixed parts differ from that program; not offered."),
    # --- tooling -------------------------------------------------------------------------------
    Rule("VAL-001", "tooling", "Input validator could not be established", Severity.INFO, "dynamic",
         "The generated input validator rejected every sample or crashed, so constraint checks were skipped."),
    Rule("OTHER-001", "other", "Other issue", Severity.MINOR, "static", "An issue not covered by another rule."),
]

RULES: dict[str, Rule] = {r.id: r for r in _RULES}


def get_rule(rule_id: str) -> Rule:
    return RULES.get(rule_id, RULES["OTHER-001"])


def static_rules() -> list[Rule]:
    return [r for r in _RULES if r.kind == "static"]


def catalog_text(kind: str | None = "static") -> str:
    """The catalog as prompt text (stable — it sits in the cached system prompt)."""
    lines = []
    for r in _RULES:
        if kind and r.kind != kind:
            continue
        lines.append(f"- {r.id} [{r.severity.value}] {r.title}: {r.description}")
    return "\n".join(lines)
