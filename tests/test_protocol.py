import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from auditcodes.exec.protocol import OutputDecodeError, decode_output, encode_args, encode_value
from auditcodes.models import IOSpec, Param
from auditcodes.types import ValueTypeError, parse_type


def test_scalars():
    assert encode_value(parse_type("int"), 42) == b"42\n"
    assert encode_value(parse_type("long"), -(2**40)) == b"-1099511627776\n"
    assert encode_value(parse_type("bool"), True) == b"1\n"
    assert encode_value(parse_type("bool"), False) == b"0\n"
    assert encode_value(parse_type("double"), 1.5) == b"1.5\n"


def test_strings_are_length_prefixed_in_bytes():
    assert encode_value(parse_type("string"), "ab c") == b"4\nab c\n"
    assert encode_value(parse_type("string"), "") == b"0\n\n"
    assert encode_value(parse_type("string"), "é") == b"2\n\xc3\xa9\n"
    assert encode_value(parse_type("string"), "a\nb") == b"3\na\nb\n"


def test_lists():
    assert encode_value(parse_type("list<int>"), [1, 2]) == b"2\n1\n2\n"
    assert encode_value(parse_type("list<int>"), []) == b"0\n"
    assert encode_value(parse_type("list<list<string>>"), [["x"], []]) == b"2\n1\n1\nx\n0\n"


def test_encode_args_checks_arity_and_types():
    spec = IOSpec(function_name="f", params=[Param(name="a", type="int"), Param(name="s", type="string")], return_type="int")
    assert encode_args(spec, [1, "hi"]) == b"1\n2\nhi\n"
    with pytest.raises(ValueTypeError):
        encode_args(spec, [1])
    with pytest.raises(ValueTypeError, match="s"):
        encode_args(spec, [1, 2])


def test_decode_output():
    assert decode_output(parse_type("list<int>"), b"[1,2]\n") == [1, 2]
    assert decode_output(parse_type("double"), b"1.0E10\n") == 1e10
    assert decode_output(parse_type("string"), b'"a\\nb"\n') == "a\nb"
    with pytest.raises(OutputDecodeError):
        decode_output(parse_type("int"), b"")
    with pytest.raises(OutputDecodeError):
        decode_output(parse_type("int"), b"undefined\n")
    with pytest.raises(OutputDecodeError):
        decode_output(parse_type("int"), b"[1]\n")


_SCALARS = {
    "int": st.integers(-(2**31), 2**31 - 1),
    "long": st.integers(-(2**63), 2**63 - 1),
    "double": st.floats(allow_nan=False, allow_infinity=False, width=64),
    "bool": st.booleans(),
    "string": st.text(max_size=12),
}


def _typed(type_name: str, strategy):
    return st.tuples(st.just(type_name), strategy)


typed_values = st.recursive(
    st.one_of(*[_typed(k, v) for k, v in _SCALARS.items()]),
    lambda inner: inner.flatmap(lambda tv: _typed(f"list<{tv[0]}>", st.lists(st.just(tv[1]), max_size=3))),
    max_leaves=4,
)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(typed_values)
def test_encoding_is_consistent_with_coercion(tv):
    type_, value = tv
    spec = parse_type(type_)
    data = encode_value(spec, value)
    assert data.endswith(b"\n")
    # nothing in the encoding should be ambiguous: re-decode with a tiny reference parser
    assert _reference_decode(spec, data) == value


def _reference_decode(spec, data: bytes):
    pos = 0

    def tok():
        nonlocal pos
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        s = pos
        while pos < len(data) and data[pos] not in b" \t\r\n":
            pos += 1
        return data[s:pos].decode()

    def read(t):
        nonlocal pos
        if t.is_list:
            return [read(t.elem) for _ in range(int(tok()))]
        if t.kind == "string":
            n = int(tok())
            pos += 1
            s = data[pos : pos + n].decode("utf-8")
            pos += n
            return s
        if t.kind == "bool":
            return tok() == "1"
        if t.kind == "double":
            return float(tok())
        return int(tok())

    return read(spec)
