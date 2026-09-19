import pytest

from auditcodes.exec.compare import values_match
from auditcodes.models import Checker
from auditcodes.types import parse_type


def test_exact():
    assert values_match([1, 2], [1, 2], parse_type("list<int>"))
    assert not values_match([1, 2], [2, 1], parse_type("list<int>"))
    assert not values_match([1], [1, 1], parse_type("list<int>"))
    assert values_match("a", "a", parse_type("string"))


def test_doubles_use_tolerance():
    assert values_match(0.1 + 0.2, 0.3, parse_type("double"))
    assert values_match([1e9], [1e9 + 1e-3], parse_type("list<double>"))
    assert not values_match(1.0, 1.1, parse_type("double"))


def test_unordered():
    t = parse_type("list<list<int>>")
    assert values_match([[1, 2], [3]], [[3], [1, 2]], t, Checker.UNORDERED)
    assert not values_match([[1, 2], [3]], [[3], [2, 1]], t, Checker.UNORDERED)
    assert not values_match([1], [1, 1], parse_type("list<int>"), Checker.UNORDERED)
    assert values_match([0.5, 1.5], [1.5, 0.5 + 1e-9], parse_type("list<double>"), Checker.UNORDERED)


def test_custom_not_supported_yet():
    with pytest.raises(NotImplementedError):
        values_match(1, 1, parse_type("int"), Checker.CUSTOM)
