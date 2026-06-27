# falsegreen

[![CI](https://github.com/vinicq/falsegreen/actions/workflows/ci.yml/badge.svg)](https://github.com/vinicq/falsegreen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/falsegreen.svg)](https://pypi.org/project/falsegreen/)
[![Python](https://img.shields.io/pypi/pyversions/falsegreen.svg)](https://pypi.org/project/falsegreen/)
[![Downloads](https://img.shields.io/pypi/dm/falsegreen.svg)](https://pypistats.org/packages/falsegreen)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Docs](https://img.shields.io/badge/docs-online-blue.svg)](https://vinicq.github.io/falsegreen-docs/)

**One problem, one tool: the false positive.** falsegreen finds Python/pytest tests that pass green without protecting anything — tests that let broken code through because the assertion is empty, always true, never runs, or checks the wrong thing.

A test that tells you a broken program is safe is worse than no test at all. AI coding assistants produce these at scale. The tool catches them before they merge.

The scanner is a zero-dependency AST pass. It validates each test against 34 active false-positive codes — patterns a parser can prove: an assertion that never runs, a check that is empty or always true, a swallowed exception, a mock assertion with a typo, a check stranded in dead code. HIGH findings block the commit; LOW ones warn. A third group (diagnostic and coupling, seven codes) can be enabled per project for informational checks that do not affect the exit code. The semantic layer — intent-based patterns no static tool can see — lives in [falsegreen-skill](https://github.com/vinicq/falsegreen-skill), the LLM companion covering Python and other languages.

The checks are grounded in the rotten-green-test research (Soares 2023; Delplanque et al., ICSE 2019) and cross-walked against the published test-smell catalog. See [CREDITS.md](CREDITS.md).

**The falsegreen family:** **falsegreen** (Python/pytest) · [falsegreen-js](https://github.com/vinicq/falsegreen-js) (JS/TS) · [robotframework-falsegreen](https://github.com/vinicq/robotframework-falsegreen) (Robot Framework) · [falsegreen-skill](https://github.com/vinicq/falsegreen-skill) (semantic LLM pass).

> **Install:** `pip install falsegreen`. Pre-commit hook also available (see below). LLM semantic pass: [falsegreen-skill](https://github.com/vinicq/falsegreen-skill).

---

## Table of contents

- [Why this exists](#why-this-exists)
- [The methodology](#the-methodology)
- [What it detects](#what-it-detects)
- [Codes the scanner does not detect](#codes-the-scanner-does-not-detect)
- [Diagnostic and coupling codes](#diagnostic-and-coupling-codes-opt-in)
- [The two layers](#the-two-layers)
- [Install and use](#install-and-use)
  - [1. CLI (pip)](#1-cli-pip)
  - [2. Pre-commit hook](#2-pre-commit-hook)
  - [3. Semantic pass (multi-language)](#3-semantic-pass-multi-language)
- [Configuration](#configuration)
- [How it compares](#how-it-compares)
- [Project layout](#project-layout)
- [Contributing, security, license](#contributing-security-license)

---

## Why this exists

Coverage tells you which lines ran. It does not tell you whether anything was checked. A suite can report 90% coverage while most of those tests assert nothing real — the green bar is a comfort, not a guarantee.

The danger is not a test that fails. It is a test that passes when it should not.

falsegreen targets that one problem. It is not a style linter and not a coverage tool. It asks one question per test: **is there a way for the code to be wrong and this test to stay green?** If yes, the test is not protecting what it claims.

This matters more now that a large share of tests come from AI assistants. The common machine-written failure modes are exactly the patterns here: assertion roulette, mocking the unit under test, asserting the value you fed the mock, copying the expected value from the current output. They look thorough. They pass. falsegreen is the second reader that asks whether they actually verify anything.

---

## The methodology

One rule drives everything: **a test is only useful if it fails when the code breaks.** If you have never seen a test go red, you do not know that it tests anything.

The patterns are organized into five families:

- **A. The test never checks anything.** The assertion is skipped, missing, swallowed, or the test is never collected by the runner.
- **B. The check is weak or always true.** It accepts almost any output, or it is true by construction.
- **C. The test checks itself, not the program.** It mocks the thing under test, asserts the value it configured, or re-implements the production logic as its own answer key.
- **D. Green depends on outside factors.** Time, randomness, test order, a fixed sleep.
- **E. The test passes but checks the wrong thing.** The assertion runs against a real result, yet the expected value contradicts what the code should do, so the test freezes a bug.

Family E is why the project has a semantic layer. It carries the core principle: the expected value must come from an **independent oracle**, not from the code's current output. The oracle hierarchy, highest first: explicit spec or requirement, documented contract (docstring, types, API), independent human judgment, current code. Code is the lowest-priority oracle. Treating it as the highest is how you rubber-stamp a bug.

The plain-language guide, with a real-world analogy and before/after for each case, is in [`docs/guide.md`](docs/guide.md). The full detection reference lives in [`falsegreen-skill`](https://github.com/vinicq/falsegreen-skill).

---

## What it detects

The scanner ships 40 active false-positive codes across the five families, plus `CC` (commented-out assert). HIGH findings block a commit; LOW ones warn. Cases that require reading production intent (10, 11, 12, 15, 18) need the semantic layer.

| # | Case | Why it fools you | Code | Conf |
|---|---|---|---|---|
| 1 | `assert` inside an `if`/`for` that may not run | check silently skips | `C1` | LOW |
| 2 | Test with no assertion at all | proves only it did not crash | `C2`/`C2b` | HIGH/LOW |
| 3 | `assert` inside `try` whose `except` swallows it | failure discarded | `C3` | HIGH |
| 4 | Test the runner never collects | vanishes from the count | `C4`/`C4b` | HIGH/LOW |
| 5 | Always-true check (`assert True`, non-empty tuple, `or True`) | passes by construction | `C5` | HIGH |
| 6 | Weak check (truthiness, `len > 0`, `"x" in str(...)`) | accepts almost anything | `C6` | LOW |
| 7 | Compares a value to itself | true by construction | `C7` | HIGH |
| 8 | Exact equality on a float | fails on rounding, not bugs | `C8` | LOW |
| 9 | `pytest.raises(Exception)` too broad | accepts the wrong error | `C9` | LOW |
| 10 | Mocks the unit under test | tests the mock, not the code | semantic | - |
| 11 | Asserts the value fed to the mock | an echo, not a result | semantic | - |
| 12 | Re-implements the production formula | both agree on the same wrong number | semantic | - |
| 13 | Mock assertion misspelled / not called | becomes a no-op | `C13`/`C13b` | HIGH/LOW |
| 14 | Golden/snapshot written from the output | records today's bug as correct | `C14` | LOW |
| 15 | Passes only if another test ran first | borrowed state | semantic | - |
| 16 | Depends on time, randomness, or a sleep | passes or fails by luck | `C16` | LOW |
| 17 | `skip` inside a broad `except` | turns red into yellow | `C17` | HIGH |
| 18 | Expected value contradicts what code should do | freezes a bug as correct | semantic | - |

Six codes cover patterns added after the original eighteen:

| Code | Pattern | Why it fools you | Family | Conf |
|---|---|---|---|---|
| `C18` | Compares `str()`/`repr()`/f-string to literal | checks formatting, not the value | B | LOW |
| `C19` | `pytest.raises` wraps more than one call | earlier line raises; target never reached | A | LOW |
| `C20` | `assert` in dead code after `return`/`raise`/`fail()` | never runs | A | HIGH |
| `C21` | Every `assert` is conditional, none unconditional | false condition passes the whole test | A | LOW |
| `C22` | `async` test asserts but never awaits the unit | checks an un-awaited coroutine | A | off |
| `C23` | Opens a real file at a literal hard-coded path | binds test to a layout; often a credential outside the repo (Mystery Guest) | D | LOW |

Eleven additional codes covering the most common patterns in real test suites:

| Code | Pattern | Conf |
|---|---|---|
| `C25` | `@pytest.mark.xfail` without `strict=True` — XPASS silently accepted | LOW |
| `C27` | `try/except/pass` used instead of `pytest.raises` | HIGH |
| `C28` | `pytest.raises` binding declared but exception content never inspected | LOW |
| `C29` | `os.environ` mutated directly in a test — state leaks between tests | LOW |
| `C30` | `responses.add()` / `httpretty.register_uri()` without activating the interceptor | LOW |
| `C31` | `capsys.readouterr()` result never asserted | LOW |
| `C32` | `@pytest.mark.skip` without `reason=` | LOW |
| `C33` | sklearn / ML metric computed but never asserted | LOW |
| `C34` | Suboptimal assert form — pytest provides a clearer, more idiomatic alternative | LOW |
| `C35` | Retry/flaky decorator masks flaky behaviour instead of fixing the root cause | LOW |
| `C36` | `pytest.fail()` with no reason argument — failure message is empty | LOW |
| `C37` | Duplicate case in `@pytest.mark.parametrize` — same argument set runs twice | LOW |
| `CC` | Commented-out assert | LOW |

Six more from the consolidated catalog:

| Code | Pattern | Conf |
|---|---|---|
| `C38` | Two test functions share a name — the later one silently overrides the first | HIGH |
| `C39` | Test `return`s a comparison instead of asserting it — pytest ignores the value | HIGH |
| `C42` | `assert` on a generator expression or lambda — the object is always truthy | HIGH |
| `C43` | `pytest.skip()` after test logic — the checks below it never run | LOW |
| `C44` | Numeric tautology (`len(x) >= 0`, `abs(x) >= 0`) — always true | HIGH |
| `C45` | Empty `@pytest.mark.parametrize` list — the test is generated with zero cases | HIGH |
| `C48` | Dark patch — the test forces a test-mode flag (`os.environ["TESTING"]`, `settings.TESTING`) then asserts, exercising the product's test-only branch | LOW |

### Codes the scanner does not detect

The static layer is close to saturated. A handful of catalog codes are deliberately
left out because a per-file AST pass cannot judge them without a high false-positive
rate, or because they are not a per-file property at all. They are listed here so the
gap is honest, not hidden. The reasoning follows the consolidated catalog.

**High false-positive without deeper analysis (left to the semantic pass).**

- `C40` (assert on a `Mock` attribute with no spec, always truthy): without spec or
  autospec analysis the false-positive rate is high, since the same shape is a valid
  check on a real object. The concept lives in the skill (Family F7).
- `C41` (assert on an in-place method that returns `None`, like `assert not lst.sort()`):
  whether it is trivially green depends on the receiver's type, which the parser cannot
  see. Restricted to known mutators it would still misfire on look-alikes, so it is left
  to the semantic pass. `C41` holds a catalog row and a fix hint (so it shows up in the
  rule list and the JSON output), but no detector is wired to it: the scanner never
  emits a `C41` finding.
- `C46` (real network or database call with no double): legitimate at the integration
  level, where crossing the boundary is the point. Flagging it per file, without knowing
  the test's layer, is a high false-positive. It belongs to the skill and the project
  layer.
- `C47` (assertion depends on dict or set ordering): most collections are used
  deterministically, so flagging unordered-vs-sequence comparisons fires far too often.
  It stays a note in the skill.

**Runtime and culture (not a per-file property).** The `PL` series is about how the
suite is invoked and configured, not what a single test file contains. `PL2`, `PL7`,
and `PL8` are already covered by `--config-audit` (warnings not promoted to errors, no
coverage gate, `addopts` that stops the run early). The rest need execution or pipeline
inspection: `PL1` (`python -O` / `PYTHONOPTIMIZE` strips every `assert` at runtime),
`PL4` (a collection error counted as "0 tests" while CI stays green), and
`PL3`, `PL5`, `PL6` (a coverage pragma in production code, `importorskip` hiding a broken
import, CI running a subset via `-k` / `-m`). They are documented, not promised, and sit
outside the "test file" target.

**Semantic Family E or F7 (mutation testing and the skill).** Mocking the unit under
test, asserting the value you fed the mock, re-implementing the production formula,
borrowing state from another test, an expected value that contradicts the spec: none of
these can be proven by structure. `C14` (a snapshot generated from the code's own output)
is the only codable corner of this family. The honest path for the rest is mutation
testing (mutmut, cosmic-ray), which mutates the production code and checks whether any
test goes red, plus the LLM semantic pass in
[falsegreen-skill](https://github.com/vinicq/falsegreen-skill).

**How the scanner detects.** It parses each test file with Python's `ast` module and inspects the tree. It never imports or runs the test, so a malicious or broken test cannot execute through it. Detection is structural: an `assert` whose expression is a constant, both sides of a comparison AST-identical, a `pytest.raises` argument of `Exception`, a mock-named receiver with a no-parentheses `assert_called_once`, a `Test*` class with `__init__`, and so on. Precision is the priority for HIGH codes, because they block commits: each one is stress-tested against look-alikes (optional-dependency skips, abstract base test classes, `@patch`-injected mocks, exact-count `len(x) == N`) and stays quiet on them.

**How the semantic pass detects.** Cases 10, 11, 12, 15, and 18 cannot be proven by structure. A parser sees a mock but cannot tell whether it replaced an edge (network, disk, clock) or the thing under test. It sees an arithmetic expression but cannot tell whether the expected value was derived independently or copied from the code. That judgment requires reading the production code against an independent oracle — that is what [falsegreen-skill](https://github.com/vinicq/falsegreen-skill) does.

**Why two confidence levels.** A blocking gate that cries wolf gets disabled. So only near-certain, mechanically unambiguous patterns are HIGH (they block). The rest are LOW (they warn) and are starting points for human or semantic judgment, not verdicts.

### How falsegreen is validated

A tool that flags tests for not protecting anything has to show it protects something itself.

- **The scanner (deterministic).** Every rule ships with two tests: one proving it fires on the bad pattern, one proving it stays quiet on a legitimate look-alike. The scanner also runs on its own source on every commit (the self-scan), because the false-positive detector is not allowed to contain one. It is also validated against real-world Python projects — the most recent corpus run covered 40 projects with over 58,000 test functions. That pass surfaced false positives in two rule classes (C7 on deliberate `__eq__` tests, C4 on test-named route handlers). Both were fixed, each with regression tests. The HIGH count across all 40 projects after fixes: 0. Each false positive is recorded in the commit history and the CHANGELOG.
- **The semantic pass (LLM).** Validation for the LLM-based semantic layer is tracked in [falsegreen-skill](https://github.com/vinicq/falsegreen-skill), where benchmark corpora for Python and TypeScript are maintained with precision/recall measurements.

---

## Test levels (the pyramid)

falsegreen scans tests at every level of the pyramid. Discovery is level-agnostic - it reads
any pytest/unittest file - but a few codes are read in light of the level, so a valid pattern
at one level is not flagged at another.

- **Unit:** a function with its boundaries doubled. The oracle is `assert` (or `self.assert*`).
- **Integration (API and database):** API tests through `requests`/`httpx` or a framework
  TestClient (FastAPI, Flask, Django), database tests against a real datastore (SQLAlchemy,
  the Django ORM, testcontainers). These cross the I/O boundary on purpose, so the response
  or row IS the verification at that level. The weak-check code (C6) relaxes in the web layer,
  where the presence of a response is a real check.
- **E2E:** Playwright for Python and Selenium. `expect(locator).to_be_visible()` is the oracle.

A real API or database hit inside a test that claims to be a unit test is itself the smell
(mystery guest, resource optimism, state leak), not the level of the test. C23 (real file at
a literal path), C29 (`os.environ` mutated), and C30 (mock interceptor never activated) flag
those forms.

## Diagnostic and coupling codes (opt-in)

Seven additional codes surface smells that do not create false positives but hurt observability and maintainability. All are **off by default**. Enable with `severity = { CODE = "info" }` in config. `info` findings appear in separate DIAGNOSTIC and COUPLING sections and do not affect the exit code.

| Code | Smell | What it flags |
|---|---|---|
| `C22` | Async Liar | `async def test_*` that asserts but never `await`s the unit |
| `D1` | Assertion Roulette | 2+ assertions in one test, all without a `msg` argument |
| `D3` | Duplicate Assert | the same assertion written twice in the same test body |
| `D4` | Unnamed Parametrize | `@pytest.mark.parametrize` with 3+ cases and no `ids=` |
| `D5` | Inline Setup Excess | too many setup statements before the first `assert` (threshold configurable) |
| `D6` | Debug Print | `print()` call in test body |
| `M2` | Long Test Method | test body exceeds `long_test_threshold` lines (default 50) |

---

## The two layers

| Layer | What it is | When it runs | Catches |
|---|---|---|---|
| **Scanner** (this repo) | Zero-dependency AST analysis | CLI, CI, pre-commit | 34 active false-positive codes + 7 opt-in diagnostic codes |
| **Semantic pass** ([falsegreen-skill](https://github.com/vinicq/falsegreen-skill)) | LLM-based analysis, Python and other languages | on demand | bug-freezing patterns no static tool can see (cases 10/11/12/15/18) |

The scanner is the fast, deterministic pre-filter. For TypeScript, JavaScript, Java, and other languages, use [falsegreen-skill](https://github.com/vinicq/falsegreen-skill).

---

## What we don't flag (and why)

Measured against the [Open Catalog of Test Smells](https://test-smell-catalog.readthedocs.io/) (517 documented smells), only the false-green slice is in scope. These stay out, on purpose:

- **Brittleness / false-red** (a test that breaks without a real bug): sensitive equality, brittle or fragile assertions. The opposite axis; flagging it would punish correct code.
- **Hygiene / maintainability**: assertion roulette, magic numbers, long or verbose tests. Linter territory (ruff), and a few are surfaced here as opt-in diagnostics (`D1`, `M2`).
- **Slow, design, naming, duplication, runtime/culture**: none are about whether the test protects.

The boundary is deliberate. Where a smell has a statically provable false-green form, that form is a code here: uncontrolled time or randomness is `C16`, a hard-coded path is `C23`, shared module state is `C24`, an assertion that may never run is `C21`. See [CREDITS.md](CREDITS.md) for the full cross-walk against the literature.

---

## Install and use

### 1. CLI (pip)

```bash
pip install falsegreen
```

No permanent install needed: `uvx falsegreen tests/` or `pipx run falsegreen tests/` runs the latest release from PyPI without touching your environment.

```bash
falsegreen                        # scan the current directory
falsegreen tests/                 # scan a folder or a single file
falsegreen --staged               # only the test files staged in git
falsegreen --format sarif         # text (default) | json | sarif | junit
falsegreen --summary              # one-line "N scanned, M flagged" to stderr
falsegreen --output report.sarif  # write to a file
falsegreen --output .falsegreen/  # write report.<ext> into a directory
falsegreen --config-audit         # audit pytest/coverage config (project-layer PL codes)
falsegreen --disable C6,C2b       # turn specific codes off
```

`--config-audit` is a separate mode: instead of scanning test files, it reads the project's pytest and coverage config (`pyproject.toml`, `pytest.ini`, `tox.ini`, `setup.cfg`) and reports the project-layer ways a suite stays green by configuration: `PL2` (`filterwarnings` does not promote warnings to errors), `PL7` (no coverage gate), `PL8` (`addopts` stops the run early with `-x`/`--maxfail`). These complement the per-file scan, which cannot see config.

Each finding is reported with its pyramid level (unit / integration / e2e, read from the file's imports) and a one-line fix hint, and the text summary breaks the findings down by level and lists the most common fixes. `--output` takes a file or a directory: an extension-less or trailing-slash path (e.g. `.falsegreen/`) receives `report.<ext>` for the chosen format. Reports are run artifacts; keep the output directory gitignored.

`--format sarif` emits SARIF 2.1.0 (HIGH → error, LOW → warning) for GitHub code scanning and PR annotations. `--format junit` emits JUnit XML for CI dashboards.

`python -m falsegreen ...` is equivalent. Exit codes: `0` clean, `10` low-confidence findings only, `20` at least one high-confidence finding. No third-party runtime dependencies; Python 3.8+.

### 2. Pre-commit hook

Add to `.pre-commit-config.yaml`:

```yaml
  - repo: https://github.com/vinicq/falsegreen
    rev: v0.3.0
    hooks:
      - id: falsegreen
```

Then `pre-commit install`. On each commit it scans the staged test files. HIGH findings block the commit. Bypass once with `git commit --no-verify`, or set `FALSEGREEN_BLOCK=0` to make the hook warn-only.

Raw git hook (without the pre-commit framework):

```bash
python -m falsegreen.hook_install --repo .      # install
python -m falsegreen.hook_install --uninstall   # remove
```

### 3. Semantic pass (multi-language)

For cases that require reading production intent (mocking the unit under test, copying expected from current output, re-implementing the formula), use [falsegreen-skill](https://github.com/vinicq/falsegreen-skill). It covers Python, TypeScript, JavaScript, Java, and other languages.

---

## Configuration

- **Inline suppression:** `# falsegreen: ignore` silences all codes on a line; `# falsegreen: ignore[C8]` silences one.
- **Disable codes globally:** `--disable C6,C2b`.
- **Environment:** `FALSEGREEN_BLOCK=0` makes the pre-commit hook warn instead of block.

### Project config file

`[tool.falsegreen]` in `pyproject.toml`, or a flat `.falsegreen.toml` at the repo root (`.falsegreen.toml` wins if both exist):

```toml
[tool.falsegreen]
disable = ["C13b"]            # turn these codes off everywhere
exclude = ["tests/legacy/*"]  # skip files matching these globs
long_test_threshold = 30      # line-count limit for M2 (default: 50)
inline_setup_threshold = 3    # stmt limit for D5 (default: 5)

[tool.falsegreen.severity]
C8 = "high"    # promote: now blocks the commit (exit 20)
C6 = "off"     # same as adding C6 to disable
C22 = "low"    # enable: async-never-awaits check
D1 = "info"    # enable Assertion Roulette
D3 = "info"    # enable Duplicate Assert
D4 = "info"    # enable Unnamed Parametrize
D5 = "info"    # enable Inline Setup Excess
D6 = "info"    # enable Debug Print
M2 = "info"    # enable Long Test Method
```

`severity` values: `high`, `low`, `info`, or `off`. `info` findings appear in DIAGNOSTIC/COUPLING sections and do not affect the exit code. `long_test_threshold` and `inline_setup_threshold` are top-level keys in `[tool.falsegreen]`, not inside `[severity]`. Precedence, highest first: `--disable` CLI, inline `# falsegreen: ignore`, config file, built-in default. Point at a specific file with `--config PATH`.

### Baseline (adopt on a legacy repo)

Record the findings you already have, then fail only on new ones:

```bash
falsegreen --write-baseline tests/   # writes .falsegreen-baseline.json, exits 0
falsegreen --baseline tests/         # suppresses recorded findings, fails on new
```

A finding is fingerprinted by relative path, code, detail, and normalized source line — not line number, so prepending code does not re-trigger a baselined finding. Commit `.falsegreen-baseline.json` and the ratchet only tightens.

---

## How it compares

- **ruff / flake8-pytest-style** — mature, fast lint rules. Overlaps on broad `raises` (PT011) and assert-in-except (PT017). Run both: falsegreen adds uncollected tests, always-true asserts, self-comparison, mock typos, duplicate parametrize cases, and more.
- **PyNose / pytest-smell** — test-smell catalogs from research. Broader taxonomy, but no commit gate and no oracle-correctness check.
- **mutmut / cosmic-ray** — mutation testing, the most honest measure of whether a green suite fails when the code is wrong. Complementary and heavier. falsegreen is the cheap pre-filter you run on every commit; mutation testing is the deep audit you run on suites that matter.
- **[falsegreen-skill](https://github.com/vinicq/falsegreen-skill)** — the LLM companion for the semantic pass and for TypeScript, JavaScript, Java, and other languages.

The defensible gap: a deterministic commit gate that catches the mechanical false-positive patterns with zero runtime dependencies, paired with an LLM semantic layer that catches the oracle-correctness cases no static tool can see.

---

## Project layout

```
falsegreen/
  src/falsegreen/scanner.py        the deterministic scanner
  src/falsegreen/hook_install.py   raw git-hook installer
  docs/guide.md                    plain-language guide to every case
  examples/python/                 a BAD + CLEAN sample for every detected code
  tests/test_scanner.py            the scanner's own tests
  .pre-commit-hooks.yaml           pre-commit integration
  pyproject.toml                   packaging
```

---

## Contributing, security, license

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, how to add a detection rule, the false-positive policy, Conventional Commits.
- [SECURITY.md](SECURITY.md) — how to report a vulnerability privately.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1.
- [CREDITS.md](CREDITS.md) — research falsegreen builds on (Soares rotten-green work, PyNose, test-smell catalog, agentic-LLM studies), with author credit.
- License: **MIT**, see [LICENSE](LICENSE).

## Contributors ✨

Thanks to the people who keep false-green tests out of real suites ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-2-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://vinicq.github.io/md-bridge/"><img src="https://avatars.githubusercontent.com/u/78210890?v=4?s=100" width="100px;" alt="Vinicius Queiroz"/><br /><sub><b>Vinicius Queiroz</b></sub></a><br /><a href="https://github.com/vinicq/falsegreen/commits?author=vinicq" title="Code">💻</a> <a href="https://github.com/vinicq/falsegreen/commits?author=vinicq" title="Documentation">📖</a> <a href="#ideas-vinicq" title="Ideas, Planning, & Feedback">🤔</a> <a href="#maintenance-vinicq" title="Maintenance">🚧</a> <a href="#infra-vinicq" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a> <a href="https://github.com/vinicq/falsegreen/commits?author=vinicq" title="Tests">⚠️</a> <a href="#research-vinicq" title="Research">🔬</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/homesellerq-coder"><img src="https://avatars.githubusercontent.com/u/294912019?v=4?s=100" width="100px;" alt="Home Seller"/><br /><sub><b>Home Seller</b></sub></a><br /><a href="https://github.com/vinicq/falsegreen/commits?author=homesellerq-coder" title="Code">💻</a> <a href="https://github.com/vinicq/falsegreen/commits?author=homesellerq-coder" title="Documentation">📖</a> <a href="#infra-homesellerq-coder" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

New contributors are added automatically; the table also recognizes non-code work (docs, ideas, infrastructure, tests, research) via the [all-contributors](https://allcontributors.org) spec.
