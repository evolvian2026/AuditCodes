import pytest

from auditcodes.types import InvalidTypeError, TypeSpec, ValueTypeError, coerce, parse_type


@pytest.mark.parametrize("text", ["int", "long", "double", "bool", "string", "list<int>", "list<list<string>>", "list< list<int> >"])
def test_parse_roundtrip(text):
    spec = parse_type(text)
    assert str(spec) == text.replace(" ", "")
    assert parse_type(str(spec)) == spec


@pytest.mark.parametrize("text", ["", "integer", "list", "list<int", "list<>", "int>", "list<int>>", "List<int>", "map<int>"])
def test_parse_rejects(text):
    with pytest.raises(InvalidTypeError):
        parse_type(text)


def test_properties():
    t = parse_type("list<list<double>>")
    assert t.depth == 2
    assert t.base == TypeSpec("double")
    assert t.mangled == "list_list_double"
    assert not parse_type("int").is_list
    assert parse_type("int").depth == 0


def test_coerce_accepts():
    assert coerce(parse_type("int"), 5) == 5
    assert coerce(parse_type("long"), 2**40) == 2**40
    assert coerce(parse_type("double"), 3) == 3.0
    assert isinstance(coerce(parse_type("double"), 3), float)
    assert coerce(parse_type("bool"), True) is True
    assert coerce(parse_type("string"), "x") == "x"
    assert coerce(parse_type("list<list<int>>"), [(1, 2), []]) == [[1, 2], []]


@pytest.mark.parametrize(
    "type_, value",
    [
        ("int", True),
        ("int", 3.0),
        ("int", 2**31),
        ("int", -(2**31) - 1),
        ("long", 2**63),
        ("double", "1.5"),
        ("double", float("inf")),
        ("double", float("nan")),
        ("bool", 1),
        ("string", 5),
        ("list<int>", 5),
        ("list<int>", [1, "2"]),
        ("list<list<int>>", [1]),
    ],
)
def test_coerce_rejects(type_, value):
    with pytest.raises(ValueTypeError):
        coerce(parse_type(type_), value)


def test_coerce_error_names_path():
    with pytest.raises(ValueTypeError, match=r"nums\[1\]\[0\]"):
        coerce(parse_type("list<list<int>>"), [[1], ["x"]], "nums")
