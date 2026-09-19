# AuditCodes

Audits coding/programming questions with **execution-backed** checks: every claim about a sample
output, a hidden test, a driver or a solution is settled by compiling and running the code, never
by an LLM's reading of it. Findings are reviewed and approved before the corrected question bank is
exported as PDF / DOCX / JSON.

Supported solution languages: C, C++, Java, Python, JavaScript.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Executable core: canonical schema, sandboxed runner, language specs, generated drivers, test harness | **done** |
| 2 | PDF ingest (layout-aware recovery, template parser), stdio harness, extraction review web app | **done** |
| 3 | Rule catalog, execution-based audit, model-driven review, findings with patches, accept/reject UI | **done** |
| 4 | Oracle establishment, hidden-test generation (10-50 by difficulty), repair projection | **done** |
| 5 | Solutions and driver scaffolds in all five languages, each verified | **done** |
| 6 | PDF / DOCX / JSON / ZIP export | |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m auditcodes check-toolchains   # gcc, g++, javac/java, python3, node
.venv/bin/python -m pytest
```

The test suite compiles and runs reference solutions in all five languages; a language whose
toolchain is missing is skipped, not failed.

## Using it

```bash
export ANTHROPIC_API_KEY=...                        # enables the model-driven stages
.venv/bin/python -m auditcodes check-llm            # confirms the API is reachable
.venv/bin/python -m auditcodes serve                # http://127.0.0.1:8000
.venv/bin/python -m auditcodes extract bank.pdf -o questions.json --assets images/
.venv/bin/python -m auditcodes audit bank.pdf -o reports.json   # headless audit; --no-llm for execution checks only
```

`AUDITCODES_MODEL` overrides the model (default `claude-opus-5`).

Upload a question-bank PDF; each question is shown with every extracted component, a per-field
extraction confidence, and the warnings the extractor raised (a wrapped code line it re-joined,
typographic quotes inside code, a missing section...). Every field is editable in place. "Run
editorial" compiles the reference solution and runs it against all sample and hidden tests with the
question's time limit, in the sandbox. Jobs live under `data/jobs/<job id>/` as plain files:
`source.pdf`, `questions.json` (the canonical model — also downloadable as Export JSON), `assets/`.

The app is a local tool with no authentication; keep it on localhost or behind your own proxy.

### The audit

"Run audit" (per question) or "Audit all questions" (per document) produces findings against the
rule catalog in `auditcodes/audit/rules.py`. Two kinds:

- **execution** findings are established by compiling and running: the editorial does not build
  (`SOL-001`), only builds after typographic normalisation (`SOL-002`, with a verified patch),
  disagrees with a sample or hidden test (`SOL-003/004`), crashes or times out (`SOL-005/006`),
  duplicate hidden tests or tests that copy a sample (`HIDE-001/002`), too few tests (`HIDE-003`),
  inputs that violate the constraints (`HIDE-005`, via a model-written validator program that the
  sandbox runs on every input), and a driver whose scaffold does not work with the editorial's
  logic (`DRV-001/002`, via a model-spliced program the sandbox runs).
- **model** findings are proposals from Claude: clarity, missing information, constraint
  consistency, sample explanations, editorial quality, language-specific risks, cross-component
  consistency, difficulty assessment. A patch on code is compiled and run before it is shown and
  carries the result; a patch on prose is shown as a diff.

Every finding has a severity (blocker / major / minor / info), the component it concerns, evidence,
and — where a safe textual fix exists — a patch. Accept applies the patch to the question and
re-validates the model; reject and waive record the decision. Reports live in
`data/jobs/<job>/audit/<question id>.json`.

### Hidden-test generation

New hidden tests are only ever produced against an **oracle of two independent implementations**.
The editorial is one; Claude writes the other from the statement alone (it never sees the
editorial), in a language the question does not have yet. Only when both pass every existing test
is the pair trusted (`GEN-001` offers the second solution as a patch); otherwise `GEN-003` says
why and nothing is generated.

Inputs come from a Claude-written generator program run in the sandbox per category
(boundary-min, boundary-max, edge, structured, random-small, random-large, adversarial) and seed,
filtered by the constraint validator and de-duplicated against existing tests. Expected outputs
come from running both implementations; a case is kept only when they agree, disagreements are
reported (`GEN-004`), and an editorial that times out at the constraint ceiling is reported
(`SOL-006`). The target count is 15 / 25 / 40 for easy / medium / hard; generated cases get a
level from their category and points from the question's own scheme, and arrive as one
"append" patch that a reviewer can inspect case by case.

### Language completion

For every supported language the question lacks, Claude ports the verified editorial (same
algorithm, that language's conventions); the sandbox runs it against every test, existing and
generated, with the judge's per-language time allowance. It is offered (`LANG-001`, a verified
patch) only when it passes everything. A port that is correct but too slow is `LANG-003` — a
fairness signal about the time limit in that language — and one that cannot be made to pass is
`LANG-002`. A driver scaffold is then written per language in the house style of the existing
driver (`DRV-005`): the scaffold and its filled version come from one call, the filled program
must pass every test, and every fixed line of the scaffold must appear verbatim in it, so the
stub really is the only thing a candidate writes. Set `AUDITCODES_GENERATE_DRIVERS=0` to skip
scaffolds. Toolchains missing on the host are reported (`LANG-004`), never silently skipped.

The report also carries a **projection**: what remains after every execution-verified patch is
accepted (re-running the execution audit on a working copy), with an "Accept all verified
patches" action.

Without an API key the execution-based checks still run in full; the model stages are skipped
and the report says so.

### The input format

The parser targets the "Coding Ques" export: `Q.n <title> <difficulty> · Time limit`, a
`DBNO / Area / LOD` line, then the sections *Problem Statement, Input Explanation, Output
Explanation, Constraints, Sample Test Cases, Sample Test Case Explanation, Driver Code — <lang>,
Editorial, Code Editorial — <lang>, Hidden Test Cases* with `Example n` / `Test Case n Level · Points`
blocks. `DBNO` becomes the question id and is never modified. `LOD` 33/66/99 maps to easy/medium/
hard. Figures inside sections are extracted and kept in place as Markdown images.

Text is recovered from span geometry, not `get_text()`: inline code/emphasis is put back into its
sentence, code indentation is inferred from x offsets (whole indentation levels, so a generator's
19.3pt step does not turn into 4/8/13/17 spaces), lines that wrapped at the margin are re-joined
only when the previous line was full, running headers/footers are dropped, and a code block that
crosses a page break keeps its base column.

## How the core works

- **`auditcodes/ingest/`** — `layout.py` rebuilds lines from PDF spans; `template.py` is the
  deterministic parser for the export format; `normalize.py` proposes fixes for typographic
  characters in code (the extractor itself stays faithful and only *warns*).
- **`auditcodes/app/`** — FastAPI + Jinja + HTMX review app; `store.py` keeps one directory per
  job, `edits.py` applies a reviewer's change to a dotted field path and re-validates the model.

- **`auditcodes/models.py`** — the canonical `Question` schema. Test cases are language-agnostic
  JSON (`args`, `expected`) validated against a typed `IOSpec`; one suite drives all languages.
- **`auditcodes/types.py`** — the type grammar `int | long | double | bool | string | list<T>`,
  with strict coercion (32/64-bit ranges, no bool-as-int, finite doubles).
- **`auditcodes/exec/drivers/`** — drivers are *generated* from the `IOSpec` for each language,
  so driver correctness is a diff against a known-good artifact, not a judgement call.
  `drivers.signature(lang, spec)` gives the exact signature a solution must implement.
- **`auditcodes/exec/protocol.py`** — stdin wire format (length-prefixed strings, counted lists —
  trivial to parse in C) and JSON on stdout.
- **`auditcodes/exec/runner.py`** — `LocalRunner`: rlimits (CPU, address space, file size, core),
  scrubbed environment, private cwd, process-group kill on wall timeout, `unshare -n` network
  isolation when available. `Runner` is an ABC so a Docker runner can be swapped in.
- **`auditcodes/exec/harness.py`** — build once, run every case, classify each as
  passed / failed / runtime_error / timeout / memory_limit / output_limit / bad_output.
  `run_suite` drives a function-mode solution through its generated driver; `run_stdio_suite`
  runs a complete program on raw stdin/stdout cases with judge-style output comparison.

### Solution conventions

Solutions are normalised to one convention per language before verification:

| Language | Convention |
|---|---|
| Python | `def name(...)` at module level, or `class Solution` with method `name` |
| JavaScript | `var name = function(...)` / `function name(...)`, or `class Solution` |
| C++ | `class Solution { public: R name(...); }`, `vector<T>` / `string` |
| Java | `class Solution { public R name(...) }`, `list<T>` is `T[]` |
| C | free function; `list<T>` → `T* name, int nameSize`; nested adds `int* nameColSize`; list returns take `int* returnSize` (+ `int** returnColumnSizes`) |

### Sandbox note

`LocalRunner` is containment for trusted question banks, not isolation: code still runs as the
current user. Use a dedicated worker or swap in a container-backed `Runner` for untrusted input.
