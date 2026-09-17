"""Phase 3: reporting. JSON is the source of truth; everything else projects from it."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment

from .models import Finding, RunReport, Verdict

VERDICT_COLOUR = {
    Verdict.PASS: "#1a7f37",
    Verdict.FAIL: "#cf222e",
    Verdict.WARN: "#9a6700",
    Verdict.NOT_APPLICABLE: "#6e7781",
    Verdict.MANUAL_REVIEW: "#8250df",
    Verdict.ERROR: "#bc4c00",
}

HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Compliance report {{ report.run_id }}</title>
<style>
 body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
      margin:0;padding:2rem;color:#1f2328;background:#fff;max-width:1100px}
 h1{font-size:1.5rem;margin:0 0 .25rem} h2{font-size:1.15rem;margin:2rem 0 .5rem;
    padding-bottom:.3rem;border-bottom:1px solid #d1d9e0}
 .meta{color:#6e7781;font-size:.85rem;margin-bottom:1.5rem}
 .tally{display:flex;gap:.5rem;flex-wrap:wrap;margin:1rem 0}
 .pill{padding:.2rem .6rem;border-radius:999px;font-size:.8rem;font-weight:600;color:#fff}
 table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem;font-size:.9rem}
 th,td{text-align:left;padding:.5rem .6rem;border-bottom:1px solid #d1d9e0;vertical-align:top}
 th{background:#f6f8fa;font-weight:600}
 code{background:#f6f8fa;padding:.1rem .3rem;border-radius:4px;font-size:.85em}
 /* Rule ids must never break mid-identifier: "LP-CATALOGUE-01" wrapping to
    "LP-CATALOGUE-" / "01" makes the report look broken and is hard to scan. */
 td code.rid{white-space:nowrap}
 .v{font-weight:600;white-space:nowrap}
 .rem{color:#57606a;font-size:.85rem;margin-top:.3rem}
 .dg{color:#8250df;font-size:.8rem;margin-top:.2rem}
 /* An unassessed run must never be mistakable for a clean one. */
 .untrusted{border:2px solid #cf222e;background:#fff5f5;color:#82071e;
            padding:.75rem .9rem;border-radius:6px;margin:1rem 0;font-size:.9rem}
 details{margin-top:.35rem} summary{cursor:pointer;font-size:.8rem;color:#57606a}
 pre{background:#f6f8fa;padding:.5rem;border-radius:6px;overflow-x:auto;font-size:.75rem;
     white-space:pre-wrap;word-break:break-all;margin:.3rem 0 0}
</style></head><body>
<h1>Federation compliance report</h1>
<div class="meta">Run <code>{{ report.run_id }}</code> &middot;
 {{ report.started_at.strftime('%Y-%m-%d %H:%M UTC') }} &middot;
 {{ report.findings|length }} findings across {{ report.by_target()|length }} targets</div>

<div class="tally">
{% for verdict, n in counts.items() if n %}
 <span class="pill" style="background:{{ colours[verdict] }}">{{ verdict|replace('_',' ') }}: {{ n }}</span>
{% endfor %}
</div>

{% if not report.is_trustworthy() %}
<div class="untrusted">
 <strong>This report is not a compliance statement.</strong>
 {% if report.skipped_targets %}
 Evidence was collected for
 {% for t in report.skipped_targets %}<code>{{ t }}</code>{% if not loop.last %}, {% endif %}{% endfor %}
 but no registry entry defines their declarations and exemptions, so no rule was
 evaluated against them. An absence of failures below says nothing about these
 targets. Re-run <code>evaluate</code> with the <code>--targets</code> file that
 contains them.
 {% else %}
 It contains no findings at all: nothing was assessed.
 {% endif %}
</div>
{% endif %}

{% if report.blocking_failures() %}
<h2>Blocking failures ({{ report.blocking_failures()|length }})</h2>
<table><tr><th style="width:9rem">Target</th><th style="width:11.5rem">Rule</th><th>Message</th></tr>
{% for f in report.blocking_failures() %}
 <tr><td>{{ f.target_id }}</td>
     <td><code class="rid">{{ f.rule_id }}</code></td>
     <td>{{ f.message }}{% if f.remediation %}<div class="rem">&rarr; {{ f.remediation }}</div>{% endif %}</td></tr>
{% endfor %}
</table>
{% endif %}

{% for target_id, findings in report.by_target().items() %}
<h2>{{ target_id }}</h2>
<table>
 <tr><th style="width:11.5rem">Rule</th><th style="width:7rem">Verdict</th><th>Detail</th></tr>
 {% for f in findings %}
 <tr>
  <td><code class="rid">{{ f.rule_id }}</code><br><span class="meta">v{{ f.rule_version }}</span>
      {% if f.requirement_ref %}<br><span class="meta">{{ f.requirement_ref }}</span>{% endif %}</td>
  <td class="v" style="color:{{ colours[f.verdict] }}">{{ f.verdict|replace('_',' ') }}
      {% if f.confidence < 1.0 %}<br><span class="meta">conf {{ '%.2f'|format(f.confidence) }}</span>{% endif %}</td>
  <td>
    {% if f.target_detail %}<code>{{ f.target_detail }}</code><br>{% endif %}
    {{ f.message }}
    {% if f.remediation %}<div class="rem">&rarr; {{ f.remediation }}</div>{% endif %}
    {% if f.downgrade_reason %}<div class="dg">Downgraded from
        {{ f.downgraded_from }}: {{ f.downgrade_reason }}</div>{% endif %}
    {% if f.evidence %}<details><summary>Evidence ({{ f.evidence|length }})</summary>
      {% for e in f.evidence %}<pre>{% if e.locator %}locator: {{ e.locator }}
{% endif %}{% if e.matched_text %}matched: {{ e.matched_text[:200] }}
{% endif %}{% if e.url %}url: {{ e.url }}
{% endif %}{% if e.extra %}extra: {{ e.extra }}{% endif %}</pre>{% endfor %}
    </details>{% endif %}
  </td>
 </tr>
 {% endfor %}
</table>
{% endfor %}
</body></html>
"""


