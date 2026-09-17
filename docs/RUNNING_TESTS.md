# Running the test suite

Two routes to the same 63 tests: ask Perplexity to do it, or run it yourself on a
Debian VM. The second is the one to use if you need a result you can defend —
you see every command and every byte of output.

---

## Part 1 — Via Perplexity

### What to ask for

The agent has a Linux sandbox with the repository already checked out at
`/home/user/workspace/compliance-checker`. Plain requests work:

| Goal | What to say |
|---|---|
| Whole suite | "Run the compliance checker test suite and show me the output." |
| Fast subset | "Run only the hermetic tests — skip the ones needing a browser." |
| One area | "Run just the AUP rule tests." |
| Lint too | "Run ruff and the full test suite." |
| After a change | "Pull the latest `main` from GitHub, then run the suite." |
| Diagnose a failure | "Run the suite; if anything fails, show me the failing assertion and the rule it covers." |

A fresh session starts with an empty sandbox, so open with:

> Clone https://github.com/marioreale/eosc-compliance-checker, install its
> dependencies with uv, then run the test suite.

### What comes back

Expect `63 passed in ~34s`. If something fails you get the test name, the
assertion, and which rule it pins.

### Worth knowing

- **Ask for the exact commands** if you want to reproduce the run yourself. The
  agent will list them rather than just narrating.
- **A green result here is not a compliance statement.** The suite tests the
  *checker*, not any node. Scanning a real node is a separate request
  ("run the checker against <URL>").
- **The sandbox is ephemeral.** Anything not pushed to GitHub disappears with the
  session. If a fix gets made, ask for it to be committed and pushed.
- **Long runs get cut off.** The sandbox kills a command at ~10 minutes. The test
  suite is nowhere near that, but a real multi-node scan can be, so ask for
  output to be written to a file and tailed.
- **Don't accept a summary in place of a run.** If you are told tests pass, the
  transcript should contain the actual `pytest` invocation and its output.

---

## Part 2 — Manually on a Debian VM

Commands below were run and their output checked, on Python 3.14 with uv; the
Debian-specific steps follow Debian 12 (bookworm) / 13 (trixie), x86_64.

### 1. System packages

```bash
sudo apt update
sudo apt install -y git curl
```

### 2. Install uv

The project needs **Python ≥ 3.12**. Debian 12 ships 3.11, so do not use the
system interpreter — `uv` fetches and manages its own.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"     # or open a new shell
uv --version
```

### 3. Clone

```bash
git clone https://github.com/marioreale/eosc-compliance-checker.git
cd eosc-compliance-checker
```

Public repository — no credentials needed to read. To push you need your own
access; with the GitHub CLI: `gh auth login`.

### 4. Install dependencies

```bash
uv sync --extra lang
```

Creates `.venv/`, resolves from the committed `uv.lock` (so you get the same
versions CI does), and adds the `lang` extra — `lingua`, used by the
English-availability rule. Without it those tests skip rather than fail.

### 5. Install Chromium

Collection drives a real browser. This pulls the browser build **and** its system
libraries, so it needs sudo and downloads a few hundred MB:

```bash
uv run playwright install chromium --with-deps
```

On a headless VM that is all that's required — no X server, no `xvfb`.

### 6. Run the tests

```bash
uv run pytest
```

Expect:

```
63 passed in 34.35s
```

### Useful variations

```bash
# Hermetic only: no browser, no network. 62 of the 63 tests, in under a second.
uv run pytest -m "not slow"

# Just the end-to-end run against local fixture pages.
uv run pytest -m slow

# One file, verbose.
uv run pytest tests/test_invariants.py -v

# One test by name.
uv run pytest -k unprobed_links -v

# Stop at the first failure, with the local variables at the failure point.
uv run pytest -x -l

# Lint, as CI runs it.
uv run ruff check src tests

# JUnit XML, the same artifact CI publishes.
uv run pytest --junitxml=junit.xml
```

### Skipping step 5

If you cannot install a browser, `uv run pytest -m "not slow"` runs everything
that doesn't need one — 62 of 63 tests, in 0.8s. Only the end-to-end collection
test is skipped. That ratio is by design: the rules are pure functions over
saved evidence bundles, so almost nothing needs a browser to test.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `uv: command not found` | The installer put it in `~/.local/bin`. `source "$HOME/.local/bin/env"` or start a new shell. |
| `Host system is missing dependencies` | Chromium's libraries are absent. Re-run step 5 including `--with-deps`. |
| Browser tests fail, hermetic ones pass | Chromium missing or broken: `uv run playwright install chromium --with-deps`. |
| `address already in use` on 8009 | A stale fixture server. `conftest.py` reuses a live one, so this is usually a half-dead process: `pkill -f tests.serve_fixtures`. |
| `ModuleNotFoundError: lingua` | Installed without the extra. `uv sync --extra lang`. |
| `requires-python >=3.12` | You invoked system `pytest` instead of `uv run pytest`. Always prefix with `uv run`. |
| Tests pass locally, fail in CI | Usually the `lang` extra or a browser difference. Compare against the `junit-results` artifact on the run. |

### Updating later

```bash
cd eosc-compliance-checker
git pull
uv sync --extra lang     # in case dependencies moved
uv run pytest
```

---

## Where CI shows the same results

Every push and pull request runs lint plus the full suite on GitHub Actions.

1. Go to the [Actions tab](https://github.com/marioreale/eosc-compliance-checker/actions).
2. Open a run — the **compliance scan** workflow.
3. The **Summary** page carries a rendered table of every test with timings, so
   you don't have to read the logs. Raw JUnit XML is attached as the
   `junit-results` artifact.
4. On a pull request, failures are annotated inline on the diff.

Note the two jobs have deliberately different contracts. `rule tests` is
hermetic, runs on every push, and may block a merge. `nightly scan` touches
other institutions' infrastructure, runs only on a schedule or manual dispatch,
and never blocks anyone — it shows as skipped on a normal push, which is correct
and not a failure.
