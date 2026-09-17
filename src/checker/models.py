"""Core data model.

Two objects carry the whole design: the evidence bundle (what we observed) and
the finding (what we concluded). Getting these right makes everything else
mechanical.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class Verdict(StrEnum):
    """Deliberately wider than pass/fail.

    NOT_APPLICABLE and MANUAL_REVIEW are what make this usable for real
    compliance work; ERROR keeps our own failures out of the findings we report
    against somebody else's site.
    """

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    NOT_APPLICABLE = "not_applicable"
    MANUAL_REVIEW = "manual_review"
    ERROR = "error"

    @property
    def is_adverse(self) -> bool:
        return self in (Verdict.FAIL,)


class Severity(StrEnum):
    MANDATORY = "mandatory"
    RECOMMENDED = "recommended"
    INFORMATIONAL = "informational"


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class Link(BaseModel):
    href: str = ""
    text: str = ""
    aria_label: str | None = None
    rel: str | None = None
    is_internal: bool = False
    visible: bool = True


class UiElement(BaseModel):
    """A candidate interactive control: <a>, <button>, [role=button], <input>."""

    tag: str = ""
    text: str = ""
    aria_label: str | None = None
    role: str | None = None
    href: str | None = None
    title: str | None = None
    alt_texts: list[str] = Field(default_factory=list)
    visible: bool = True

    @property
    def all_text(self) -> str:
        """Every string a human could read off this control."""
        parts = [self.text, self.aria_label or "", self.title or "", *self.alt_texts]
        return " ".join(p for p in parts if p).strip()


class PageEvidence(BaseModel):
    """Everything captured from one page visit.

    Capture generously. Re-crawling because a field was not stored is the most
    predictable regret in a project like this.
    """

    requested_url: str
    final_url: str = ""
    status: int = 0
    ok: bool = False
    error: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    html: str = ""
    text: str = ""
    title: str | None = None
    lang_attr: str | None = None
    hreflang: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    controls: list[UiElement] = Field(default_factory=list)
    screenshot: str | None = None
    console_errors: list[str] = Field(default_factory=list)
    content_sha256: str | None = None
    axe: dict[str, Any] | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    fetch_duration_ms: int = 0
    depth: int = 0


class EvidenceBundle(BaseModel):
    """One target, one run. The artefact that decouples collection from evaluation."""

    schema_version: int = SCHEMA_VERSION
    run_id: str
    target_id: str
    target_type: str = "node_landing_page"
    collected_at: datetime = Field(default_factory=utcnow)
    collector_version: str = "0.1.0"
    pages: dict[str, PageEvidence] = Field(default_factory=dict)
    entry_url: str = ""
    link_liveness: dict[str, int] = Field(default_factory=dict)
    catalogue_services: list[dict[str, Any]] = Field(default_factory=list)
    declarations: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def entry_page(self) -> PageEvidence | None:
        return self.pages.get(self.entry_url)

    def page_list(self) -> list[PageEvidence]:
        return list(self.pages.values())


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    """The justification shown to a node operator who disputes a finding."""

    locator: str | None = None
    matched_text: str | None = None
    snippet_html: str | None = None
    url: str | None = None
    screenshot: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    rule_id: str
    rule_version: str
    requirement_ref: str | None = None
    rule_title: str = ""
    severity: Severity = Severity.MANDATORY
    target_id: str
    target_detail: str | None = None
    verdict: Verdict
    confidence: float = 1.0
    message: str = ""
    remediation: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utcnow)
    duration_ms: int = 0
    downgraded_from: Verdict | None = None
    downgrade_reason: str | None = None


class RunReport(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    rules_evaluated: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for f in self.findings:
            out[f.verdict.value] += 1
        return out

    def blocking_failures(self) -> list[Finding]:
        """Only these should ever fail a CI run."""
        return [
            f
            for f in self.findings
            if f.verdict == Verdict.FAIL and f.severity == Severity.MANDATORY
        ]

    def by_target(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.target_id, []).append(f)
        return out


class RuleMeta(BaseModel):
    """Rule metadata. `mandatory_from` is how comply-by dates get enforced."""

    id: str
    version: str
    title: str
    requirement_ref: str | None = None
    severity: Severity = Severity.MANDATORY
    mandatory_from: date | None = None
    applies_to: list[str] = Field(default_factory=lambda: ["node_landing_page"])
    description: str = ""
