"""CAT-AUP-01 — every listed service must reach an AUP/UAP document.

Demonstrates the pattern that matters most for this rule class: a link is not
compliance. The link must also resolve, and "present but dead" is a materially
different finding from "absent".
"""

from __future__ import annotations

from ..api import CheckContext, check
from ..models import Evidence, Finding, Severity, Verdict

AUP_PATTERNS = [
    r"(?i)\bacceptable\s+use\s+polic",
    r"(?i)\buser\s+acceptable\s+use\b",
    r"(?i)\b(AUP|UAP)\b",
    r"(?i)\bterms\s+of\s+(use|service)\b",
    r"(?i)\brules\s+of\s+participation\b",
    r"(?i)\bacceptable[-_]use\b",
    r"(?i)/aup\b",
    # European research infrastructures very often publish this artefact as an
    # "Access Policy" rather than an "Acceptable Use Policy" (BBMRI-ERIC, and
    # most ERICs, do exactly this). Matching the label is still a heuristic --
    # see README known limitations -- but omitting it produced a false FAIL
    # against a node that does publish a conforming AUP.
    r"(?i)\baccess\s+polic",
    r"(?i)\bconditions\s+of\s+(use|access)\b",
]

# Fields a catalogue record might use to carry the policy URL directly.
POLICY_FIELDS = (
    "accessPolicy",
    "access_policy",
    "aup",
    "termsOfUse",
    "terms_of_use",
    "usagePolicy",
)


@check(
    id="CAT-AUP-01",
    version="1.0.0",
    title="Listed services reference an acceptable use policy",
    requirement_ref="Checklist 5.4",
    severity=Severity.MANDATORY,
    applies_to=["node_landing_page", "node_catalogue"],
)
def services_reference_aup(ctx: CheckContext) -> list[Finding]:
    """Each service in the node catalogue must point to an AUP/UAP document."""
    services = ctx.evidence.catalogue_services
    if not services:
        # No catalogue configured is not a failure of this rule; fall back to
        # checking that the node itself publishes a policy somewhere.
        return [_node_level_aup(ctx)]

    findings: list[Finding] = []
    for svc in services:
        svc_id = str(svc.get("id") or svc.get("name") or "unknown")
        svc_name = str(svc.get("name") or svc_id)

        direct = next(
            (str(svc[f]) for f in POLICY_FIELDS if svc.get(f)),
            None,
        )
        if direct:
            live = ctx.link_is_live(direct)
            findings.append(
                ctx.finding(
                    Verdict.PASS if live else Verdict.WARN,
                    target_detail=svc_id,
                    message=(
                        f"Service {svc_name!r} declares a policy URL: {direct}"
                        if live
                        else (
                            f"Service {svc_name!r} declares a policy URL that is "
                            f"unreachable: {direct}"
                        )
                    ),
                    remediation=(
                        None if live else "Fix or update the policy URL in the catalogue record."
                    ),
                    evidence=[Evidence(locator="catalogue_record", extra={"policy_url": direct})],
                )
            )
            continue

        # Otherwise look for an AUP link on the service's own page.
        page_url = str(svc.get("url") or svc.get("webpage") or "")
        page = ctx.evidence.pages.get(page_url)
        if not page or not page.ok:
            findings.append(
                ctx.finding(
                    Verdict.MANUAL_REVIEW,
                    target_detail=svc_id,
                    message=(
                        f"No policy field in the catalogue record for {svc_name!r} and its "
                        "service page was not collected; needs a human check."
                    ),
                    confidence=0.0,
                    evidence=[Evidence(url=page_url or None, locator="catalogue_record")],
                )
            )
            continue

        hits = ctx.match_links(page, AUP_PATTERNS)
        if not hits:
            findings.append(
                ctx.finding(
                    Verdict.FAIL,
                    target_detail=svc_id,
                    message=f"No AUP/UAP reference found for service {svc_name!r}.",
                    remediation=(
                        "Publish an acceptable use policy and link it from the service "
                        "page, or populate accessPolicy in the catalogue record."
                    ),
                    evidence=[
                        Evidence(
                            url=page.final_url,
                            locator="a[href]",
                            extra={"links_examined": len(page.links)},
                        )
                    ],
                )
            )
            continue

        dead = [h for h in hits if not ctx.link_is_live(h.href)]
        findings.append(
            ctx.finding(
                Verdict.WARN if dead else Verdict.PASS,
                target_detail=svc_id,
                message=(
                    f"AUP reference for {svc_name!r} is present but unreachable: "
                    + ", ".join(d.href for d in dead)
                    if dead
                    else f"AUP reference found for {svc_name!r}: {hits[0].href}"
                ),
                remediation="Repair the broken policy link." if dead else None,
                evidence=[
                    Evidence(
                        locator="a[href]",
                        matched_text=h.text[:200],
                        url=page.final_url,
                        extra={"href": h.href, "status": ctx.evidence.link_liveness.get(h.href)},
                    )
                    for h in hits[:3]
                ],
            )
        )
    return findings


