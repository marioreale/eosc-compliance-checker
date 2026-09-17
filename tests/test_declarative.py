"""The tier-1 YAML engine, plus the shipped rule pack."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from conftest import bundle_from_html, make_target

from checker.api import evaluate, registered_rules
from checker.declarative import load_rule_dir
from checker.models import Verdict

RULES_DIR = Path(__file__).resolve().parents[1] / "rules"
URL = "https://node.example.org/"
AFTER = date(2026, 11, 1)


def setup_module(module):
    load_rule_dir(RULES_DIR)


def test_rule_pack_loads():
    ids = set(registered_rules())
    assert {"LP-CONTACT-01", "LP-PRIVACY-01", "LP-CATALOGUE-01", "LP-EOSC-BRAND-01"} <= ids


def run(html, rule, *, liveness=None, today=AFTER):
    bundle = bundle_from_html(html, url=URL, link_liveness=liveness or {})
    findings = evaluate(make_target(url=URL), bundle, only=[rule], today=today)
    assert len(findings) == 1
    return findings[0]


def wrap(body: str) -> str:
    return f'<html lang="en"><head><title>T</title></head><body>{body}</body></html>'


def test_contact_via_mailto():
    assert run(wrap('<a href="mailto:x@y.org">Write us</a>'), "LP-CONTACT-01").verdict == Verdict.PASS


def test_contact_missing():
    assert run(wrap("<p>Hello</p>"), "LP-CONTACT-01").verdict == Verdict.FAIL


def test_privacy_requires_live_link():
    html = wrap('<a href="/privacy.html">Privacy</a>')
    live = run(html, "LP-PRIVACY-01", liveness={f"{URL}privacy.html": 200})
    dead = run(html, "LP-PRIVACY-01", liveness={f"{URL}privacy.html": 404})
    assert live.verdict == Verdict.PASS
    assert dead.verdict == Verdict.FAIL, "a dead privacy link is not a privacy policy"


def test_recommended_severity_downgrades_fail_to_warn():
    finding = run(wrap("<p>Some national portal.</p>"), "LP-EOSC-BRAND-01")
    assert finding.verdict == Verdict.WARN
    assert finding.downgraded_from == Verdict.FAIL
    assert "recommended" in finding.downgrade_reason


def test_brand_rule_passes_on_text_mention():
    finding = run(
        wrap("<main><p>This node is part of the EOSC Federation.</p></main>"),
        "LP-EOSC-BRAND-01",
    )
    assert finding.verdict == Verdict.PASS


def test_declarative_and_python_rules_are_indistinguishable_downstream():
    """Both tiers produce Findings with the same shape and governance handling."""
    from checker.checks import aai_login  # noqa: F401

    bundle = bundle_from_html(wrap('<a href="/login">Log in</a>'), url=URL)
    findings = evaluate(make_target(url=URL), bundle, today=AFTER)
    assert {f.rule_id for f in findings} >= {"LP-AAI-01", "LP-CONTACT-01"}
    for f in findings:
        assert f.rule_version and f.rule_title and f.severity
