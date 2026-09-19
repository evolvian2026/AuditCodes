from __future__ import annotations

from ...models import IOSpec
from ...types import TypeSpec

_HEADER = "#include <bits/stdc++.h>\nusing namespace std;\n\n"

_DRIVER = r'''
// ---- generated driver (AuditCodes) ----
static string _buf;
static size_t _pos = 0;
static inline bool _ws(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
static string _tok() {
    while (_pos < _buf.size() && _ws(_buf[_pos])) _pos++;
    size_t s = _pos;
    while (_pos < _buf.size() && !_ws(_buf[_pos])) _pos++;
    return _buf.substr(s, _pos - s);
}
static int _read_int() { return stoi(_tok()); }
static long long _read_long() { return stoll(_tok()); }
static double _read_double() { return stod(_tok()); }
static bool _read_bool() { return _tok() == "1"; }
static string _read_string() {
    size_t n = (size_t)stoll(_tok());
    _pos += 1;
    string s = _buf.substr(_pos, n);
    _pos += n;
    return s;
}
static void _write_int(int v) { printf("%d", v); }
static void _write_long(long long v) { printf("%lld", v); }
static void _write_double(double v) { printf("%.17g", v); }
static void _write_bool(bool v) { fputs(v ? "true" : "false", stdout); }
static void _write_string(const string& s) {
    putchar('"');
    for (unsigned char c : s) {
        switch (c) {
            case '"': fputs("\\\"", stdout); break;
            case '\\': fputs("\\\\", stdout); break;
            case '\n': fputs("\\n", stdout); break;
            case '\r': fputs("\\r", stdout); break;
            case '\t': fputs("\\t", stdout); break;
            default:
                if (c < 0x20) printf("\\u%04x", c); else putchar(c);
        }
    }
    putchar('"');
}
'''

_CPP_TYPES = {"int": "int", "long": "long long", "double": "double", "bool": "bool", "string": "string"}


def cpp_type(t: TypeSpec) -> str:
    return _CPP_TYPES[t.kind] if not t.is_list else f"vector<{cpp_type(t.elem)}>"


def _param_decl(p) -> str:
    t = p.type_spec
    return f"{cpp_type(t)}& {p.name}" if t.is_list or t.kind == "string" else f"{cpp_type(t)} {p.name}"


def signature(io_spec: IOSpec) -> str:
    params = ", ".join(_param_decl(p) for p in io_spec.params)
    return f"class Solution {{\npublic:\n    {cpp_type(io_spec.return_spec)} {io_spec.function_name}({params});\n}};"


def generate(io_spec: IOSpec, solution_code: str) -> dict[str, str]:
    from . import collect_list_types

    parts = [_HEADER, solution_code if solution_code.endswith("\n") else solution_code + "\n", _DRIVER]
    for t in collect_list_types(io_spec):
        ct, et = cpp_type(t), cpp_type(t.elem)
        parts.append(
            f"static {ct} _read_{t.mangled}() {{\n"
            f"    int n = _read_int();\n"
            f"    {ct} v;\n"
            f"    v.reserve(n);\n"
            f"    for (int i = 0; i < n; i++) v.push_back(_read_{t.elem.mangled}());\n"
            f"    return v;\n}}\n"
            f"static void _write_{t.mangled}(const {ct}& v) {{\n"
            f"    putchar('[');\n"
            f"    for (size_t i = 0; i < v.size(); i++) {{\n"
            f"        if (i) putchar(',');\n"
            f"        _write_{t.elem.mangled}(({et})v[i]);\n"
            f"    }}\n"
            f"    putchar(']');\n}}\n"
        )
    lines = [
        "int main() {",
        "    { char tmp[1 << 16]; size_t k; while ((k = fread(tmp, 1, sizeof tmp, stdin)) > 0) _buf.append(tmp, k); }",
    ]
    for p in io_spec.params:
        lines.append(f"    {cpp_type(p.type_spec)} {p.name} = _read_{p.type_spec.mangled}();")
    args = ", ".join(p.name for p in io_spec.params)
    lines.append("    Solution _sol;")
    lines.append(f"    {cpp_type(io_spec.return_spec)} _result = _sol.{io_spec.function_name}({args});")
    lines.append(f"    _write_{io_spec.return_spec.mangled}(_result);")
    lines.append("    putchar('\\n');")
    lines.append("    return 0;")
    lines.append("}\n")
    parts.append("\n".join(lines))
    return {"main.cpp": "".join(parts)}
