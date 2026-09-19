"""Hidden-test generation.

Inputs come from a model-written generator program run per category and seed in the sandbox;
each input must pass the constraint validator (when one was established) and must not duplicate
an existing test. Expected outputs come from *running* both oracle implementations; a case is
kept only when they agree. Disagreements are reported, not resolved by guessing.
"""

from __future__ import annotations

import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..exec.harness import CaseStatus, run_stdio_suite
from ..exec.runner import Limits, Runner
from ..llm.client import LLM, LLMError
from ..llm.prompts import GENERATOR_SYSTEM, GeneratorProgram, generator_user
from ..models import Difficulty, Question, TestCase, TestCategory, TestOrigin
from .dynamic import finding, run_limits
from .oracle import Oracle, cross_check_limits
from .patches import normalized_input
from .report import AuditReport, Patch

TARGET = {Difficulty.EASY: 15, Difficulty.MEDIUM: 25, Difficulty.HARD: 40, None: 20}
MIN_TOTAL, MAX_TOTAL = 10, 50
QUOTA = [
    (TestCategory.BOUNDARY_MIN, 0.10),
    (TestCategory.BOUNDARY_MAX, 0.15),
    (TestCategory.EDGE, 0.20),
    (TestCategory.STRUCTURED, 0.15),
    (TestCategory.RANDOM_SMALL, 0.15),
    (TestCategory.RANDOM_LARGE, 0.15),
    (TestCategory.ADVERSARIAL, 0.10),
]
LEVEL = {
    TestCategory.BOUNDARY_MIN: Difficulty.EASY,
    TestCategory.RANDOM_SMALL: Difficulty.EASY,
    TestCategory.EDGE: Difficulty.MEDIUM,
    TestCategory.STRUCTURED: Difficulty.MEDIUM,
    TestCategory.RANDOM_LARGE: Difficulty.MEDIUM,
    TestCategory.BOUNDARY_MAX: Difficulty.HARD,
    TestCategory.ADVERSARIAL: Difficulty.HARD,
}
DEFAULT_POINTS = {Difficulty.EASY: 5, Difficulty.MEDIUM: 10, Difficulty.HARD: 15}
_GEN_LIMITS = Limits(cpu_seconds=5.0, wall_seconds=20.0, output_bytes=1024 * 1024)
_MAX_INPUT_BYTES = 64 * 1024
_SEEDS_PER_CASE = 4


@dataclass
class Candidate:
    category: TestCategory
    stdin: str


def target_count(q: Question) -> int:
    return TARGET.get(q.difficulty, TARGET[None])


def allocate(needed: int) -> dict[TestCategory, int]:
    if needed <= 0:
        return {}
    raw = {cat: needed * share for cat, share in QUOTA}
    counts = {cat: int(v) for cat, v in raw.items()}
    for cat, _ in sorted(QUOTA, key=lambda cs: raw[cs[0]] - int(raw[cs[0]]), reverse=True):
        if sum(counts.values()) >= needed:
            break
        counts[cat] += 1
    if needed >= len(QUOTA):
        for cat in counts:  # every category represented once the budget allows it
            if counts[cat] == 0:
                donor = max(counts, key=counts.get)
                counts[donor] -= 1
                counts[cat] += 1
    return {cat: n for cat, n in counts.items() if n > 0}


