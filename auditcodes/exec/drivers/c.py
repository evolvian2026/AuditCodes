from __future__ import annotations

from ...models import IOSpec, Param
from ...types import TypeSpec

_HEADER = """#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <limits.h>
#include <math.h>

"""

_DRIVER = r'''
/* ---- generated driver (AuditCodes) ---- */
static char* _buf;
static size_t _len = 0, _pos = 0;
static int _ws(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
static char* _tok(void) {
    while (_pos < _len && _ws(_buf[_pos])) _pos++;
    size_t s = _pos;
    while (_pos < _len && !_ws(_buf[_pos])) _pos++;
    size_t n = _pos - s;
    char* t = (char*)malloc(n + 1);
    memcpy(t, _buf + s, n);
    t[n] = 0;
    return t;
}
static int _read_int(void) { return (int)strtol(_tok(), NULL, 10); }
static long long _read_long(void) { return strtoll(_tok(), NULL, 10); }
static double _read_double(void) { return strtod(_tok(), NULL); }
static bool _read_bool(void) { return _tok()[0] == '1'; }
static char* _read_string(void) {
    size_t n = (size_t)strtoll(_tok(), NULL, 10);
    _pos += 1;
    char* s = (char*)malloc(n + 1);
    memcpy(s, _buf + _pos, n);
    s[n] = 0;
    _pos += n;
    return s;
}
static void _write_int(int v) { printf("%d", v); }
static void _write_long(long long v) { printf("%lld", v); }
static void _write_double(double v) { printf("%.17g", v); }
static void _write_bool(bool v) { fputs(v ? "true" : "false", stdout); }
static void _write_string(const char* s) {
    putchar('"');
    for (const unsigned char* p = (const unsigned char*)s; *p; p++) {
        unsigned char c = *p;
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

_C_TYPES = {"int": "int", "long": "long long", "double": "double", "bool": "bool", "string": "char*"}
MAX_DEPTH = 2


def c_type(t: TypeSpec) -> str:
    """``int`` -> ``int``, ``list<int>`` -> ``int*``, ``list<list<string>>`` -> ``char***``."""
    return _C_TYPES[t.base.kind] + "*" * t.depth


def _check(t: TypeSpec, what: str) -> None:
    from . import UnsupportedSignatureError

    if t.depth > MAX_DEPTH:
        raise UnsupportedSignatureError(f"C drivers support at most list<list<T>>; {what} is {t}")


def _param_decls(p: Param) -> list[str]:
    t = p.type_spec
    decls = [f"{c_type(t)} {p.name}"]
    if t.depth >= 1:
        decls.append(f"int {p.name}Size")
    if t.depth == 2:
        decls.append(f"int* {p.name}ColSize")
    return decls


def _return_decls(t: TypeSpec) -> list[str]:
    decls = []
    if t.depth >= 1:
        decls.append("int* returnSize")
    if t.depth == 2:
        decls.append("int** returnColumnSizes")
    return decls


def signature(io_spec: IOSpec) -> str:
    for p in io_spec.params:
        _check(p.type_spec, f"parameter {p.name}")
    _check(io_spec.return_spec, "return type")
    decls = [d for p in io_spec.params for d in _param_decls(p)] + _return_decls(io_spec.return_spec)
    return f"{c_type(io_spec.return_spec)} {io_spec.function_name}({', '.join(decls)});"


def generate(io_spec: IOSpec, solution_code: str) -> dict[str, str]:
    from . import collect_list_types

    signature(io_spec)  # validates depth
    parts = [_HEADER, solution_code if solution_code.endswith("\n") else solution_code + "\n", _DRIVER]
    for t in collect_list_types(io_spec):
        ct, et = c_type(t), c_type(t.elem)
        if t.depth == 1:
            parts.append(
                f"static {ct} _read_{t.mangled}(int* n) {{\n"
                f"    *n = _read_int();\n"
                f"    {ct} a = ({ct})malloc((size_t)(*n > 0 ? *n : 1) * sizeof({et}));\n"
                f"    for (int i = 0; i < *n; i++) a[i] = _read_{t.elem.mangled}();\n"
                f"    return a;\n}}\n"
                f"static void _write_{t.mangled}({ct} a, int n) {{\n"
                f"    putchar('[');\n"
                f"    for (int i = 0; i < n; i++) {{ if (i) putchar(','); _write_{t.elem.mangled}(a[i]); }}\n"
                f"    putchar(']');\n}}\n"
            )
        else:
            parts.append(
                f"static {ct} _read_{t.mangled}(int* n, int** cols) {{\n"
                f"    *n = _read_int();\n"
                f"    {ct} a = ({ct})malloc((size_t)(*n > 0 ? *n : 1) * sizeof({et}));\n"
                f"    *cols = (int*)malloc((size_t)(*n > 0 ? *n : 1) * sizeof(int));\n"
                f"    for (int i = 0; i < *n; i++) a[i] = _read_{t.elem.mangled}(&(*cols)[i]);\n"
                f"    return a;\n}}\n"
                f"static void _write_{t.mangled}({ct} a, int n, int* cols) {{\n"
                f"    putchar('[');\n"
                f"    for (int i = 0; i < n; i++) {{ if (i) putchar(','); _write_{t.elem.mangled}(a[i], cols[i]); }}\n"
                f"    putchar(']');\n}}\n"
            )
    lines = [
        "int main(void) {",
        "    { size_t cap = 1 << 16; _buf = (char*)malloc(cap); size_t k;",
        "      while ((k = fread(_buf + _len, 1, cap - _len, stdin)) > 0) { _len += k; if (_len == cap) { cap *= 2; _buf = (char*)realloc(_buf, cap); } } }",
    ]
    call_args: list[str] = []
    for p in io_spec.params:
        t, n = p.type_spec, p.name
        if t.depth == 0:
            lines.append(f"    {c_type(t)} {n} = _read_{t.mangled}();")
            call_args.append(n)
        elif t.depth == 1:
            lines.append(f"    int {n}Size; {c_type(t)} {n} = _read_{t.mangled}(&{n}Size);")
            call_args += [n, f"{n}Size"]
        else:
            lines.append(f"    int {n}Size; int* {n}ColSize; {c_type(t)} {n} = _read_{t.mangled}(&{n}Size, &{n}ColSize);")
            call_args += [n, f"{n}Size", f"{n}ColSize"]
    rt = io_spec.return_spec
    fn = io_spec.function_name
    if rt.depth == 0:
        lines.append(f"    {c_type(rt)} _result = {fn}({', '.join(call_args)});")
        lines.append(f"    _write_{rt.mangled}(_result);")
    elif rt.depth == 1:
        lines.append(f"    int _resultSize = 0;")
        lines.append(f"    {c_type(rt)} _result = {fn}({', '.join(call_args + ['&_resultSize'])});")
        lines.append(f"    _write_{rt.mangled}(_result, _resultSize);")
    else:
        lines.append(f"    int _resultSize = 0; int* _resultColSizes = NULL;")
        lines.append(f"    {c_type(rt)} _result = {fn}({', '.join(call_args + ['&_resultSize', '&_resultColSizes'])});")
        lines.append(f"    _write_{rt.mangled}(_result, _resultSize, _resultColSizes);")
    lines.append("    putchar('\\n');")
    lines.append("    return 0;")
    lines.append("}\n")
    parts.append("\n".join(lines))
    return {"main.c": "".join(parts)}
