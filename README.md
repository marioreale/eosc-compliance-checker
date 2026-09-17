# Federated web compliance checker

Automated conformance checking for a set of distributed web properties — in this
case EOSC Federation node landing pages and service catalogues — against a
published checklist of requirements.

It is **not** a test suite. It is an evidence-collecting scanner with an audit
trail. That distinction drives the whole design.

## The pipeline

```
targets/targets.yaml
        │
        ▼
   ┌─────────┐   evidence/<run>/<target>.json    ┌──────────┐
   │ COLLECT │ ────────────────────────────────► │ EVALUATE │
   └─────────┘   full DOM, links, controls,      └──────────┘
   browser,      headers, text, screenshots,          │ pure, no network
   the only      link liveness, catalogue JSON        ▼
   networked                                    findings/<run>.json
   component                                          │
                                                      ▼
                                                 ┌────────┐
                                                 │ REPORT │ html · md · diff
                                                 └────────┘
```

The persisted evidence bundle between the two phases is the single most
important design decision here. It buys you:

- **Re-evaluation without re-visiting.** Change a rule, re-run `evaluate`, get
  new verdicts against last night's evidence. You are hitting other
  institutions' infrastructure; do it once.
- **Trivially fast rule development.** Rules are pure functions over a JSON
  document. No browser in the loop, no flakiness, millisecond test runs.
- **A real audit trail.** Every verdict points at the exact matched text,
  locator, URL and screenshot it came from. When a node operator disputes a
  finding — and they will — you can show them what was on the page.
- **Deterministic regression tests.** Freeze an evidence bundle as a fixture and
  a rule's behaviour is pinned forever.

## Quick start

```bash
uv sync --extra lang                          # or: pip install -e '.[lang]'
uv run playwright install chromium --with-deps

uv run checker rules                          # list rules and their metadata
uv run pytest -q                              # hermetic rule tests

# End-to-end against the bundled fixture server:
uv run python -m tests.serve_fixtures &        # serves :8009
uv run checker run                            # collect + evaluate + report
open reports/*.html
```

## The verdict model

A boolean pass/fail is the wrong shape for compliance work, which is why this
does not sit on top of pytest.

| Verdict         | Meaning                                                           |
| --------------- | ----------------------------------------------------------------- |
| `PASS`          | Requirement demonstrably satisfied.                               |
| `FAIL`          | Requirement demonstrably not satisfied.                           |
| `WARN`          | Adverse, but not blocking — recommended rule, or before comply-by. |
| `NOT_APPLICABLE`| Rule does not apply: declaration or agreed exemption.             |
| `MANUAL_REVIEW` | The tool cannot decide. A human must. **Not a failure.**           |
| `ERROR`         | *We* failed — timeout, TLS, bot wall. Never the target's fault.    |

Two rules follow from this table and they matter more than any code here:

1. **A collection failure must never become a compliance failure.** If you
   cannot reach a site, that is `ERROR`, and it belongs in your operations
   backlog, not in a node's assessment.
2. **A heuristic check must never emit a confident `FAIL`.** Its adverse outcome
   is `MANUAL_REVIEW`. One false accusation of non-compliance costs you more
   political capital than fifty manual reviews cost you time.

## Three rule tiers

| Tier | Where | For | Example |
| --- | --- | --- | --- |
| 1 | `rules/**/*.yaml` | Pattern presence — the majority of a checklist. Editable by a policy owner, no Python review. | `LP-CONTACT-01`, `LP-PRIVACY-01` |
| 2 | `src/checker/checks/*.py` | Real logic: branching, near-miss reporting, per-service iteration. | `LP-AAI-01`, `CAT-AUP-01` |
| 3 | Tier 2 + degradation | Irreducibly fuzzy questions. Combines signals, reports confidence, falls back to a human. | `LP-LANG-01` |

Both tiers compile to the same `CheckFn` signature, so nothing downstream knows
or cares which tier a finding came from. When a tier-1 rule starts wanting an
`if`, promote it to tier 2 rather than growing the YAML into a programming
language — an under-powered DSL that keeps growing is how these projects die.

`rules/` deliberately sits at the top level, not under `src/`. It is a
governance artefact, co-owned with whoever owns the checklist, and it is a
reasonable candidate for its own repository with its own review process.

## Governance: exemptions and comply-by dates

Requirements phase in; they do not switch on overnight. Two mechanisms handle
this, applied uniformly in `api.apply_governance` after every rule runs:

- `mandatory_from` on a rule — before that date, a `FAIL` is emitted as `WARN`
  with `downgraded_from` and a reason recorded.
- `exemptions` on a target — an active, time-boxed, attributed carve-out turns
  `FAIL` into `NOT_APPLICABLE`.

The original verdict is always preserved on the finding, so "what would fail if
we enforced today?" remains answerable. A `NOT_APPLICABLE` is always traceable
to a line in `targets.yaml`.

## Politeness

You are running automated traffic against other institutions' production
infrastructure, on a schedule, without their staff watching. Non-negotiables,
all implemented in `collect.py`:

