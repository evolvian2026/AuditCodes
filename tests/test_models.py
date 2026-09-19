import pytest
from pydantic import ValidationError

from auditcodes.models import IOSpec, Language, Param, Question, TestCase
from auditcodes.types import ValueTypeError


def _q(**overrides):
    base = dict(
        id="q1",
        title="Add",
        description_md="Return a + b.",
        io_spec=IOSpec(function_name="add", params=[Param(name="a", type="int"), Param(name="b", type="int")], return_type="int"),
        samples=[TestCase(args=[1, 2], expected=3)],
    )
    base.update(overrides)
    return Question(**base)


def test_question_roundtrips_json():
    q = _q(solutions={Language.PYTHON: "def add(a, b): return a + b"})
    data = q.model_dump_json()
    assert Question.model_validate_json(data) == q


def test_question_rejects_case_that_does_not_match_spec():
    with pytest.raises((ValidationError, ValueTypeError), match="samples\\[0\\].args.b"):
        _q(samples=[TestCase(args=[1, "2"], expected=3)])
    with pytest.raises((ValidationError, ValueTypeError), match="hidden_tests\\[0\\].expected"):
        _q(hidden_tests=[TestCase(args=[1, 2], expected="3")])
    with pytest.raises((ValidationError, ValueTypeError), match="expected 2 argument"):
        _q(samples=[TestCase(args=[1], expected=3)])


def test_iospec_validation():
    with pytest.raises(ValidationError):
        IOSpec(function_name="2bad", params=[], return_type="int")
    with pytest.raises(ValidationError):
        IOSpec(function_name="f", params=[Param(name="a", type="int"), Param(name="a", type="int")], return_type="int")
    with pytest.raises(ValidationError):
        IOSpec(function_name="f", params=[], return_type="list<>")
    spec = IOSpec(function_name="f", params=[Param(name="a", type="list< int >")], return_type="int")
    assert spec.params[0].type == "list<int>"