def generate(q: Question, llm: LLM, runner: Runner, report: AuditReport, oracle: Oracle) -> None:
    if not oracle.established:
        return
    existing = {normalized_input(t.stdin) for t in [*q.samples, *q.hidden_tests]}
    unique_hidden = len({normalized_input(t.stdin) for t in q.hidden_tests})
    target = target_count(q)
    needed = target - unique_hidden
    stats = {"target": target, "existing_unique": unique_hidden, "needed": max(0, needed)}
    if needed <= 0:
        report.generation = {**stats, "generated": 0, "note": "the question already has enough unique hidden tests for its difficulty"}
        report.log(f"generation: {unique_hidden} unique hidden tests already meet the target of {target}")
        return
    quotas = allocate(needed)
    program = _generator_program(q, llm, runner, report)
    if program is None:
        report.generation = {**stats, "generated": 0, "note": "no working generator program"}
        return
    validator = report.validator.get("code") if report.validator and report.validator.get("established") else None
    candidates, rejected = _collect(program, quotas, existing, validator, runner)
    stats.update({"candidates": len(candidates), "rejected_invalid": rejected["invalid"], "rejected_duplicate": rejected["duplicate"], "rejected_other": rejected["other"]})
    if not candidates:
        report.generation = {**stats, "generated": 0, "note": "the generator produced no usable inputs"}
        report.add(finding("GEN-005", "hidden_tests", "The generator program produced no input that was valid, non-empty and not already present; no hidden tests were generated."))
        return
    primary_lang, primary_code = oracle.primary
    secondary_lang, secondary_code = oracle.secondary
    probe = [TestCase(stdin=c.stdin, stdout=None) for c in candidates]
    primary = run_stdio_suite(primary_lang, primary_code, probe, runner=runner, limits=run_limits(q))
    secondary = run_stdio_suite(secondary_lang, secondary_code, probe, runner=runner, limits=cross_check_limits(q))
    accepted: list[TestCase] = []
    disagreements: list[dict] = []
    slow: list[dict] = []
    points = _points_scheme(q)
    next_number = len(q.hidden_tests) + 1
    for i, cand in enumerate(candidates):
        p, s = primary.cases[i], secondary.cases[i]
        if p.status is CaseStatus.TIMEOUT:
            slow.append({"category": cand.category.value, "cpu": p.cpu_seconds, "stdin": cand.stdin[:300]})
            continue
        if not p.passed or not s.passed:
            disagreements.append({"category": cand.category.value, "stdin": cand.stdin[:300], "primary": f"{p.status.value} {p.message}".strip(), "secondary": f"{s.status.value} {s.message}".strip()})
            continue
        if normalized_input(p.actual) != normalized_input(s.actual):
            disagreements.append({"category": cand.category.value, "stdin": cand.stdin[:300], "primary": (p.actual or "")[:300], "secondary": (s.actual or "")[:300]})
            continue
        level = LEVEL[cand.category]
        accepted.append(TestCase(
            stdin=cand.stdin, stdout=p.actual, label=f"Test Case {next_number + len(accepted)}", points=points[level],
            category=cand.category, difficulty=level, origin=TestOrigin.GENERATED,
        ))
    stats.update({"generated": len(accepted), "disagreements": len(disagreements), "too_slow": len(slow), "by_category": dict(Counter(c.category.value for c in accepted))})
    report.generation = stats
    if slow:
        worst = max(slow, key=lambda x: x["cpu"])
        report.add(finding("SOL-006", f"solutions.{primary_lang.value}",
                           f"The editorial exceeds the time limit ({run_limits(q).cpu_seconds:g} s) on {len(slow)} generated input(s) at or near the constraint ceiling ({worst['category']}). Existing tests do not exercise the limit; the intended complexity may not fit the constraints.",
                           evidence=worst["stdin"]))
    if disagreements:
        d = disagreements[0]
        report.add(finding("GEN-004", "hidden_tests",
                           f"On {len(disagreements)} generated input(s) the {primary_lang.value} editorial and the independent {secondary_lang.value} solution give different results. One of them is wrong beyond the existing tests; these inputs were not added.",
                           evidence=f"input ({d['category']}):\n{d['stdin']}\n\n{primary_lang.value} editorial:\n{d['primary']}\n\n{secondary_lang.value}:\n{d['secondary']}"))
    if not accepted:
        report.add(finding("GEN-005", "hidden_tests", "No generated input survived validation and cross-checking; no hidden tests were added."))
        report.log("generation: nothing accepted")
        return
    patch = Patch(op="append", path="hidden_tests", items=[t.model_dump(mode="json") for t in accepted], verified=True,
                  verification_note=f"{len(accepted)} case(s); expected outputs agreed by {primary_lang.value} and {secondary_lang.value}")
    summary = ", ".join(f"{n} {cat.replace('_', ' ')}" for cat, n in stats["by_category"].items())
    hide003 = next((f for f in report.findings if f.rule_id == "HIDE-003" and f.open), None)
    if hide003 is not None:
        hide003.patch = patch
        hide003.message += f" {len(accepted)} verified test(s) were generated ({summary}); accepting brings the total to {unique_hidden + len(accepted)}."
    else:
        report.add(finding("GEN-002", "hidden_tests", f"{len(accepted)} hidden test(s) generated to reach the target of {target} for a {q.difficulty.value if q.difficulty else 'default'} question ({summary}). Each input passed the validator{'' if validator else ' (none established, so only cross-checking applied)'}; each expected output was produced independently by the {primary_lang.value} editorial and the {secondary_lang.value} solution.",
                           patch=patch))
    report.log(f"generation: {len(accepted)} accepted, {len(disagreements)} disagreement(s), {len(slow)} too slow, {rejected['invalid']} invalid, {rejected['duplicate']} duplicate")


