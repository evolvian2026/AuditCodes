"""Canonical question model.

Every pipeline stage reads and writes this schema: the PDF is parsed *into* it, audits operate
*on* it, exports render *from* it. Nothing passes free-form text between stages.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .types import TypeSpec, ValueTypeError, coerce, parse_type

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Language(str, Enum):
    C = "c"
    CPP = "cpp"
    JAVA = "java"
    PYTHON = "python"
    JAVASCRIPT = "javascript"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class IOMode(str, Enum):
    """How a solution is exercised.

    ``stdio``: the solution is a complete program; tests are raw stdin/stdout text.
    ``function``: the solution implements ``IOSpec``; tests are typed JSON args/expected and the
    driver is generated.
    """

    STDIO = "stdio"
    FUNCTION = "function"


class Checker(str, Enum):
    """How a solution's output is compared with the expected value."""

    EXACT = "exact"  # structural equality; doubles always use a tolerance
    UNORDERED = "unordered"  # top-level list compared as a multiset
    FLOAT_TOL = "float_tol"  # alias of EXACT kept for explicitness in question metadata
    CUSTOM = "custom"  # requires a checker program (later phase)


class TestCategory(str, Enum):
    __test__ = False

    SAMPLE = "sample"
    ORIGINAL = "original"  # hidden test carried over from the source document
    BOUNDARY_MIN = "boundary_min"
    BOUNDARY_MAX = "boundary_max"
    EDGE = "edge"
    STRUCTURED = "structured"
    RANDOM_SMALL = "random_small"
    RANDOM_LARGE = "random_large"
    ADVERSARIAL = "adversarial"


class TestOrigin(str, Enum):
    __test__ = False

    SOURCE = "source"  # extracted from the uploaded document
    GENERATED = "generated"  # produced by the test generator from the verified oracle
    MANUAL = "manual"  # entered by a reviewer


class Param(BaseModel):
    name: str
    type: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _IDENTIFIER.match(v):
            raise ValueError(f"parameter name {v!r} is not a valid identifier")
        return v

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        return str(parse_type(v))

    @property
    def type_spec(self) -> TypeSpec:
        return parse_type(self.type)


class IOSpec(BaseModel):
    """The function contract a solution must implement. Drivers are generated from this."""

    function_name: str
    params: list[Param]
    return_type: str

    @field_validator("function_name")
    @classmethod
    def _valid_function_name(cls, v: str) -> str:
        if not _IDENTIFIER.match(v):
            raise ValueError(f"function name {v!r} is not a valid identifier")
        return v

    @field_validator("return_type")
    @classmethod
    def _valid_return_type(cls, v: str) -> str:
        return str(parse_type(v))

    @model_validator(mode="after")
    def _unique_param_names(self) -> "IOSpec":
        names = [p.name for p in self.params]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate parameter names: {sorted(dupes)}")
        return self

    @property
    def return_spec(self) -> TypeSpec:
        return parse_type(self.return_type)

    @property
    def param_specs(self) -> list[TypeSpec]:
        return [p.type_spec for p in self.params]


class Constraint(BaseModel):
    """A constraint as written, plus the structured reading the generator and validator use."""

    text: str
    variable: str | None = None
    min: float | int | None = None
    max: float | int | None = None


class TestCase(BaseModel):
    __test__ = False  # keep pytest from collecting this as a test class

    # function mode
    args: list[Any] | None = None
    expected: Any = None
    # stdio mode
    stdin: str | None = None
    stdout: str | None = None
    label: str | None = None  # e.g. "Test Case 3" as printed in the source
    points: int | None = None
    explanation: str | None = None
    category: TestCategory = TestCategory.ORIGINAL
    difficulty: Difficulty | None = None
    origin: TestOrigin = TestOrigin.SOURCE


class Provenance(BaseModel):
    pages: list[int] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)  # field name -> 0..1
    warnings: list[str] = Field(default_factory=list)


class ImageAsset(BaseModel):
    path: str  # relative to the job's assets directory
    page: int
    section: str


def validate_case(io_spec: IOSpec, case: TestCase, where: str = "case") -> TestCase:
    """Return a copy of ``case`` with args/expected coerced to canonical form.

    Raises ``ValueTypeError`` describing exactly which argument is wrong.
    """
    if case.args is None:
        raise ValueTypeError(f"{where}: function-mode test case has no args")
    if len(case.args) != len(io_spec.params):
        raise ValueTypeError(
            f"{where}: expected {len(io_spec.params)} argument(s) for {io_spec.function_name}, got {len(case.args)}"
        )
    args = [coerce(p.type_spec, a, f"{where}.args.{p.name}") for p, a in zip(io_spec.params, case.args)]
    expected = case.expected
    if expected is not None:
        expected = coerce(io_spec.return_spec, expected, f"{where}.expected")
    return case.model_copy(update={"args": args, "expected": expected})


class Question(BaseModel):
    id: str  # the source's question id (DBNO); never modified by the audit
    number: int | None = None  # ordinal in the source document (Q.1, Q.2 ...)
    title: str
    io_mode: IOMode = IOMode.FUNCTION
    description_md: str
    input_format_md: str | None = None
    output_format_md: str | None = None
    constraints: list[Constraint] = Field(default_factory=list)
    io_spec: IOSpec | None = None
    samples: list[TestCase] = Field(default_factory=list)
    sample_explanation_md: str | None = None
    hidden_tests: list[TestCase] = Field(default_factory=list)
    drivers: dict[Language, str] = Field(default_factory=dict)
    editorial_md: str | None = None
    solutions: dict[Language, str] = Field(default_factory=dict)
    difficulty: Difficulty | None = None
    lod: int | None = None  # source's level-of-difficulty number (33 / 66 / 99)
    area: str | None = None
    time_limit_seconds: float | None = None
    tags: list[str] = Field(default_factory=list)
    checker: Checker = Checker.EXACT
    images: list[ImageAsset] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _cases_match_mode(self) -> "Question":
        if self.io_mode is IOMode.FUNCTION:
            if self.io_spec is None:
                raise ValueError("function-mode questions need an io_spec")
            self.samples = [validate_case(self.io_spec, c, f"samples[{i}]") for i, c in enumerate(self.samples)]
            self.hidden_tests = [validate_case(self.io_spec, c, f"hidden_tests[{i}]") for i, c in enumerate(self.hidden_tests)]
        else:
            for where, cases in (("samples", self.samples), ("hidden_tests", self.hidden_tests)):
                for i, c in enumerate(cases):
                    if c.stdin is None or c.stdout is None:
                        raise ValueError(f"{where}[{i}]: stdio test case needs both stdin and stdout")
        return self

    @property
    def all_tests(self) -> list[TestCase]:
        return [*self.samples, *self.hidden_tests]
