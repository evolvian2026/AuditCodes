"""Wire protocol between the harness and a generated driver.

Input (stdin) is a newline-separated token stream that is trivial to parse in C:

* ``int``/``long``  -> decimal token
* ``double``        -> Python ``repr`` (``1.5``, ``1e-09``) — parseable by strtod & friends
* ``bool``          -> ``1`` / ``0``
* ``string``        -> byte length, newline, then exactly that many UTF-8 bytes
* ``list<T>``       -> element count, then each element

Output (stdout) is one JSON value: every language can *emit* JSON easily even though parsing it in
C is not worth the trouble. Doubles are compared with a tolerance downstream.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import IOSpec
from ..types import TypeSpec, ValueTypeError, coerce


class OutputDecodeError(ValueError):
    """The driver's stdout was not a JSON value of the declared return type."""


def encode_value(spec: TypeSpec, value: Any) -> bytes:
    out: list[bytes] = []
    _encode(spec, coerce(spec, value), out)
    return b"\n".join(out) + b"\n"


def encode_args(io_spec: IOSpec, args: list[Any]) -> bytes:
    if len(args) != len(io_spec.params):
        raise ValueTypeError(f"expected {len(io_spec.params)} argument(s), got {len(args)}")
    out: list[bytes] = []
    for param, value in zip(io_spec.params, args):
        _encode(param.type_spec, coerce(param.type_spec, value, param.name), out)
    return b"\n".join(out) + b"\n"


def _encode(spec: TypeSpec, value: Any, out: list[bytes]) -> None:
    kind = spec.kind
    if kind == "list":
        out.append(str(len(value)).encode())
        for v in value:
            _encode(spec.elem, v, out)
    elif kind == "string":
        data = value.encode("utf-8")
        out.append(str(len(data)).encode())
        out.append(data)
    elif kind == "bool":
        out.append(b"1" if value else b"0")
    elif kind == "double":
        out.append(repr(float(value)).encode())
    else:
        out.append(str(value).encode())


def decode_output(spec: TypeSpec, stdout: bytes) -> Any:
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise OutputDecodeError("driver produced no output")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise OutputDecodeError(f"driver output is not valid JSON: {e.msg} (output starts {text[:80]!r})") from e
    try:
        return coerce(spec, raw, "output")
    except ValueTypeError as e:
        raise OutputDecodeError(str(e)) from e
