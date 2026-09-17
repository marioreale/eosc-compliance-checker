"""Command line interface.

    checker collect   --targets targets/targets.yaml
    checker evaluate  --run <run-id>
    checker report    --run <run-id>
    checker run       # collect + evaluate + report
    checker rules     # list registered rules
    checker diff      --previous a.json --current b.json
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import (
    checks,  # noqa: F401  -- registers the tier-2 rule pack
    store,
)
from . import report as reporting
from .api import evaluate as evaluate_bundle
from .api import registered_rules
from .collect import collect_target, new_run_id
from .declarative import load_rule_dir
from .models import RunReport, Verdict, utcnow
from .registry import TargetRegistry

app = typer.Typer(add_completion=False, help="Federated web compliance scanner")
console = Console()

RULES_DIR = Path("rules")


def _load_declarative_rules() -> None:
    """Register tier-1 YAML rules from the governance directory, if present."""
    if RULES_DIR.is_dir():
        load_rule_dir(RULES_DIR)

VERDICT_STYLE = {
    Verdict.PASS: "green",
    Verdict.FAIL: "bold red",
    Verdict.WARN: "yellow",
    Verdict.NOT_APPLICABLE: "dim",
    Verdict.MANUAL_REVIEW: "magenta",
    Verdict.ERROR: "bold orange3",
}


@app.command()
def collect(
    targets: Path = typer.Option(Path("targets/targets.yaml"), "--targets", "-t"),
    evidence_root: Path = typer.Option(Path("evidence"), "--evidence"),
    run_id: str = typer.Option("", "--run"),
    only: str = typer.Option("", "--only", help="Comma-separated target ids"),
    screenshots: bool = typer.Option(True, "--screenshots/--no-screenshots"),
) -> None:
    """Phase 1: visit targets and write evidence bundles."""
    registry = TargetRegistry.load(targets)
    wanted = [t.strip() for t in only.split(",") if t.strip()]
    selected = [t for t in registry.enabled_targets() if not wanted or t.id in wanted]
    if not selected:
        console.print("[red]No matching enabled targets[/]")
        raise typer.Exit(2)

    rid = run_id or new_run_id()
    artefacts = evidence_root / rid / "artefacts"

    async def _go() -> None:
        for target in selected:
            console.print(f"[cyan]collecting[/] {target.id} → {target.landing_page}")
            bundle = await collect_target(target, rid, artefacts, screenshots=screenshots)
            path = store.save_bundle(bundle, evidence_root)
            ok = sum(1 for p in bundle.page_list() if p.ok)
            console.print(f"  {ok}/{len(bundle.pages)} pages captured → {path}")

    asyncio.run(_go())
    console.print(f"\n[green]run id:[/] {rid}")


@app.command()
def evaluate(
    targets: Path = typer.Option(Path("targets/targets.yaml"), "--targets", "-t"),
    evidence_root: Path = typer.Option(Path("evidence"), "--evidence"),
    run_id: str = typer.Option("", "--run"),
    findings_dir: Path = typer.Option(Path("findings"), "--findings"),
    rule: str = typer.Option("", "--rule", help="Comma-separated rule ids"),
) -> None:
    """Phase 2: evaluate rules against stored evidence. No network access."""
    registry = TargetRegistry.load(targets)
    rid = run_id or store.latest_run_id(evidence_root)
    if not rid:
        console.print("[red]No evidence found; run 'collect' first[/]")
        raise typer.Exit(2)

    _load_declarative_rules()
    only = [r.strip() for r in rule.split(",") if r.strip()] or None
    run = RunReport(run_id=rid, rules_evaluated=sorted(registered_rules()))

    for bundle in store.load_run(evidence_root, rid):
        target = registry.get(bundle.target_id)
        if target is None:
            run.skipped_targets.append(bundle.target_id)
            console.print(
                f"[red]cannot evaluate {bundle.target_id}: no entry in {targets}[/]\n"
                "  Rules need the registry for declarations and exemptions, so this\n"
                "  bundle is NOT assessed. Did you collect with a different -t file?"
            )
            continue
        run.findings.extend(evaluate_bundle(target, bundle, only=only, today=None))

    run.finished_at = utcnow()
    path = reporting.write_json(run, findings_dir / f"{rid}.json")
    _print_summary(run)
    console.print(f"\n[green]findings:[/] {path}")

    # Refuse to let an unassessed run look like a clean one. Zero findings and
    # zero failures is exactly what a fully compliant run looks like, so this
    # has to be loud and it has to be a non-zero exit.
    if not run.is_trustworthy():
        if run.skipped_targets:
            console.print(
                f"\n[bold red]{len(run.skipped_targets)} target(s) collected but not "
                f"evaluated:[/] {', '.join(run.skipped_targets)}\n"
                "[bold red]This run is NOT a compliance statement.[/]"
            )
        else:
            console.print(
                "\n[bold red]No findings produced. Nothing was assessed; this run is "
                "NOT a compliance statement.[/]"
            )
        raise typer.Exit(2)


@app.command()
def report(
    run_id: str = typer.Option("", "--run"),
    findings_dir: Path = typer.Option(Path("findings"), "--findings"),
    out_dir: Path = typer.Option(Path("reports"), "--out"),
    fail_on_mandatory: bool = typer.Option(
        True, "--fail-on-mandatory/--no-fail", help="Exit non-zero on mandatory FAILs"
    ),
) -> None:
    """Phase 3: render HTML and Markdown from findings JSON."""
    rid = run_id or _latest_findings(findings_dir)
    if not rid:
        console.print("[red]No findings found; run 'evaluate' first[/]")
        raise typer.Exit(2)
    run = reporting.load_report(findings_dir / f"{rid}.json")
    html = reporting.write_html(run, out_dir / f"{rid}.html")
    md = reporting.write_markdown(run, out_dir / f"{rid}.md")
    _print_summary(run)
    console.print(f"\n[green]html:[/] {html}\n[green]markdown:[/] {md}")

    if not run.is_trustworthy():
        console.print(
            "\n[bold red]This report is not a compliance statement:[/] "
            + (
                f"{len(run.skipped_targets)} target(s) were collected but never "
                f"evaluated ({', '.join(run.skipped_targets)})."
                if run.skipped_targets
                else "it contains no findings at all."
            )
        )
        raise typer.Exit(2)

    blocking = run.blocking_failures()
    if blocking and fail_on_mandatory:
        console.print(f"\n[bold red]{len(blocking)} mandatory failure(s)[/]")
        raise typer.Exit(1)


@app.command("run")
def run_all(
    targets: Path = typer.Option(Path("targets/targets.yaml"), "--targets", "-t"),
    evidence_root: Path = typer.Option(Path("evidence"), "--evidence"),
    findings_dir: Path = typer.Option(Path("findings"), "--findings"),
    out_dir: Path = typer.Option(Path("reports"), "--out"),
    only: str = typer.Option("", "--only"),
) -> None:
    """collect + evaluate + report in one go."""
    rid = new_run_id()
    collect(targets=targets, evidence_root=evidence_root, run_id=rid, only=only, screenshots=True)
    evaluate(
        targets=targets, evidence_root=evidence_root, run_id=rid, findings_dir=findings_dir, rule=""
    )
    report(run_id=rid, findings_dir=findings_dir, out_dir=out_dir, fail_on_mandatory=True)


@app.command()
def rules() -> None:
    """List registered rules and their governance metadata."""
    _load_declarative_rules()
    table = Table("id", "version", "severity", "mandatory from", "applies to", "title")
    for rule_id, (meta, _) in sorted(registered_rules().items()):
        table.add_row(
            rule_id,
            meta.version,
            meta.severity.value,
            meta.mandatory_from.isoformat() if meta.mandatory_from else "-",
            ", ".join(meta.applies_to),
            meta.title,
        )
    console.print(table)


@app.command()
def diff(
    previous: Path = typer.Option(..., "--previous"),
    current: Path = typer.Option(..., "--current"),
) -> None:
    """Compare two findings files."""
    result = reporting.diff_reports(
        reporting.load_report(previous), reporting.load_report(current)
    )
    for section, items in result.items():
        if items:
            console.print(f"\n[bold]{section}[/] ({len(items)})")
            for item in items:
                console.print(f"  {item}")
    if not any(result.values()):
        console.print("[dim]no changes[/]")


def _print_summary(run: RunReport) -> None:
    table = Table("target", "rule", "verdict", "message", show_lines=False)
    for f in run.findings:
        detail = f" [{f.target_detail}]" if f.target_detail else ""
        table.add_row(
            f.target_id + detail,
            f.rule_id,
            f"[{VERDICT_STYLE[f.verdict]}]{f.verdict.value}[/]",
            f.message[:88],
        )
    console.print(table)
    counts = {k: v for k, v in run.counts().items() if v}
    console.print("  ".join(f"{k}={v}" for k, v in counts.items()))


def _latest_findings(findings_dir: Path) -> str | None:
    if not findings_dir.is_dir():
        return None
    files = sorted(findings_dir.glob("*.json"), reverse=True)
    return files[0].stem if files else None


if __name__ == "__main__":
    app()