- Honest `User-Agent` naming the tool with a contact URL and email.
- One request in flight per host, with a real delay between them.
- Bounded crawl: depth and page caps, host-locked, exclusion patterns.
- `robots.txt` respected by default.
- Read-only. No form submission, no login attempts, no state mutation.

Tell the node operators before you point this at them. A scanner that gets your
IP range blocked has negative value.

## What is deliberately not here

Skipped for v1 on purpose: database, web dashboard, Kubernetes, Terraform,
devcontainers, queue, auth. A nightly GitHub Actions job writing JSON and HTML
into an artefact covers the actual requirement. Add infrastructure when a
concrete need appears, not before.

## Repository layout

```
targets/targets.yaml        who we scan, declarations, exemptions
rules/                      tier-1 declarative rules (governance artefact)
src/checker/
  models.py                 evidence + finding schema, versioned
  registry.py               target registry loader
  extract.py                HTML -> signals (pure, unit-testable)
  collect.py                Playwright + httpx. The only networked module.
  store.py                  evidence bundle persistence
  api.py                    CheckContext, @check, governance, evaluate()
  declarative.py            YAML -> CheckFn compiler
  checks/                   tier-2 and tier-3 Python rules
  report.py                 JSON / HTML / Markdown / run diff
  cli.py                    collect · evaluate · report · run · rules · diff
tests/
  conftest.py               html -> evidence helpers, fixture web server
  fixtures/pages/           adversarial HTML (see below)
.github/workflows/scan.yml  nightly scan + PR rule tests
```

## The adversarial fixtures are the point

`tests/fixtures/pages/tricky/` encodes traps taken from real institutional
sites. A checker that passes the clean fixture but mishandles these is worthless
in production:

- login control is a `<div role="button">`, not an `<a>` or `<button>`;
- its only readable label lives in an SVG `aria-label`;
- a hidden legacy login link that must be ignored;
- an AUP-looking link inside a hidden cookie banner;
- `lang="en"` declared while the body content is Dutch;
- a privacy link that resolves to 404 — present but dead.

Write the awkward fixture first, then the rule. Most of the value of this kind
of tool is in correctly handling markup nobody would design on purpose.

## CI behaviour

`checker report` exits non-zero **only** when a mandatory rule fails past its
`mandatory_from` date. Recommended-rule failures, manual reviews and collection
errors do not break the build. If a red pipeline can be caused by someone else's
web server being slow, people stop looking at the pipeline.

## Extending it

Add a tier-1 rule: append to a file in `rules/`. Nothing else to touch.

Add a tier-2 rule:

```python
from ..api import CheckContext, check
from ..models import Evidence, Finding, Severity, Verdict

@check(id="LP-SLA-01", version="1.0.0",
       title="Service level description published",
       severity=Severity.RECOMMENDED)
def sla_published(ctx: CheckContext) -> list[Finding]:
    page = ctx.entry
    hits = ctx.match_links(page, [r"(?i)service\s+level"])
    if hits:
        return [ctx.finding(Verdict.PASS, message=f"SLA linked: {hits[0].href}")]
    return [ctx.finding(Verdict.FAIL, message="No service level description linked.")]
```

Import it in `checks/__init__.py`, add a test with `bundle_from_html`, done.

## Known limitations

These are real and deliberate. Each one is a decision you may want to revisit
before pointing this at the eleven candidate nodes.

**Rule patterns are English-only.** `LP-CATALOGUE-01` FAILs on the
`fixture-tricky` target because that page's navigation says *Diensten*, not
*Services*. The page is compliant; the rule is monolingual. This is the single
most important thing to fix before a real run against European nodes, and there
are only two honest ways to fix it: extend each tier-1 pattern list with the
languages you actually expect, or promote the rule to tier 2 and match on
structure (a link whose target returns a catalogue-shaped payload) rather than on
label text. Do not "fix" it by loosening the regex until it matches everything.

**Visibility is a static approximation.** `extract._is_visible` walks ancestors
looking for `hidden`, `aria-hidden="true"` and inline `display:none` /
`visibility:hidden`. It does not resolve stylesheets or computed geometry, so a
link hidden purely by a CSS class in an external stylesheet still counts as
visible. If a rule needs certainty, call Playwright's `is_visible()` during
collection and record the answer in the evidence, rather than re-deriving it
from HTML later.

**One entry page per target.** Multi-domain nodes (a portal on one host, the
catalogue on another) need either two registry entries or a `crawl.extra_hosts`
option that does not exist yet.

**Link liveness is a HEAD request.** Servers that reject HEAD, or that return 200
with a soft-404 body, will be judged wrongly. The four-way concurrency limit and
the `PER_HOST_DELAY_S` politeness gap also mean a broad crawl is slow by design.

**`CAT-AUP-01` reads the catalogue API optimistically.** It looks for any of
`accessPolicy`, `aup`, `termsOfUse` and friends in the JSON. There is no agreed
schema yet, so a node using a different field name will be reported as missing a
policy it actually publishes. Fix the field list when the catalogue schema is
settled, not by guessing more names.

**No accessibility rules yet.** The `a11y` extra is declared and the evidence
bundle stores rendered HTML, so an axe-core pass is a tier-2 rule away, but
nothing implements it.