def _generator_program(q: Question, llm: LLM, runner: Runner, report: AuditReport) -> str | None:
    previous: tuple[str, str] | None = None
    for attempt in range(2):
        try:
            out = llm.structured(task="generator", system=GENERATOR_SYSTEM, user=generator_user(q, previous), schema=GeneratorProgram, max_tokens=8000)
        except LLMError as e:
            report.add(finding("GEN-005", "hidden_tests", f"Hidden-test generation skipped: the generator program could not be obtained ({e})."))
            return None
        with tempfile.TemporaryDirectory(prefix="auditcodes-gen-") as d:
            (Path(d) / "gen.py").write_text(out.python_code, encoding="utf-8")
            r = runner.run([sys.executable, "gen.py", "random_small", "1"], cwd=Path(d), limits=_GEN_LIMITS)
        if r.ok and r.stdout.strip():
            return out.python_code
        failure = f"running 'python gen.py random_small 1' gave {r.status.value} (exit {r.exit_code}); stderr:\n{r.stderr_text(800)}" if not r.ok else "the program printed nothing"
        previous = (out.python_code, failure)
    report.add(finding("GEN-005", "hidden_tests", "Hidden-test generation skipped: the generator program did not run.", evidence=previous[1] if previous else None))
    return None


def _collect(program: str, quotas: dict[TestCategory, int], existing: set[str], validator: str | None, runner: Runner) -> tuple[list[Candidate], dict[str, int]]:
    rejected = {"invalid": 0, "duplicate": 0, "other": 0}
    seen = set(existing)
    out: list[Candidate] = []
    with tempfile.TemporaryDirectory(prefix="auditcodes-gen-") as d:
        workdir = Path(d)
        (workdir / "gen.py").write_text(program, encoding="utf-8")
        if validator:
            (workdir / "validate.py").write_text(validator, encoding="utf-8")
        for cat, n in quotas.items():
            got = 0
            for seed in range(1, n * _SEEDS_PER_CASE + 1):
                if got >= n:
                    break
                r = runner.run([sys.executable, "gen.py", cat.value, str(seed)], cwd=workdir, limits=_GEN_LIMITS)
                text = r.stdout.decode("utf-8", errors="replace")
                if not r.ok or not text.strip() or len(r.stdout) > _MAX_INPUT_BYTES:
                    rejected["other"] += 1
                    continue
                if not text.endswith("\n"):
                    text += "\n"
                key = normalized_input(text)
                if key in seen:
                    rejected["duplicate"] += 1
                    continue
                if validator:
                    v = runner.run([sys.executable, "validate.py"], cwd=workdir, stdin=text.encode("utf-8"), limits=_GEN_LIMITS)
                    if not v.ok:
                        rejected["invalid"] += 1
                        continue
                seen.add(key)
                out.append(Candidate(cat, text))
                got += 1
    return out, rejected


def _points_scheme(q: Question) -> dict[Difficulty, int]:
    scheme = dict(DEFAULT_POINTS)
    by_level: dict[Difficulty, Counter] = {}
    for t in q.hidden_tests:
        if t.difficulty and t.points is not None:
            by_level.setdefault(t.difficulty, Counter())[t.points] += 1
    for level, counter in by_level.items():
        scheme[level] = counter.most_common(1)[0][0]
    return scheme
