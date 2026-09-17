"""Cross-cutting invariants that must hold for every rule in the pack.

These caught two real bugs during development:

  * CAT-AUP-01 returned FAIL when collection had failed entirely, i.e. it accused
    a node of non-compliance because our own crawler could not reach it.
  * Declarative rules emitted FAIL findings with an empty evidence list, so the
    report could not show a node operator what had been looked at.

Both are the kind of defect that only shows up on a real run, which is why they
are pinned here as properties over all rules rather than as one-off tests.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import bundle_from_html, make_target

from checker.api import evaluate, registered_rules
from checker.checks import aai_login, aup_pointer, english_available  # noqa: F401
from checker.declarative import load_rule_dir
from checker.models import Verdict
from checker.registry import Target

RULES_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "rules"
TODAY = date(2026, 11, 1)  # after every comply-by date in the pack


def setup_module(module):
    load_rule_dir(RULES_DIR)


def _all_findings(bundle, target: Target | None = None):
    return evaluate(target or make_target(), bundle, today=TODAY)


def test_every_rule_is_exercised_by_the_pack():
    assert len(registered_rules()) >= 6


def test_failed_collection_never_produces_a_compliance_failure():
    """The single most important invariant in the whole project.

    If we could not collect the page, no rule may emit FAIL or WARN. Anything
    else means an unreachable host looks like a non-compliant one, and the tool
    becomes politically unusable.
    """
    bundle = bundle_from_html("<html></html>")
    page = bundle.entry_page
    page.ok = False
    page.status = 0
    page.error = "TimeoutError: navigation timeout of 30000ms exceeded"
    page.html = ""
    page.text = ""
    page.links = []
    page.controls = []

    findings = _all_findings(bundle)
    assert findings, "rules must still run and report, not silently skip"
    for f in findings:
        assert f.verdict in (Verdict.ERROR, Verdict.NOT_APPLICABLE), (
            f"{f.rule_id} returned {f.verdict} on failed collection: {f.message}"
        )


def test_empty_page_does_not_crash_any_rule():
    """A run must always complete. A rule that raises becomes ERROR, never a stack trace."""
    findings = _all_findings(bundle_from_html("<html><body></body></html>"))
    assert findings
    assert not [f for f in findings if "raised" in f.message], [
        f.message for f in findings if "raised" in f.message
    ]


@pytest.mark.parametrize(
    "html",
    [
        "<html><body></body></html>",
        '<html lang="en"><head><title>T</title></head><body><p>Hello there.</p></body></html>',
        '<html><body><a href="/x">Log in</a></body></html>',
    ],
)
def test_every_adverse_finding_carries_evidence_or_a_reason(html):
    """An unevidenced verdict is unarguable in the wrong direction.

    The node operator must be able to see what the tool inspected, otherwise they
    cannot tell you it looked in the wrong place.
    """
    for f in _all_findings(bundle_from_html(html)):
        if f.verdict in (Verdict.FAIL, Verdict.WARN, Verdict.MANUAL_REVIEW):
            assert f.evidence or f.downgrade_reason, f"{f.rule_id}: {f.message}"


def test_every_fail_offers_remediation():
    """A finding a node operator cannot act on is a complaint, not a finding."""
    for f in _all_findings(bundle_from_html("<html><body><p>x</p></body></html>")):
        if f.verdict == Verdict.FAIL or f.downgraded_from == Verdict.FAIL:
            assert f.remediation, f"{f.rule_id} fails without telling anyone how to fix it"


def test_findings_are_fully_attributed():
    """Every finding must be traceable to a versioned rule and a target."""
    for f in _all_findings(bundle_from_html("<html><body><p>x</p></body></html>")):
        assert f.rule_id and f.rule_version and f.rule_title
        assert f.target_id
        assert f.severity
        assert f.checked_at


def test_rule_ids_and_versions_are_unique_and_well_formed():
    seen = set()
    for rule_id, (meta, _) in registered_rules().items():
        assert rule_id == meta.id
        assert rule_id.isupper() or "-" in rule_id
        assert meta.version.count(".") == 2, f"{rule_id} version must be semver-ish"
        assert rule_id not in seen
        seen.add(rule_id)
