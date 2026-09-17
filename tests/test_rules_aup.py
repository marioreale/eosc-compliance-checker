"""CAT-AUP-01: a link is not compliance — it also has to resolve."""

from __future__ import annotations

from conftest import bundle_from_html, make_target

from checker.api import evaluate
from checker.checks import aup_pointer  # noqa: F401
from checker.models import Verdict

PAGE = (
    '<html lang="en"><head><title>Service</title></head><body>'
    '<a href="/aup.html">Acceptable Use Policy</a></body></html>'
)
BARE = '<html lang="en"><head><title>Service</title></head><body><p>Nothing.</p></body></html>'
URL = "https://node.example.org/"


def run(html, *, liveness=None, services=None):
    bundle = bundle_from_html(
        html, url=URL, link_liveness=liveness or {}, catalogue_services=services or []
    )
    return evaluate(make_target(url=URL), bundle, only=["CAT-AUP-01"])


def test_node_level_aup_found():
    findings = run(PAGE, liveness={"https://node.example.org/aup.html": 200})
    assert [f.verdict for f in findings] == [Verdict.PASS]


def test_node_level_aup_present_but_dead_is_warn_not_pass():
    findings = run(PAGE, liveness={"https://node.example.org/aup.html": 404})
    assert findings[0].verdict == Verdict.WARN
    assert "unreachable" in findings[0].message


def test_aup_link_hidden_in_a_cookie_banner_does_not_count():
    """Straight from the adversarial fixture: a policy link no user can reach.

    This one actually slipped through an early build of the checker, which is
    exactly why the tricky fixture exists.
    """
    html = (
        '<html lang="en"><head><title>Service</title></head><body>'
        '<div id="cookie-banner" style="display:none">'
        '<a href="/cookies.html">Terms of use</a></div>'
        "</body></html>"
    )
    findings = run(html, liveness={"https://node.example.org/cookies.html": 200})
    assert findings[0].verdict == Verdict.FAIL


def test_missing_aup_fails():
    findings = run(BARE)
    assert findings[0].verdict == Verdict.FAIL
    assert findings[0].remediation


def test_catalogue_policy_field_is_used_directly():
    findings = run(
        BARE,
        services=[{"id": "svc-1", "name": "Compute", "accessPolicy": "https://p.example/aup"}],
        liveness={"https://p.example/aup": 200},
    )
    assert len(findings) == 1
    assert findings[0].verdict == Verdict.PASS
    assert findings[0].target_detail == "svc-1"


def test_per_service_findings_are_emitted_separately():
    """One finding per service: the report has to be actionable per service owner."""
    findings = run(
        BARE,
        services=[
            {"id": "svc-1", "name": "Compute", "accessPolicy": "https://p.example/aup"},
            {"id": "svc-2", "name": "Storage"},
            {"id": "svc-3", "name": "Analysis", "termsOfUse": "https://p.example/dead"},
        ],
        liveness={"https://p.example/aup": 200, "https://p.example/dead": 500},
    )
    by_id = {f.target_detail: f.verdict for f in findings}
    assert by_id["svc-1"] == Verdict.PASS
    assert by_id["svc-2"] == Verdict.MANUAL_REVIEW  # page not collected, cannot judge
    assert by_id["svc-3"] == Verdict.WARN


def test_access_policy_label_counts_as_an_aup():
    """Most ERICs publish this as an "Access Policy", not an "Acceptable Use Policy".

    Omitting the wording produced a false FAIL against BBMRI-ERIC on the first
    real run, which does publish a conforming AUP.
    """
    html = (
        '<html lang="en"><head><title>Node</title></head><body>'
        '<a href="/services/access-policies/">Access Policies</a>'
        "</body></html>"
    )
    findings = run(html, liveness={"https://node.example.org/services/access-policies/": 200})
    assert findings[0].verdict == Verdict.PASS


def test_absence_on_a_truncated_crawl_is_manual_review_not_fail():
    """A bounded crawl cannot prove absence.

    The AUP may simply live on a page the crawl budget never reached -- exactly
    what happened on the first real BBMRI-ERIC run, whose AUP is linked from an
    "Access Policies" page outside the 12-page budget. Claiming FAIL there is a
    false accusation against the node operator.
    """
    bundle = bundle_from_html(BARE)
    bundle.crawl_truncated = True
    bundle.unvisited_count = 37
    findings = evaluate(make_target(), bundle, only=["CAT-AUP-01"])
    assert findings[0].verdict == Verdict.MANUAL_REVIEW
    assert "truncated" in findings[0].message
    assert findings[0].evidence[0].extra["unvisited"] == 37
