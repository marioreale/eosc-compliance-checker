"""LP-AAI-01 — EOSC AAI login affordance on the node landing page.

Tier 2 (Python) rather than declarative YAML, because the exemption logic and
the near-miss reporting need real branching.
"""

from __future__ import annotations

from datetime import date

from ..api import CheckContext, check
from ..extract import snippet_around
from ..models import Evidence, Finding, Severity, Verdict

# Ordered from most to least specific. A match on an early pattern is strong
# evidence; a match only on a late one is a near miss worth reporting.
EOSC_LOGIN_PATTERNS = [
    r"(?i)\b(log|sign)\s*[- ]?in\b.{0,40}\bEOSC\b",
    r"(?i)\bEOSC\b.{0,40}\b(log|sign)\s*[- ]?in\b",
    r"(?i)\bEOSC\s*AAI\b",
    r"(?i)\bMyAccess\s?ID\b",
]
AAI_HREF_PATTERNS = [
    r"(?i)https?://[^\s\"']*aai\.[^\s\"']*eosc",
    r"(?i)https?://[^\s\"']*eosc[^\s\"']*/(auth|login|oidc|saml)",
    r"(?i)MyAccessID",
    r"(?i)/oauth2?/authorize",
]
GENERIC_LOGIN_PATTERNS = [
    r"(?i)\b(log|sign)\s*[- ]?in\b",
    r"(?i)\bauthenticate\b",
    r"(?i)\baccedi\b",
    r"(?i)\banmelden\b",
    r"(?i)\bse\s+connecter\b",
]


@check(
    id="LP-AAI-01",
    version="1.2.0",
    title="EOSC AAI login affordance present on landing page",
    requirement_ref="Landing Page Requirements 3.1",
    severity=Severity.MANDATORY,
    mandatory_from=date(2026, 10, 8),
    applies_to=["node_landing_page"],
)
def eosc_aai_login_affordance(ctx: CheckContext) -> list[Finding]:
    """Federation users must be able to see how to authenticate via EOSC AAI.

    Does not apply where the node has declared that nothing sits behind
    authentication -- the anonymous-access exemption.
    """
    page = ctx.entry
    if page is None or not page.ok:
        return [
            ctx.finding(
                Verdict.ERROR,
                message="Landing page could not be collected; cannot evaluate.",
                evidence=[
                    Evidence(
                        url=(page.requested_url if page else ctx.target.landing_page),
                        extra={"error": page.error if page else "no page evidence"},
                    )
                ],
            )
        ]

    # The exemption agreed for nodes with no authenticated area at all.
    if ctx.declares("no_authenticated_area"):
        return [
            ctx.finding(
                Verdict.NOT_APPLICABLE,
                message=(
                    "Node declares no authenticated area behind the landing page; "
                    "a login affordance is not required."
                ),
                evidence=[
                    Evidence(
                        url=page.final_url,
                        extra={"declaration": "no_authenticated_area"},
                    )
                ],
            )
        ]

    strong_controls = ctx.match_controls(page, EOSC_LOGIN_PATTERNS)
    href_links = ctx.match_links(page, AAI_HREF_PATTERNS)

    if strong_controls or href_links:
        ev: list[Evidence] = []
        for c in strong_controls[:3]:
            ev.append(
                Evidence(
                    locator=f"{c.tag}[role={c.role}]" if c.role else c.tag,
                    matched_text=c.all_text[:200],
                    snippet_html=snippet_around(page.html, c.all_text),
                    url=page.final_url,
                    screenshot=page.screenshot,
                    extra={"href": c.href},
                )
            )
        for link in href_links[:3]:
            ev.append(
                Evidence(
                    locator="a[href]",
                    matched_text=link.text[:200],
                    url=page.final_url,
                    screenshot=page.screenshot,
                    extra={"href": link.href},
                )
            )
        label = strong_controls[0].all_text if strong_controls else href_links[0].href
        return [
            ctx.finding(
                Verdict.PASS,
                message=f"EOSC AAI login affordance found: {label[:120]!r}",
                evidence=ev,
            )
        ]

    # A generic login control with no EOSC wording is the interesting near miss:
    # the node has an authenticated area but is not signalling EOSC AAI.
    generic = ctx.match_controls(page, GENERIC_LOGIN_PATTERNS)
    if generic:
        return [
            ctx.finding(
                Verdict.FAIL,
                message=(
                    f"A login control was found ({generic[0].all_text[:80]!r}) but it does "
                    "not identify EOSC AAI as an authentication option."
                ),
                remediation=(
                    'Label the control so EOSC users can recognise it, e.g. "Login with '
                    'EOSC AAI", or expose EOSC AAI as an option on the login page and '
                    "reference it from the landing page."
                ),
                evidence=[
                    Evidence(
                        locator=g.tag,
                        matched_text=g.all_text[:200],
                        snippet_html=snippet_around(page.html, g.all_text),
                        url=page.final_url,
                        screenshot=page.screenshot,
                        extra={"href": g.href},
                    )
                    for g in generic[:3]
                ],
            )
        ]

    return [
        ctx.finding(
            Verdict.FAIL,
            message="No login affordance of any kind was found on the landing page.",
            remediation=(
                'Add a visible control labelled "Login with EOSC AAI" pointing at your '
                "EOSC AAI-compliant endpoint, or register the anonymous-access "
                "exemption if nothing sits behind authentication."
            ),
            evidence=[
                Evidence(
                    url=page.final_url,
                    locator="a, button, [role=button]",
                    screenshot=page.screenshot,
                    extra={"controls_examined": len(page.controls)},
                )
            ],
        )
    ]
