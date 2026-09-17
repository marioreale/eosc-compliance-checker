"""LP-AAI-01 behaviour, including the near-miss and exemption paths."""

from __future__ import annotations

from datetime import date

import pytest
from conftest import bundle_from_html, make_target

from checker.api import evaluate
from checker.checks import aai_login  # noqa: F401 -- registration
from checker.models import Verdict

AFTER_MANDATORY = date(2026, 11, 1)
BEFORE_MANDATORY = date(2026, 9, 1)


def run(html: str, *, declarations=None, exemptions=None, today=AFTER_MANDATORY):
    target = make_target(declarations=declarations, exemptions=exemptions)
    bundle = bundle_from_html(html, declarations=declarations)
    findings = evaluate(target, bundle, only=["LP-AAI-01"], today=today)
    assert len(findings) == 1
    return findings[0]


def page(body: str, lang: str = "en") -> str:
    return f'<html lang="{lang}"><head><title>T</title></head><body>{body}</body></html>'


@pytest.mark.parametrize(
    "body",
    [
        '<a href="/auth">Login with EOSC AAI</a>',
        '<button>Sign in with EOSC AAI</button>',
        '<a href="/x">EOSC AAI login</a>',
        '<div role="button"><svg aria-label="Sign in with EOSC AAI"></svg></div>',
        '<a href="https://aai.example.org/eosc/auth">Access services</a>',
        '<a href="/go">Log in</a><a href="https://proxy.example/MyAccessID/auth">MyAccessID</a>',
    ],
)
def test_recognised_login_affordances(body):
    assert run(page(body)).verdict == Verdict.PASS


def test_generic_login_is_a_reported_near_miss():
    """The most useful finding the tool produces: present, but not EOSC-labelled."""
    finding = run(page('<a href="/login">Log in</a>'))
    assert finding.verdict == Verdict.FAIL
    assert "does not identify EOSC AAI" in finding.message
    assert finding.remediation
    assert finding.evidence


def test_no_login_at_all():
    finding = run(page("<p>Welcome to the node.</p>"))
    assert finding.verdict == Verdict.FAIL
    assert "No login affordance of any kind" in finding.message


def test_hidden_login_control_does_not_count():
    finding = run(page('<a href="/x" style="display:none">Login with EOSC AAI</a>'))
    assert finding.verdict == Verdict.FAIL


def test_anonymous_declaration_yields_not_applicable():
    finding = run(page("<p>Open data.</p>"), declarations={"no_authenticated_area": True})
    assert finding.verdict == Verdict.NOT_APPLICABLE
    assert "no authenticated area" in finding.message.lower()


def test_before_mandatory_date_a_failure_is_only_a_warning():
    """Comply-by dates: the same evidence produces a different governed verdict."""
    finding = run(page("<p>Nothing here.</p>"), today=BEFORE_MANDATORY)
    assert finding.verdict == Verdict.WARN
    assert finding.downgraded_from == Verdict.FAIL
    assert "2026-10-08" in finding.downgrade_reason


def test_active_exemption_downgrades_and_records_the_reason():
    finding = run(
        page("<p>Nothing here.</p>"),
        exemptions=[
            {
                "rule_id": "LP-AAI-01",
                "reason": "Landing page is deliberately anonymous",
                "expires": "2026-12-31",
            }
        ],
    )
    assert finding.verdict == Verdict.NOT_APPLICABLE
    assert finding.downgraded_from == Verdict.FAIL
    assert "deliberately anonymous" in finding.downgrade_reason


def test_expired_exemption_does_not_apply():
    finding = run(
        page("<p>Nothing here.</p>"),
        exemptions=[
            {"rule_id": "LP-AAI-01", "reason": "old carve-out", "expires": "2026-01-01"}
        ],
    )
    assert finding.verdict == Verdict.FAIL


def test_collection_failure_is_error_not_fail():
    """A site we could not reach is our problem, never their non-compliance."""
    target = make_target()
    bundle = bundle_from_html("<html></html>")
    entry = bundle.entry_page
    entry.ok = False
    entry.error = "TimeoutError: navigation timeout"
    finding = evaluate(target, bundle, only=["LP-AAI-01"], today=AFTER_MANDATORY)[0]
    assert finding.verdict == Verdict.ERROR
    assert finding.verdict.is_adverse is False
