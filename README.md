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
| 2 | PDF ingest, segmentation, LLM structuring, extraction review UI | next |
| 3 | Rule catalog, static audit, dynamic verification, review UI | |
| 4 | Oracle establishment, hidden-test generation, repair loop | |
| 5 | Missing-language solution generation | |
| 6 | PDF / DOCX / JSON / ZIP export | |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m auditcodes check-toolchains   # gcc, g++, javac/java, python3, node
.venv/bin/python -m pytest
```

The test suite compiles and runs reference solutions in all five languages; a language whose
toolchain is missing is skipped, not failed.

## How the core works

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
