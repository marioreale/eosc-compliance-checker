"""LP-LANG-01: the fuzzy rule must degrade to a human, never guess a FAIL."""

from __future__ import annotations

import pytest
from conftest import bundle_from_html, make_target

from checker.api import evaluate
from checker.checks import english_available  # noqa: F401
from checker.models import Verdict

pytest.importorskip("lingua", reason="language detection extra not installed")

ENGLISH = (
    "This node of the European research infrastructure provides computing, storage "
    "and data analysis services to researchers and public sector organisations. "
    "Access to authenticated services is provided through federated identity, so "
    "any researcher holding an eligible institutional identity can sign in without "
    "creating a separate account with the node operator."
)
DUTCH = (
    "Dit knooppunt van de Europese onderzoeksinfrastructuur biedt rekenkracht, "
    "opslagcapaciteit en gegevensanalysediensten aan onderzoekers en publieke "
    "instellingen. Toegang tot beveiligde diensten verloopt via de federatieve "
    "inlogvoorziening, zodat iedere onderzoeker met een geldige instellingsidentiteit "
    "kan inloggen zonder een afzonderlijk account aan te maken bij het knooppunt."
)


def page(text: str, *, lang: str = "en", head: str = "", body_extra: str = "") -> str:
    return (
        f'<html lang="{lang}"><head><title>T</title>{head}</head>'
        f"<body><main><p>{text}</p></main>{body_extra}</body></html>"
    )


def run(html: str):
    findings = evaluate(
        make_target(), bundle_from_html(html), only=["LP-LANG-01"]
    )
    assert len(findings) == 1
    return findings[0]


def test_english_content_passes():
    finding = run(page(ENGLISH))
    assert finding.verdict == Verdict.PASS
    assert finding.confidence > 0.6


def test_declared_english_but_dutch_content_is_manual_review_not_fail():
    """The trap this rule exists for. A confident FAIL here would be wrong."""
    finding = run(page(DUTCH, lang="en"))
    assert finding.verdict == Verdict.MANUAL_REVIEW
    assert "disagree" in finding.message
    assert finding.evidence[0].extra["detected_language"] == "nl"


def test_dutch_with_english_alternate_is_manual_review():
    finding = run(
        page(DUTCH, lang="nl", head='<link rel="alternate" hreflang="en" href="/en">')
    )
    assert finding.verdict == Verdict.MANUAL_REVIEW
    assert "English alternative" in finding.message


def test_dutch_with_language_switcher_is_manual_review():
    finding = run(page(DUTCH, lang="nl", body_extra='<a href="/en">EN</a>'))
    assert finding.verdict == Verdict.MANUAL_REVIEW


def test_dutch_with_no_english_route_is_still_manual_review():
    finding = run(page(DUTCH, lang="nl"))
    assert finding.verdict == Verdict.MANUAL_REVIEW
    assert "No English version identified" in finding.message


def test_too_little_text_is_manual_review_with_zero_confidence():
    finding = run(page("Hello."))
    assert finding.verdict == Verdict.MANUAL_REVIEW
    assert finding.confidence == 0.0
    assert "too little" in finding.message


def test_language_rule_never_emits_fail():
    """Property test over the whole rule: MANUAL_REVIEW is its adverse outcome."""
    for html in (page(ENGLISH), page(DUTCH, lang="en"), page(DUTCH, lang="nl"), page("Hi.")):
        assert run(html).verdict != Verdict.FAIL