def _node_level_aup(ctx: CheckContext) -> Finding:
    """Fallback when no catalogue is configured: does the node publish any policy?"""
    # Invariant: if we collected nothing usable, that is OUR failure. Emitting
    # FAIL here would accuse a node of non-compliance because our crawler could
    # not reach it.
    if not any(p.ok for p in ctx.pages()):
        return ctx.finding(
            Verdict.ERROR,
            message="No page was successfully collected; cannot evaluate AUP coverage.",
            evidence=[
                Evidence(
                    url=ctx.target.landing_page,
                    extra={
                        "errors": [p.error for p in ctx.pages() if p.error][:3],
                    },
                )
            ],
        )

    for page in ctx.pages():
        if not page.ok:
            continue
        hits = ctx.match_links(page, AUP_PATTERNS)
        if hits:
            dead = [h for h in hits if not ctx.link_is_live(h.href)]
            return ctx.finding(
                Verdict.WARN if dead else Verdict.PASS,
                message=(
                    "Node-level acceptable use policy link is unreachable: "
                    + ", ".join(d.href for d in dead)
                    if dead
                    else f"Node-level acceptable use policy found: {hits[0].href}"
                ),
                remediation="Repair the broken policy link." if dead else None,
                evidence=[
                    Evidence(
                        locator="a[href]",
                        matched_text=h.text[:200],
                        url=page.final_url,
                        extra={"href": h.href},
                    )
                    for h in hits[:3]
                ],
            )
    # Absence is only meaningful if we actually finished looking. On a truncated
    # crawl the AUP may sit on a page we never opened -- which is exactly what
    # happened on the first real run against BBMRI-ERIC, whose AUP is linked from
    # an "Access Policies" page that fell outside the 12-page budget.
    if ctx.evidence.crawl_truncated:
        return ctx.finding(
            Verdict.MANUAL_REVIEW,
            message=(
                "No acceptable use policy found on the "
                f"{len(ctx.pages())} page(s) collected, but the crawl was truncated "
                f"with {ctx.evidence.unvisited_count} link(s) unvisited, so absence "
                "cannot be concluded."
            ),
            remediation=(
                "Raise crawl.max_pages for this target, or point catalogue_api at the "
                "node catalogue, then re-run."
            ),
            evidence=[
                Evidence(
                    url=ctx.target.landing_page,
                    extra={
                        "pages_examined": len(ctx.pages()),
                        "unvisited": ctx.evidence.unvisited_count,
                        "crawl_truncated": True,
                    },
                )
            ],
        )

    return ctx.finding(
        Verdict.FAIL,
        message="No acceptable use policy reference found anywhere on the collected pages.",
        remediation=(
            "Publish an AUP/UAP document and link it from the landing page, and "
            "configure catalogue_api so per-service policies can be verified."
        ),
        evidence=[
            Evidence(
                url=ctx.target.landing_page,
                locator="a[href]",
                extra={"pages_examined": len(ctx.pages())},
            )
        ],
    )
