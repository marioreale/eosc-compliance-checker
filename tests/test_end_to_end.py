"""One real end-to-end pass: browser -> evidence -> findings -> report.

Slow relative to the rule tests, so it is marked and kept to a single test. Its
job is to prove the collector and the rules agree about reality, which the
hermetic tests cannot do on their own.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from checker import report as reporting
from checker import store
from checker.api import evaluate, registered_rules
from checker.checks import aai_login, aup_pointer  # noqa: F401
from checker.collect import collect_target
from checker.declarative import load_rule_dir
from checker.models import RunReport, Verdict, utcnow
from checker.registry import TargetRegistry

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.slow

# Pinned so the governed verdicts are deterministic. Rules carry comply-by dates,
# so an unpinned `today` would silently change this test's expectations over time.
TODAY = date(2026, 9, 17)


@pytest.fixture(scope="module", autouse=True)
def _rules():
    load_rule_dir(ROOT / "rules")


def test_full_pipeline_against_fixture_server(fixture_server, tmp_path):
    registry = TargetRegistry.load(ROOT / "targets" / "targets.yaml")
    targets = [t for t in registry.enabled_targets() if t.id.startswith("fixture-")]
    assert targets, "expected fixture targets in targets.yaml"

    run_id = "e2e"
    evidence_root = tmp_path / "evidence"

    async def collect_all():
        out = []
        for target in targets:
            bundle = await collect_target(
                target, run_id, evidence_root / run_id / "artefacts", screenshots=False
            )
            store.save_bundle(bundle, evidence_root)
            out.append(bundle)
        return out

    bundles = asyncio.run(collect_all())

    # -- collection actually worked --------------------------------------
    by_id = {b.target_id: b for b in bundles}
    good = by_id["fixture-good"]
    assert good.entry_page.ok and good.entry_page.status == 200
    assert good.entry_page.title and "EOSC" in good.entry_page.title
    assert len(good.pages) > 1, "depth-1 crawl should have followed internal links"
    assert good.link_liveness, "link liveness probing should have produced results"

    # Round-trips through JSON without loss.
    reloaded = store.load_bundle(store.bundle_path(evidence_root, run_id, "fixture-good"))
    assert reloaded.entry_page.content_sha256 == good.entry_page.content_sha256

    # -- evaluation ------------------------------------------------------
    run = RunReport(run_id=run_id, rules_evaluated=sorted(registered_rules()))
    for bundle in bundles:
        run.findings.extend(evaluate(registry.get(bundle.target_id), bundle, today=TODAY))
    run.finished_at = utcnow()

    verdicts = {
        (f.target_id, f.rule_id): f.verdict for f in run.findings if not f.target_detail
    }

    # The clean fixture passes the headline rules.
    assert verdicts[("fixture-good", "LP-AAI-01")] == Verdict.PASS
    assert verdicts[("fixture-good", "LP-CONTACT-01")] == Verdict.PASS
    assert verdicts[("fixture-good", "LP-PRIVACY-01")] == Verdict.PASS
    assert verdicts[("fixture-good", "LP-LANG-01")] == Verdict.PASS

    # The adversarial fixture: login is a div+SVG, and must still be found.
    assert verdicts[("fixture-tricky", "LP-AAI-01")] == Verdict.PASS
    # lang="en" but Dutch content -> a human decides, never an automated FAIL.
    assert verdicts[("fixture-tricky", "LP-LANG-01")] == Verdict.MANUAL_REVIEW

    # Its privacy link 404s: present but dead is not compliance, so the rule
    # produced FAIL -- but LP-PRIVACY-01 is not mandatory until 2026-10-08, so
    # the governed verdict on TODAY is WARN with the original preserved. This is
    # the comply-by mechanism working end to end.
    tricky_privacy = next(
        f for f in run.findings
        if f.target_id == "fixture-tricky" and f.rule_id == "LP-PRIVACY-01"
    )
    assert tricky_privacy.verdict == Verdict.WARN
    assert tricky_privacy.downgraded_from == Verdict.FAIL
    assert "2026-10-08" in tricky_privacy.downgrade_reason
    # ... and once the date passes, the very same evidence blocks.
    after = evaluate(
        registry.get("fixture-tricky"), by_id["fixture-tricky"], only=["LP-PRIVACY-01"],
        today=date(2026, 11, 1),
    )
    assert after[0].verdict == Verdict.FAIL

    # The AUP link that satisfies the tricky fixture must be the visible footer
    # one, NOT the "Terms of use" link hidden inside its collapsed cookie banner.
    tricky_aup = next(
        f for f in run.findings
        if f.target_id == "fixture-tricky" and f.rule_id == "CAT-AUP-01"
    )
    assert tricky_aup.verdict == Verdict.PASS
    assert "cookies.html" not in tricky_aup.message, (
        "matched the hidden cookie-banner link instead of the real policy"
    )
    assert "aup.html" in tricky_aup.message

    # The declared-anonymous node is not failed for having no login button.
    assert verdicts[("fixture-anonymous", "LP-AAI-01")] == Verdict.NOT_APPLICABLE

    # The exempted node's missing contact route is carved out, with a reason.
    exempted = next(
        f for f in run.findings
        if f.target_id == "fixture-exempted" and f.rule_id == "LP-CONTACT-01"
    )
    assert exempted.verdict == Verdict.NOT_APPLICABLE
    assert exempted.downgraded_from == Verdict.FAIL
    assert "parent institutional portal" in exempted.downgrade_reason

    # No rule may ever crash on real input.
    assert not [f for f in run.findings if f.verdict == Verdict.ERROR], [
        f.message for f in run.findings if f.verdict == Verdict.ERROR
    ]

    # -- reporting -------------------------------------------------------
    json_path = reporting.write_json(run, tmp_path / "findings" / f"{run_id}.json")
    html_path = reporting.write_html(run, tmp_path / "reports" / f"{run_id}.html")
    md_path = reporting.write_markdown(run, tmp_path / "reports" / f"{run_id}.md")
    for path in (json_path, html_path, md_path):
        assert path.exists() and path.stat().st_size > 500

    html = html_path.read_text(encoding="utf-8")
    assert "fixture-tricky" in html
    assert "Blocking failures" in html  # fixture-bare has mandatory failures today

    # Every finding must carry traceable evidence or an explicit reason not to.
    for f in run.findings:
        assert f.evidence or f.downgrade_reason, f"{f.rule_id}/{f.target_id} has no evidence"

    # -- the diff, which is what a recurring process actually consumes ----
    blocking = run.blocking_failures()
    assert blocking, "the bare fixture should produce mandatory failures"
    regressed_rule = blocking[0]

    before = run.model_copy(deep=True)
    for f in before.findings:
        if f.target_id == regressed_rule.target_id and f.rule_id == regressed_rule.rule_id:
            f.verdict = Verdict.PASS
    delta = reporting.diff_reports(before, run)
    assert any(regressed_rule.rule_id in item for item in delta["regressed"]), delta
