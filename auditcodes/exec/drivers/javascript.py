from __future__ import annotations

from ...models import IOSpec
from ...types import TypeSpec

_DRIVER = '''\

// ---- generated driver (AuditCodes) ----
const _fs = require("fs");
const _buf = _fs.readFileSync(0);
let _pos = 0;
function _ws(c) { return c === 32 || c === 9 || c === 13 || c === 10; }
function _tok() {
  while (_pos < _buf.length && _ws(_buf[_pos])) _pos++;
  const s = _pos;
  while (_pos < _buf.length && !_ws(_buf[_pos])) _pos++;
  return _buf.toString("latin1", s, _pos);
}
function _read_int() { return Number(_tok()); }
function _read_long() { return Number(_tok()); }
function _read_double() { return Number(_tok()); }
function _read_bool() { return _tok() === "1"; }
function _read_string() {
  const n = Number(_tok());
  _pos += 1;
  const s = _buf.toString("utf8", _pos, _pos + n);
  _pos += n;
  return s;
}
'''

_JS_TYPES = {"int": "number", "long": "number", "double": "number", "bool": "boolean", "string": "string"}


def _js_type(t: TypeSpec) -> str:
    return _JS_TYPES[t.kind] if not t.is_list else f"{_js_type(t.elem)}[]"


def signature(io_spec: IOSpec) -> str:
    params = ", ".join(p.name for p in io_spec.params)
    doc = "".join(f" * @param {{{_js_type(p.type_spec)}}} {p.name}\n" for p in io_spec.params)
    return f"/**\n{doc} * @return {{{_js_type(io_spec.return_spec)}}}\n */\nvar {io_spec.function_name} = function({params}) {{"


def generate(io_spec: IOSpec, solution_code: str) -> dict[str, str]:
    from . import collect_list_types

    parts = [solution_code if solution_code.endswith("\n") else solution_code + "\n", _DRIVER]
    for t in collect_list_types(io_spec):
        parts.append(
            f"function _read_{t.mangled}() {{\n"
            f"  const n = _read_int();\n"
            f"  const a = new Array(n);\n"
            f"  for (let i = 0; i < n; i++) a[i] = _read_{t.elem.mangled}();\n"
            f"  return a;\n}}\n"
        )
    name = io_spec.function_name
    lines = ["(function () {"]
    for p in io_spec.params:
        lines.append(f"  const {p.name} = _read_{p.type_spec.mangled}();")
    lines.append(f'  const _fn = (typeof {name} === "function") ? {name} : (function () {{ const s = new Solution(); return s.{name}.bind(s); }})();')
    args = ", ".join(p.name for p in io_spec.params)
    lines.append(f"  const _result = _fn({args});")
    lines.append('  process.stdout.write(JSON.stringify(_result) + "\\n");')
    lines.append("})();\n")
    parts.append("\n".join(lines))
    return {"main.js": "".join(parts)}
