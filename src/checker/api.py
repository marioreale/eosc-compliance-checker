"""Phase 2: the evaluation API.

Everything here is pure: evidence in, findings out, no network access. That
constraint is what makes rule development fast and rule testing trivial.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from datetime import date

from .models import (
    Evidence,
    EvidenceBundle,
    Finding,
    Link,
    PageEvidence,
    RuleMeta,
    Severity,
    UiElement,
    Verdict,
)
from .registry import Target

CheckFn = Callable[["CheckContext"], list[Finding]]
_REGISTRY: dict[str, tuple[RuleMeta, CheckFn]] = {}


class CheckContext:
    """Everything a rule is allowed to see."""

    def __init__(
        self,
        meta: RuleMeta,
        target: Target,
        bundle: EvidenceBundle,
        *,
        today: date | None = None,
    ) -> None:
        self.meta = meta
        self.target = target
        self.evidence = bundle
        self.today = today or date.today()

    # -- convenience accessors -------------------------------------------

    @property
    def entry(self) -> PageEvidence | None:
        return self.evidence.entry_page

    def pages(self) -> list[PageEvidence]:
        return self.evidence.page_list()

    def declares(self, key: str) -> bool:
        return bool(self.evidence.declarations.get(key))

    def link_is_live(self, href: str) -> bool:
        """Unknown links are treated as live: never invent a failure."""
        status = self.evidence.link_liveness.get(href)
        return status is None or 200 <= status < 400

    def link_liveness_verified(self, href: str) -> bool:
        """True only if this link was actually probed.

        `link_is_live` deliberately returns True for links it knows nothing
        about, so that a gap in collection never becomes an accusation. The cost
        is that "live" conflates *verified reachable* with *never checked*. A rule
        asserting reachability needs to tell those apart, otherwise it reports
        confident PASSes it has not earned.
        """
        return href in self.evidence.link_liveness

    def match_controls(
        self, page: PageEvidence, patterns: Iterable[str]
    ) -> list[UiElement]:
        compiled = [re.compile(p) for p in patterns]
        return [
            c
            for c in page.controls
            if c.visible and any(rx.search(c.all_text) for rx in compiled)
        ]

    def match_links(
        self,
        page: PageEvidence,
        patterns: Iterable[str],
        *,
        visible_only: bool = True,
    ) -> list[Link]:
        """Links whose text, label or href matches any pattern.

        Visible links only by default. Hidden markup is where false positives
        live: a collapsed cookie banner, a hidden legacy nav, or a mega-menu that
        never renders will happily contain a "Terms of use" link that no user can
        reach. A requirement is about what a user can find, so a link nobody can
        see does not satisfy it.
        """
        compiled = [re.compile(p) for p in patterns]
        out = []
        for link in page.links:
            if visible_only and not link.visible:
                continue
            haystack = f"{link.text} {link.aria_label or ''} {link.href}"
            if any(rx.search(haystack) for rx in compiled):
                out.append(link)
        return out

    # -- finding construction --------------------------------------------

    def finding(
        self,
        verdict: Verdict,
        *,
        message: str,
        remediation: str | None = None,
        evidence: list[Evidence] | None = None,
        target_detail: str | None = None,
        confidence: float = 1.0,
    ) -> Finding:
        return Finding(
            rule_id=self.meta.id,
            rule_version=self.meta.version,
            rule_title=self.meta.title,
            requirement_ref=self.meta.requirement_ref,
            severity=self.meta.severity,
            target_id=self.target.id,
            target_detail=target_detail,
            verdict=verdict,
            confidence=confidence,
            message=message,
            remediation=remediation,
            evidence=evidence or [],
        )


def check(
    *,
    id: str,
    version: str,
    title: str,
    requirement_ref: str | None = None,
    severity: Severity | str = Severity.MANDATORY,
    mandatory_from: date | None = None,
    applies_to: list[str] | None = None,
    description: str = "",
) -> Callable[[CheckFn], CheckFn]:
    """Register a tier-2 Python check."""

    def decorator(fn: CheckFn) -> CheckFn:
        meta = RuleMeta(
            id=id,
            version=version,
            title=title,
            requirement_ref=requirement_ref,
            severity=Severity(severity),
            mandatory_from=mandatory_from,
            applies_to=applies_to or ["node_landing_page"],
            description=description or (fn.__doc__ or "").strip(),
        )
        _REGISTRY[id] = (meta, fn)
        return fn

    return decorator


def registered_rules() -> dict[str, tuple[RuleMeta, CheckFn]]:
    return dict(_REGISTRY)


# ----------------------------------------------------------------------
# Post-processing: exemptions and comply-by dates
# ----------------------------------------------------------------------


def apply_governance(finding: Finding, target: Target, today: date) -> Finding:
    """Turn raw verdicts into governed verdicts.

    Two transformations, both of which exist because compliance regimes phase
    requirements in rather than switching them on overnight:

    1. An active exemption converts FAIL -> NOT_APPLICABLE.
    2. A rule before its `mandatory_from` date converts FAIL -> WARN.

    The original verdict is always preserved on the finding.
    """
    if finding.verdict != Verdict.FAIL:
        return finding

    exemption = target.exemption_for(finding.rule_id, today)
    if exemption:
        finding.downgraded_from = finding.verdict
        finding.verdict = Verdict.NOT_APPLICABLE
        finding.downgrade_reason = f"Exemption: {exemption.reason}"
        return finding

    meta_pair = _REGISTRY.get(finding.rule_id)
    mandatory_from = meta_pair[0].mandatory_from if meta_pair else None
    if mandatory_from and today < mandatory_from:
        finding.downgraded_from = finding.verdict
        finding.verdict = Verdict.WARN
        finding.downgrade_reason = (
            f"Not mandatory until {mandatory_from.isoformat()}"
        )
        return finding

    if finding.severity == Severity.RECOMMENDED:
        finding.downgraded_from = finding.verdict
        finding.verdict = Verdict.WARN
        finding.downgrade_reason = "Rule is recommended, not mandatory"

    return finding


def evaluate(
    target: Target,
    bundle: EvidenceBundle,
    *,
    only: list[str] | None = None,
    today: date | None = None,
) -> list[Finding]:
    """Run every applicable rule against one evidence bundle."""
    today = today or date.today()
    findings: list[Finding] = []

    for rule_id, (meta, fn) in sorted(_REGISTRY.items()):
        if only and rule_id not in only:
            continue
        if bundle.target_type not in meta.applies_to:
            continue

        ctx = CheckContext(meta, target, bundle, today=today)
        started = time.perf_counter()
        try:
            produced = fn(ctx) or []
        except Exception as exc:
            produced = [
                ctx.finding(
                    Verdict.ERROR,
                    message=f"Rule raised {type(exc).__name__}: {exc}",
                )
            ]
        elapsed = int((time.perf_counter() - started) * 1000)
        for f in produced:
            f.duration_ms = elapsed
            findings.append(apply_governance(f, target, today))

    return findings
