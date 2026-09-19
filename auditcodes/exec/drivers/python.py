from __future__ import annotations

from ...models import IOSpec
from ...types import TypeSpec

_PRELUDE = '''\
import json
import sys

import solution as _solution

_buf = sys.stdin.buffer.read()
_pos = 0
_WS = b" \\t\\r\\n"


def _tok():
    global _pos
    n = len(_buf)
    while _pos < n and _buf[_pos] in _WS:
        _pos += 1
    s = _pos
    while _pos < n and _buf[_pos] not in _WS:
        _pos += 1
    return _buf[s:_pos].decode("ascii")


def _read_int():
    return int(_tok())


_read_long = _read_int


def _read_double():
    return float(_tok())


def _read_bool():
    return _tok() == "1"


def _read_string():
    global _pos
    n = int(_tok())
    _pos += 1
    s = _buf[_pos:_pos + n].decode("utf-8")
    _pos += n
    return s
'''

_PY_TYPES = {"int": "int", "long": "int", "double": "float", "bool": "bool", "string": "str"}


def _py_type(t: TypeSpec) -> str:
    return _PY_TYPES[t.kind] if not t.is_list else f"list[{_py_type(t.elem)}]"


def signature(io_spec: IOSpec) -> str:
    params = ", ".join(f"{p.name}: {_py_type(p.type_spec)}" for p in io_spec.params)
    return f"def {io_spec.function_name}({params}) -> {_py_type(io_spec.return_spec)}:"


def generate(io_spec: IOSpec, solution_code: str) -> dict[str, str]:
    from . import collect_list_types

    parts = [_PRELUDE]
    for t in collect_list_types(io_spec):
        parts.append(
            f"\n\ndef _read_{t.mangled}():\n"
            f"    n = _read_int()\n"
            f"    return [_read_{t.elem.mangled}() for _ in range(n)]\n"
        )
    name = io_spec.function_name
    lines = ["\n\ndef _main():"]
    for p in io_spec.params:
        lines.append(f"    {p.name} = _read_{p.type_spec.mangled}()")
    lines.append(f"    _fn = getattr(_solution, {name!r}, None)")
    lines.append(f"    if _fn is None:")
    lines.append(f"        _fn = getattr(_solution.Solution(), {name!r})")
    args = ", ".join(p.name for p in io_spec.params)
    lines.append(f"    _result = _fn({args})")
    lines.append('    sys.stdout.write(json.dumps(_result, ensure_ascii=False, separators=(",", ":")))')
    lines.append('    sys.stdout.write("\\n")')
    lines.append("\n\n_main()\n")
    parts.append("\n".join(lines))
    return {"solution.py": _ensure_newline(solution_code), "main.py": "".join(parts)}


def _ensure_newline(code: str) -> str:
    return code if code.endswith("\n") else code + "\n"
