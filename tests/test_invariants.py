"""Cross-cutting invariants that must hold for every rule in the pack.

These caught three real bugs during development:

  * CAT-AUP-01 returned FAIL when collection had failed entirely, i.e. it accused
    a node of non-compliance because our own crawler could not reach it.
  * Declarative rules emitted FAIL findings with an empty evidence list, so the
    report could not show a node operator what had been looked at.
  * `evaluate` skipped an evidence bundle with no registry entry and then exited
    0 with an empty, green report -- indistinguishable from full compliance.

Both are the kind of defect that only shows up on a real run, which is why they
are pinned here as properties over all rules rather than as one-off tests.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from conftest import bundle_from_html, make_target

from checker import report as reporting
from checker.api import evaluate, registered_rules
from checker.checks import aai_login, aup_pointer, english_available  # noqa: F401
from checker.declarative import load_rule_dir
from checker.models import Finding, RunReport, Severity, Verdict
from checker.registry import CrawlConfig, Target

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


def test_run_with_unevaluated_targets_is_not_trustworthy():
    """A collected-but-unevaluated target must never read as a clean run.

    This was a live bug: `collect -t targets/real.yaml` followed by a bare
    `evaluate` (which defaults to targets/targets.yaml) found no registry entry,
    skipped the bundle, produced zero findings, and exited 0 with a green report.
    Zero failures is exactly what full compliance looks like, so the run has to
    declare itself untrustworthy.
    """
    run = RunReport(run_id="r", skipped_targets=["some-node"])
    assert not run.is_trustworthy()
    assert run.blocking_failures() == []  # the trap: nothing looks wrong


def test_empty_run_is_not_trustworthy():
    assert not RunReport(run_id="r").is_trustworthy()


def test_run_with_findings_and_no_skips_is_trustworthy():
    run = RunReport(
        run_id="r",
        findings=[
            Finding(
                target_id="t",
                rule_id="X-01",
                rule_version="1.0.0",
                title="t",
                severity=Severity.MANDATORY,
                verdict=Verdict.PASS,
                message="ok",
            )
        ],
    )
    assert run.is_trustworthy()


def test_untrustworthy_run_is_flagged_in_both_report_formats(tmp_path):
    run = RunReport(run_id="r", skipped_targets=["some-node"])
    html = reporting.write_html(run, tmp_path / "r.html").read_text(encoding="utf-8")
    md = reporting.write_markdown(run, tmp_path / "r.md").read_text(encoding="utf-8")
    for text in (html, md):
        assert "not a compliance statement" in text
        assert "some-node" in text


def test_unprobed_links_are_reported_as_assumed_not_verified():
    """`link_is_live` returns True for links it never checked, by design.

    That is the right default -- a gap in collection must not become an
    accusation -- but it means a rule can emit a confident PASS on a link nobody
    verified. The report has to say which of the two it was.
    """
    html = (
        '<html lang="en"><head><title>Node</title></head><body>'
        '<a href="https://x.example/privacy">Privacy statement</a>'
        "</body></html>"
    )
    bundle = bundle_from_html(html)
    bundle.link_liveness = {}  # nothing was probed
    bundle.liveness_unprobed = ["https://x.example/privacy"]

    rules = load_rule_dir(Path("rules"))
    findings = evaluate(make_target(), bundle, only=["LP-PRIVACY-01"], today=date(2026, 1, 1))
    assert rules  # the rule pack loaded
    privacy = findings[0]
    assert privacy.verdict in (Verdict.PASS, Verdict.WARN)
    assert privacy.evidence[0].extra["liveness"] == "not probed (assumed reachable)"


def test_liveness_probing_only_targets_relevant_links():
    """Probing the whole site navigation is what made a real run take minutes."""
    from checker.registry import DEFAULT_LIVENESS_PATTERNS

    compiled = [re.compile(p) for p in DEFAULT_LIVENESS_PATTERNS]

    def relevant(url: str) -> bool:
        return any(rx.search(url) for rx in compiled)

    # Artefacts the rule pack asks about must be probed.
    for url in (
        "https://x.example/privacy-notice/",
        "https://x.example/services/access-policies/",
        "https://x.example/contact/",
        "https://x.example/catalogue",
        "https://x.example/legalnotice/",
    ):
        assert relevant(url), url

    # Ordinary site furniture must not be.
    for url in (
        "https://x.example/news/2026/annual-report",
        "https://x.example/staff/",
        "https://x.example/governance-structure/",
        "https://x.example/downloads/",
    ):
        assert not relevant(url), url


def test_crawl_scope_can_be_restricted_by_include_patterns():
    """max_pages is the wrong knob for a node page on a big institutional host.

    BBMRI-ERIC's node page lives on the host that carries the whole organisation,
    so any page budget either truncates before reaching the policy pages or walks
    hundreds of irrelevant ones. `include` bounds the crawl by relevance instead.
    """
    from checker.collect import _wanted

    target = Target(
        id="t",
        name="t",
        landing_page="https://x.example/eosc-node/",
        crawl=CrawlConfig(include=[r"/(privacy|contact|services|legal)"]),
    )
    entry = target.landing_page
    assert _wanted("https://x.example/privacy-notice/", target, entry) is True
    assert _wanted("https://x.example/services/access-policies/", target, entry) is True
    assert _wanted("https://x.example/contact/", target, entry) is True
    assert _wanted("https://x.example/staff/", target, entry) is False
    # Other hosts stay out of scope regardless of include patterns.
    assert _wanted("https://other.example/contact/", target, entry) is False
