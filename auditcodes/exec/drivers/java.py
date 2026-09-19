from __future__ import annotations

from ...models import IOSpec
from ...types import TypeSpec

_PRELUDE = r'''import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class Main {
    static byte[] buf;
    static int pos = 0;
    static StringBuilder out = new StringBuilder();

    static boolean ws(byte c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
    static String tok() {
        while (pos < buf.length && ws(buf[pos])) pos++;
        int s = pos;
        while (pos < buf.length && !ws(buf[pos])) pos++;
        return new String(buf, s, pos - s, StandardCharsets.US_ASCII);
    }
    static int read_int() { return Integer.parseInt(tok()); }
    static long read_long() { return Long.parseLong(tok()); }
    static double read_double() { return Double.parseDouble(tok()); }
    static boolean read_bool() { return tok().equals("1"); }
    static String read_string() {
        int n = Integer.parseInt(tok());
        pos += 1;
        String s = new String(buf, pos, n, StandardCharsets.UTF_8);
        pos += n;
        return s;
    }
    static void write_int(int v) { out.append(v); }
    static void write_long(long v) { out.append(v); }
    static void write_double(double v) { out.append(Double.toString(v)); }
    static void write_bool(boolean v) { out.append(v ? "true" : "false"); }
    static void write_string(String s) {
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c)); else out.append(c);
            }
        }
        out.append('"');
    }
'''

_JAVA_TYPES = {"int": "int", "long": "long", "double": "double", "bool": "boolean", "string": "String"}


def java_type(t: TypeSpec) -> str:
    return _JAVA_TYPES[t.kind] if not t.is_list else f"{java_type(t.elem)}[]"


def signature(io_spec: IOSpec) -> str:
    params = ", ".join(f"{java_type(p.type_spec)} {p.name}" for p in io_spec.params)
    return f"class Solution {{\n    public {java_type(io_spec.return_spec)} {io_spec.function_name}({params}) {{ ... }}\n}}"


def generate(io_spec: IOSpec, solution_code: str) -> dict[str, str]:
    from . import collect_list_types

    parts = [_PRELUDE]
    for t in collect_list_types(io_spec):
        jt, et = java_type(t), java_type(t.elem)
        new_expr = f"new {java_type(t.base)}[n]" + "[]" * t.elem.depth
        parts.append(
            f"\n    static {jt} read_{t.mangled}() {{\n"
            f"        int n = read_int();\n"
            f"        {jt} a = {new_expr};\n"
            f"        for (int i = 0; i < n; i++) a[i] = read_{t.elem.mangled}();\n"
            f"        return a;\n    }}\n"
            f"    static void write_{t.mangled}({jt} a) {{\n"
            f"        out.append('[');\n"
            f"        for (int i = 0; i < a.length; i++) {{\n"
            f"            if (i > 0) out.append(',');\n"
            f"            write_{t.elem.mangled}(a[i]);\n"
            f"        }}\n"
            f"        out.append(']');\n    }}\n"
        )
    lines = ["\n    public static void main(String[] args) throws IOException {", "        buf = System.in.readAllBytes();"]
    for p in io_spec.params:
        lines.append(f"        {java_type(p.type_spec)} {p.name} = read_{p.type_spec.mangled}();")
    args = ", ".join(p.name for p in io_spec.params)
    lines.append("        Solution sol = new Solution();")
    lines.append(f"        {java_type(io_spec.return_spec)} result = sol.{io_spec.function_name}({args});")
    lines.append(f"        write_{io_spec.return_spec.mangled}(result);")
    lines.append("        out.append('\\n');")
    lines.append("        System.out.write(out.toString().getBytes(StandardCharsets.UTF_8));")
    lines.append("        System.out.flush();")
    lines.append("    }")
    lines.append("}\n")
    parts.append("\n".join(lines))
    return {"Main.java": "".join(parts), "Solution.java": solution_code if solution_code.endswith("\n") else solution_code + "\n"}