def write_json(report: RunReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_html(report: RunReport, path: Path) -> Path:
    env = Environment(autoescape=True)
    tpl = env.from_string(HTML_TEMPLATE)
    html = tpl.render(
        report=report,
        counts=report.counts(),
        colours={k.value: v for k, v in VERDICT_COLOUR.items()},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def write_markdown(report: RunReport, path: Path) -> Path:
    lines = [
        f"# Compliance report `{report.run_id}`",
        "",
        f"{report.started_at.strftime('%Y-%m-%d %H:%M UTC')} — "
        f"{len(report.findings)} findings across {len(report.by_target())} targets",
        "",
        "| Verdict | Count |",
        "|---|---|",
    ]
    for verdict, n in report.counts().items():
        if n:
            lines.append(f"| {verdict.replace('_', ' ')} | {n} |")

    if not report.is_trustworthy():
        detail = (
            "Evidence was collected for "
            + ", ".join(f"`{t}`" for t in report.skipped_targets)
            + " but no registry entry defines their declarations and exemptions, so no "
            "rule was evaluated against them. An absence of failures below says nothing "
            "about these targets."
            if report.skipped_targets
            else "It contains no findings at all: nothing was assessed."
        )
        lines += [
            "",
            "> **This report is not a compliance statement.**",
            ">",
            f"> {detail}",
        ]

    lines += ["", "## Findings", "", "| Target | Rule | Verdict | Message |", "|---|---|---|---|"]
    for f in report.findings:
        msg = f.message.replace("|", "\\|")[:180]
        detail = f" ({f.target_detail})" if f.target_detail else ""
        lines.append(
            f"| {f.target_id}{detail} | `{f.rule_id}` | {f.verdict.value.replace('_', ' ')} | {msg} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def diff_reports(previous: RunReport, current: RunReport) -> dict[str, list[str]]:
    """Run-over-run comparison. In a recurring process this beats absolute state."""

    def key(f: Finding) -> str:
        return f"{f.target_id}::{f.rule_id}::{f.target_detail or ''}"

    old = {key(f): f.verdict for f in previous.findings}
    new = {key(f): f.verdict for f in current.findings}
    out: dict[str, list[str]] = {"fixed": [], "regressed": [], "new": [], "resolved_away": []}
    for k, verdict in new.items():
        if k not in old:
            out["new"].append(f"{k} = {verdict.value}")
        elif old[k] != verdict:
            was_bad = old[k] in (Verdict.FAIL, Verdict.ERROR)
            now_bad = verdict in (Verdict.FAIL, Verdict.ERROR)
            if was_bad and not now_bad:
                out["fixed"].append(f"{k}: {old[k].value} -> {verdict.value}")
            elif now_bad and not was_bad:
                out["regressed"].append(f"{k}: {old[k].value} -> {verdict.value}")
    for k in old:
        if k not in new:
            out["resolved_away"].append(k)
    return out


def load_report(path: Path) -> RunReport:
    return RunReport.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
