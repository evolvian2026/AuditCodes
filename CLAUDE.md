# AuditCodes — notes for Claude Code

- Python 3.11+, deps in `pyproject.toml`; use `.venv/bin/python -m pytest` to run tests.
- Correctness of code is decided **only** by execution through `auditcodes/exec/harness.py`;
  never add a code path where an LLM's opinion is recorded as a pass/fail verdict.
- Test cases are language-agnostic JSON validated against `IOSpec`; drivers are generated from the
  spec (`auditcodes/exec/drivers/`), never hand-written per question.
- New type-language features must be added to `types.py`, `protocol.py`, all five driver
  generators and `tests/problems.py` together.
- `tests/problems.py` holds reference problems with solutions in all five languages; extend it
  when a new type shape or convention is supported.
