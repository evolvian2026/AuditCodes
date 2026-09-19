"""Findings, patches and the per-question audit report."""

from __future__ import annotations

import secrets
import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    BLOCKER = "blocker"  # the question is wrong or unusable as published
    MAJOR = "major"  # a real defect that should be fixed before publishing
    MINOR = "minor"  # worth fixing; does not make the question wrong
    INFO = "info"  # an observation, no action required

    @property
    def rank(self) -> int:
        return ["blocker", "major", "minor", "info"].index(self.value)


class FindingStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"  # patch applied
    REJECTED = "rejected"  # reviewer disagrees
    WAIVED = "waived"  # acknowledged, deliberately not fixed


class FindingSource(str, Enum):
    DYNAMIC = "dynamic"  # established by compiling and running code
    STATIC = "static"  # proposed by the language model; an opinion until verified
    REVIEWER = "reviewer"


class Patch(BaseModel):
    """A proposed change to one field of the question, addressed like a reviewer edit."""

    op: Literal["set", "remove", "append"] = "set"
    path: str  # dotted field path, e.g. "solutions.java", "hidden_tests.3.stdout", "hidden_tests.9"
    new_value: str | None = None
    old_value: str | None = None
    item_fingerprint: str | None = None  # remove: identifies the list item regardless of index
    items: list[dict[str, Any]] | None = None  # append: test cases to add to the list at ``path``
    verified: bool | None = None  # True/False when execution checked the patch; None when it cannot
    verification_note: str | None = None


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: secrets.token_hex(4))
    rule_id: str
    severity: Severity
    source: FindingSource
    component: str  # field path or logical component the finding is about
    title: str
    message: str
    evidence: str | None = None
    patch: Patch | None = None
    confidence: float | None = None  # the model's own confidence for static findings
    status: FindingStatus = FindingStatus.OPEN
    applied: bool = False
    resolution_note: str | None = None

    @property
    def open(self) -> bool:
        return self.status is FindingStatus.OPEN


class VerificationSummary(BaseModel):
    language: str
    build_ok: bool
    build_log: str = ""
    normalized: bool = False  # the code was run after typographic normalisation
    passed: int = 0
    total: int = 0
    all_passed: bool = False
    max_cpu_seconds: float = 0.0
    cases: list[dict[str, Any]] = Field(default_factory=list)


class AuditReport(BaseModel):
    question_id: str
    status: Literal["running", "done", "failed"] = "running"
    started_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    finished_at: str | None = None
    error: str | None = None
    llm_model: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    verifications: dict[str, VerificationSummary] = Field(default_factory=dict)
    validator: dict[str, Any] | None = None
    driver_check: dict[str, Any] | None = None
    oracle: dict[str, Any] | None = None  # the pair of implementations trusted to produce expected outputs
    generation: dict[str, Any] | None = None  # hidden-test generation statistics
    projection: dict[str, Any] | None = None  # what remains after accepting every verified patch
    stages: list[str] = Field(default_factory=list)  # human-readable log of what ran

    def log(self, message: str) -> None:
        self.stages.append(message)

    def add(self, finding: Finding) -> Finding:
        self.findings.append(finding)
        return finding

    def get(self, finding_id: str) -> Finding | None:
        return next((f for f in self.findings if f.id == finding_id), None)

    def counts(self, only_open: bool = True) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            if not only_open or f.open:
                out[f.severity.value] += 1
        return out

    @property
    def open_blockers(self) -> int:
        return self.counts()["blocker"]

    @property
    def auto_applicable(self) -> list[Finding]:
        """Open findings whose patch execution verified or that only restructure the test list."""
        return [f for f in self.findings if f.open and f.patch is not None and (f.patch.verified is True or f.patch.op in ("remove", "append"))]

    @property
    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (f.status is not FindingStatus.OPEN, f.severity.rank, f.rule_id))
