"""Tier 1: declarative YAML rules.

Most requirements in a checklist reduce to "does a thing matching these patterns
exist on this page". Those should be editable by a policy person in a text file
without a Python review cycle. This module compiles such YAML into the same
CheckFn signature the Python rules use, so downstream code cannot tell them apart.

Deliberately small. The moment a rule needs branching, promote it to tier 2
rather than growing a DSL — an under-powered DSL that keeps growing is how these
projects die.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .api import CheckContext, check
from .extract import snippet_around
from .models import Evidence, Finding, Severity, Verdict

# Where in the evidence a predicate looks.
SCOPES = ("controls", "links", "text", "lang_attr", "headers")


def _negative_evidence(page, locator: str, examined: int, unit: str = "items") -> Evidence:
    """Record what was searched when nothing matched.

    An unevidenced FAIL is unarguable in the wrong direction: the node operator
    cannot see what the tool looked at, so they cannot tell you it looked in the
    wrong place. Every verdict this project emits carries its own audit trail.
    """
    return Evidence(
        url=page.final_url,
        locator=locator,
        extra={f"examined_{unit}": examined, "result": "no match"},
    )


def _eval_predicate(ctx: CheckContext, page, spec: dict[str, Any]) -> tuple[bool, list[Evidence]]:
    scope = spec.get("scope", "text")
    patterns = spec.get("any_of") or spec.get("patterns") or []
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; expected one of {SCOPES}")

    if scope == "controls":
        hits = ctx.match_controls(page, patterns)
        if not hits:
            return False, [
                _negative_evidence(page, "a, button, [role=button]", len(page.controls))
            ]
        return True, [
            Evidence(
                locator=h.tag,
                matched_text=h.all_text[:200],
                snippet_html=snippet_around(page.html, h.all_text),
                url=page.final_url,
                screenshot=page.screenshot,
                extra={"href": h.href},
            )
            for h in hits[:3]
        ]

    if scope == "links":
        matched = ctx.match_links(page, patterns)
        require_live = spec.get("require_live", False)
        satisfying = [h for h in matched if ctx.link_is_live(h.href)] if require_live else matched

        def link_evidence(links, note=None):
            return [
                Evidence(
                    locator="a[href]",
                    matched_text=h.text[:200],
                    url=page.final_url,
                    extra={
                        "href": h.href,
                        "status": ctx.evidence.link_liveness.get(h.href),
                        # Make an unverified link visible in the report rather
                        # than letting "assumed live" read as "checked and live".
                        "liveness": (
                            "verified"
                            if ctx.link_liveness_verified(h.href)
                            else "not probed (assumed reachable)"
                        ),
                        **({"note": note} if note else {}),
                    },
                )
                for h in links[:3]
            ]

        if satisfying:
            return True, link_evidence(satisfying)
        # "Matched but dead" and "never matched" are different problems with
        # different owners, so they must not produce the same evidence.
        if matched:
            return False, link_evidence(
                matched, note="matched the requirement but the link is unreachable"
            )
        return False, [_negative_evidence(page, "a[href]", len(page.links))]

    if scope == "text":
        import re

        for pattern in patterns:
            match = re.search(pattern, page.text)
            if match:
                return True, [
                    Evidence(
                        locator="main text",
                        matched_text=match.group(0)[:200],
                        url=page.final_url,
                    )
                ]
        return False, [
            _negative_evidence(page, "main text", len(page.text), unit="chars")
        ]

    if scope == "lang_attr":
        value = (page.lang_attr or "").lower()
        ok = any(value.startswith(p.lower()) for p in patterns)
        return ok, [Evidence(url=page.final_url, extra={"lang_attr": page.lang_attr})]

    header_name = str(spec.get("header", "")).lower()  # scope == "headers"
    value = {k.lower(): v for k, v in page.headers.items()}.get(header_name, "")
    import re

    ok = any(re.search(p, value) for p in patterns) if patterns else bool(value)
    return ok, [Evidence(url=page.final_url, extra={"header": header_name, "value": value})]


def _make_check(spec: dict[str, Any]):
    scope_pages = spec.get("pages", "entry")  # "entry" | "all"
    predicate = spec["assert"]
    pass_msg = spec.get("pass_message", "Requirement satisfied.")
    fail_msg = spec.get("fail_message", "Requirement not satisfied.")
    remediation = spec.get("remediation")
    requires_declaration = spec.get("requires_declaration")
    skip_if_declared = spec.get("skip_if_declared")

    def run(ctx: CheckContext) -> list[Finding]:
        if skip_if_declared and ctx.declares(skip_if_declared):
            return [
                ctx.finding(
                    Verdict.NOT_APPLICABLE,
                    message=f"Node declares {skip_if_declared!r}; rule does not apply.",
                )
            ]
        if requires_declaration and not ctx.declares(requires_declaration):
            return [
                ctx.finding(
                    Verdict.NOT_APPLICABLE,
                    message=f"Rule only applies to nodes declaring {requires_declaration!r}.",
                )
            ]

        pages = [ctx.entry] if scope_pages == "entry" else ctx.pages()
        pages = [p for p in pages if p is not None]
        if not pages or not any(p.ok for p in pages):
            return [
                ctx.finding(
                    Verdict.ERROR, message="No usable page evidence; cannot evaluate."
                )
            ]

        collected: list[Evidence] = []
        for page in pages:
            if not page.ok:
                continue
            ok, evidence = _eval_predicate(ctx, page, predicate)
            collected.extend(evidence)
            if ok:
                return [ctx.finding(Verdict.PASS, message=pass_msg, evidence=evidence)]

        return [
            ctx.finding(
                Verdict.FAIL,
                message=fail_msg,
                remediation=remediation,
                evidence=collected[:5],
            )
        ]

    run.__doc__ = spec.get("description", "")
    return run


def load_rule_file(path: str | Path) -> list[str]:
    """Load and register every rule in one YAML file. Returns the rule ids."""
    docs = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    specs = docs.get("rules", []) if isinstance(docs, dict) else docs
    ids = []
    for spec in specs:
        mandatory_from = spec.get("mandatory_from")
        if isinstance(mandatory_from, str):
            mandatory_from = date.fromisoformat(mandatory_from)
        decorator = check(
            id=spec["id"],
            version=str(spec.get("version", "1.0.0")),
            title=spec["title"],
            requirement_ref=spec.get("requirement_ref"),
            severity=Severity(spec.get("severity", "mandatory")),
            mandatory_from=mandatory_from,
            applies_to=spec.get("applies_to"),
            description=spec.get("description", ""),
        )
        decorator(_make_check(spec))
        ids.append(spec["id"])
    return ids


def load_rule_dir(directory: str | Path) -> list[str]:
    ids: list[str] = []
    for path in sorted(Path(directory).rglob("*.yaml")):
        ids.extend(load_rule_file(path))
    return ids
