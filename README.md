# falsegreen

[![CI](https://github.com/vinicq/falsegreen/actions/workflows/ci.yml/badge.svg)](https://github.com/vinicq/falsegreen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/falsegreen.svg)](https://pypi.org/project/falsegreen/)
[![Python](https://img.shields.io/pypi/pyversions/falsegreen.svg)](https://pypi.org/project/falsegreen/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Unit-test quality, focused on one failure: the false positive.** falsegreen
finds Python/pytest tests that pass green without protecting anything, and tests
that pass while asserting the wrong value.

A green test that never fails when the code breaks is worse than no test: it tells
you a broken program is safe. AI coding assistants produce these in volume. The
tool catches them in two layers.

The scanner is a zero-dependency AST pass that runs on every commit. It validates
each test against more than twenty mechanical smells, the ones a parser can prove:
an assertion that never runs, a check that is empty or always true, a swallowed
exception, a mock of the unit under test, an assertion stranded in dead code, a
weak truthiness check, an async test that never awaits. High-confidence findings
block the commit; the rest warn. The Claude Code skill then does the part a parser
cannot: it reads the production code and judges whether each test asserts the
*right* value, measured against the intended behavior rather than the current
(possibly buggy) output.

The checks are grounded in the rotten-green-test research (Soares 2023; Delplanque
et al., ICSE 2019) and cross-walked against the published test-smell catalog. See
[CREDITS.md](CREDITS.md).

> Live on PyPI: `pip install falsegreen`. Also a pre-commit hook and a Claude Code
> plugin (see the three install paths below).

---

## Table of contents

- [Why this exists](#why-this-exists)
- [The methodology and its basis](#the-methodology-and-its-basis)
- [What it validates, how, and why](#what-it-validates-how-and-why)
- [The two layers](#the-two-layers)
- [Download and use: the three ways](#download-and-use-the-three-ways)
  - [1. As a Python package (CLI, no skill needed)](#1-as-a-python-package-cli-no-skill-needed)
  - [2. As a pre-commit hook](#2-as-a-pre-commit-hook)
  - [3. As a Claude Code skill (the semantic pass)](#3-as-a-claude-code-skill-the-semantic-pass)
  - [With the skill vs without the skill](#with-the-skill-vs-without-the-skill)
- [Configuration](#configuration)
- [Technologies used](#technologies-used)
- [How it compares](#how-it-compares)
- [Project layout](#project-layout)
- [Contributing, security, license](#contributing-security-license)

---

## Why this exists

Coverage tells you which lines ran. It does not tell you whether anything was
checked. A suite can report 90% coverage while a chunk of those tests assert
nothing real, so the green bar is a comfort, not a guarantee. The danger is not a
test that fails. It is a test that passes when it should not.

falsegreen targets that single, high-cost problem: the false positive. It is not
a general style linter and it is not a coverage tool. It answers one question per
test: **is there a way for the code to be wrong and this test to stay green?** If
the answer is yes, the test is not protecting what it claims.

This matters more now that a large share of tests are written by AI assistants.
The common machine-written failure modes are exactly the ones here: assertion
roulette, mocking the unit under test, asserting the value you fed the mock,
copying the expected value from the current output. They look thorough and they
pass. falsegreen is the second reader that asks whether they actually verify
anything.

---

## The methodology and its basis

One rule sits under everything: **a test is only useful if it fails when the code
breaks.** If you have never seen a test go red, you do not know that it tests
anything. Every pattern falsegreen flags is a variation on tests that never fail,
fail for the wrong reason, or check the wrong thing.

The patterns are organized into five families:

- **A. The test never checks anything.** The assertion is skipped, missing, or
  swallowed, or the test is never even collected by the runner.
- **B. The check exists but is weak or always true.** It accepts almost any
  output, or it is true by construction.
- **C. The test checks itself, not the program.** It mocks the thing under test,
  asserts the value it configured, or re-implements the production logic as its
  own answer key.
- **D. Green depends on outside factors.** Time, randomness, test order, a fixed
  sleep.
- **E. The test passes but checks the wrong thing.** The assertion runs and
  compares a real result, yet the expected value contradicts what the code should
  do, so the test freezes a bug.

Family E is the reason the project has a semantic layer, and it carries the core
principle of the methodology: the expected value must come from an **independent
oracle**, never from the code's current output. The oracle hierarchy, highest
first, is: explicit spec or requirement, documented contract (docstring, types,
API doc), independent human judgment, and only last the current code. Code is the
lowest-priority oracle. Promoting it above the others is how you end up
rubber-stamping a bug.

Because intent changes which oracle is authoritative, the semantic pass first
classifies the test: a spec/TDD test (the test is the authority), a
characterization test (it intentionally freezes current behavior), a regression
test for a known bug, or a plain behavior test. A red TDD test is not a false
positive, and a labeled characterization snapshot is not a frozen bug. That
classification step keeps the tool from flagging legitimate styles.

The plain-language guide behind every case, with a real-world analogy and a
before/after for each, is in [`docs/guide.md`](docs/guide.md). The detection
reference that maps each code to its scanner code and to established tooling is in
[`skills/falsegreen/reference.md`](skills/falsegreen/reference.md).

The basis is the rotten-green-test research: a passing test that holds an
assertion which never runs (Elvys Soares, *A Multimethod Study of Test Smells*,
2023; Delplanque et al., *Rotten Green Tests*, ICSE 2019). The catalog of patterns
is cross-walked against the published test-smell catalog and against PyNose, the
Python test-smell detector. Mutation testing (`mutmut`, `cosmic-ray`) is the honest
measure of whether a suite actually fails when the code is wrong. Every reference,
and the specific thing falsegreen took from each one, is in [CREDITS.md](CREDITS.md).

---

## What it validates, how, and why

The catalog has 18 named cases across the five families, and the scanner now ships
21 codes (the five families are the scanner-facing view; the semantic pass asks the
same questions as six judgments, J1 to J6, which is the LLM-facing view of the same
thing, mapped code by code in [`reference.md`](skills/falsegreen/reference.md)). A
case is caught either by the deterministic **scanner** (a code like `C5`) or only
by the **semantic** pass (it needs to read the production code). HIGH-confidence
scanner findings block a commit; LOW ones warn.

| # | Case | Why it fools you | Detected by | Conf |
|---|---|---|---|---|
| 1 | `assert` inside an `if`/`for` that may not run | the check silently skips | `C1` | LOW |
| 2 | Test with no assertion at all | proves only that it did not crash | `C2`/`C2b` | HIGH/LOW |
| 3 | `assert` inside a `try` whose `except` swallows it | the failure is discarded | `C3` | HIGH |
| 4 | Test the runner never collects | it vanishes from the count | `C4`/`C4b` | HIGH/LOW |
| 5 | Always-true check (`assert True`, non-empty tuple, `or True`) | passes by construction | `C5` | HIGH |
| 6 | Weak check (truthiness, `len>0`, `"x" in str(...)`) | accepts almost anything | `C6` | LOW |
| 7 | Compares a value to itself | true by construction | `C7` | HIGH |
| 8 | Exact equality on a float | fails on rounding, not on bugs | `C8` | LOW |
| 9 | `pytest.raises(Exception)` too broad | accepts the wrong error too | `C9` | LOW |
| 10 | Mocks the unit under test | tests the mock, not the code | semantic | - |
| 11 | Asserts the value fed to the mock | an echo, not a result | semantic | - |
| 12 | Re-implements the production formula | both agree on the same wrong number | semantic | - |
| 13 | Mock assertion misspelled / not called | becomes a no-op that always passes | `C13`/`C13b` | HIGH/LOW |
| 14 | Golden/snapshot written from the output | records today's bug as correct | `C14` | LOW |
| 15 | Passes only if another test ran first | borrowed state | semantic | - |
| 16 | Depends on time, randomness, or a sleep | passes or fails by luck | `C16` | LOW |
| 17 | `skip` inside a broad `except` | turns red into yellow, hides the defect | `C17` | HIGH |
| 18 | Expected value contradicts what the code should do | freezes a bug as "correct" | semantic | - |

Five codes added after the original eighteen, each in an existing family:

| Code | Pattern | Why it fools you | Family | Conf |
|---|---|---|---|---|
| `C18` | Compares `str()`/`repr()`/an f-string to a literal | checks formatting, not the value | B | LOW |
| `C19` | `pytest.raises` block wraps more than one call | an earlier line raises, the target is never reached | A | LOW |
| `C20` | `assert` in dead code after `return`/`raise`/`fail()` | the check never runs | A | HIGH |
| `C21` | Every `assert` is conditional, none runs unconditionally | a false condition passes the whole test | A | LOW |
| `C22` | `async` test asserts but never awaits the unit | the assertion checks an un-awaited coroutine | A | off by default |

(`CC`, a commented-out `assert`, is also flagged LOW.)

**How the scanner detects.** It parses each test file with Python's `ast` module
and inspects the tree. It never imports or runs the test, so a malicious or broken
test cannot execute through it. Detection is structural: an `assert` whose
expression is a constant, both sides of a comparison being AST-identical, a
`pytest.raises` whose argument is `Exception`, a mock-named receiver with a
no-parentheses `assert_called_once`, a `Test*` class with an `__init__`, and so
on. Precision is the priority for HIGH codes, because they block commits: each one
is stress-tested against legitimate look-alikes (optional-dependency skips,
abstract base test classes, `@patch`-injected mocks, exact-count `len(x) == N`)
and stays quiet on them.

**How the semantic pass detects.** Cases 10, 11, 12, 15, and 18 cannot be proven
by structure. A parser sees a mock but cannot tell whether it replaced an edge
(network, disk, clock) or the thing under test. It sees an arithmetic expression
but cannot tell whether the expected value was derived independently or copied
from the code. The `/falsegreen` skill reads the production code, derives the
intended behavior from the oracle hierarchy, compares it against what the test
asserts, and when they disagree, names which side is wrong. It is told to favor
precision over recall and to ground a verdict in a cited contract line, never in
the code's current output alone.

**Why two confidence levels.** A blocking gate that cries wolf gets disabled. So
only near-certain, mechanically-unambiguous patterns are HIGH (they block). The
rest are LOW (they warn) and are starting points for human or semantic judgment,
not verdicts.

### How falsegreen is validated

A tool that flags tests for not protecting anything has to show it protects
something itself. The two layers are validated differently, because they have
different natures.

- **The scanner (deterministic).** Every rule ships with two tests: one proving it
  fires on the bad pattern, and at least one proving it stays quiet on a legitimate
  look-alike. The second matters more. The scanner also runs on its own source on
  every commit, the self-scan, because the false-positive detector is not allowed
  to contain one. And it is validated against real-world Python projects (Python
  web and service codebases with large test suites), where it has found and fixed
  its own false positives before they shipped. The most recent pass ran the
  scanner over 8 projects, each above 200 GitHub stars with 500 or more test
  functions (httpx, starlette, flask, fastapi, django-rest-framework, aiohttp,
  sanic, werkzeug). That pass surfaced about 47 HIGH-confidence false positives in
  two rule classes (C7 on deliberate `__eq__` tests, C4 on test-named route
  handlers and local callbacks). Both were fixed, each with a fires-on-bad and a
  stays-clean regression test, and a re-scan brought the HIGH count to 0 across all
  8 projects. Every project it has been run on, what each run revealed, and the
  decision that followed are logged in [VALIDATION.md](VALIDATION.md).
- **The semantic pass (LLM, any language).** Cross-language coverage runs through
  this pass, so its reliability is measured, not assumed. The validation is a
  benchmark corpus: tests planted with a known ground truth, a test that mocks the
  unit under test, one that copies the expected value from current output, one that
  re-implements the production formula, in Python and in other languages, scored for
  precision and recall with precision held above recall. Because the pass runs on an
  LLM it is non-deterministic, so this is a periodic skill-validation artifact, not
  a CI gate. The first labeled corpus has 24 Python cases (10 rotten, 14 sound)
  across cases 10, 11, 12, and 18, with sound look-alikes and plain controls. Run
  blind on a small model (Claude Haiku), the pass scored precision 1.00 (no false
  alarms on the 14 sound tests), recall 0.70, and 1.00 recall on the clear-cut
  smells; the only misses were borderline cases (a pure-delegation passthrough, a
  trivial one-operator formula) where the precision-first guardrail defers to
  "sound". That is the evidence behind the design claim that a small model is
  enough for a precision-first semantic pass. The number to grow is recall: a
  larger corpus, a second annotator, and multi-vote runs are the next step. A
  second corpus of 20 TypeScript cases (Jest/Vitest) reproduced the pattern:
  precision 1.00, recall 0.625, with the only misses being the same boundary
  cases, evidence that the pass carries across languages and frameworks, not just
  Python.

---

## What falsegreen takes from the research, and what it leaves out

The detection logic is a deliberate subset of the test-smell literature, not a copy
of it. falsegreen keeps the smells that make a passing test ineffective and drops
the rest on purpose. The specific debt to each work, with citations, is in
[CREDITS.md](CREDITS.md); the short version:

- From the **rotten-green-test work** (Soares 2023; Delplanque et al., 2019): the
  core definition (a passing test with an assertion that never runs) and the rules
  that follow from it, `C1`, `C20`, `C21`, plus the AAA Assert framing.
- From the **test-smell catalog**: the semantic-logic slice, reorganized as the six
  judgments. The other seven categories (naming, size, duplication, performance,
  manual tests) stay out.
- From **PyNose**: the confirmation that the `unittest`/xUnit dialect is a real gap
  worth covering. PyNose's maintainability smells (Assertion Roulette, Redundant
  Print, Lack of Cohesion, Test Maverick) stay out.
- From the **LLM studies** (Agentic LMs, SBES 2025; Santana Jr. et al., 2025): the
  method, an LLM agent that detects and proposes fixes, validated by a
  coverage-and-mutation check. Santana Jr. et al. found standalone LLM refactoring
  drops coverage and recommend exactly that validating agent, which is why the
  AI-fix path here is gated, not trusted.

Why leave the rest out: a cloned test, a badly named test, an Eager Test, an
Assertion Roulette still goes red when the code is wrong. They cost maintainability,
not protection. Flagging them as false positives would itself be a false positive,
and precision over recall is the whole point. `ruff`'s `PT` rules and PyNose cover
that maintainability layer well; run them alongside falsegreen.

---

## The two layers

| Layer | What it is | When it runs | Catches |
|---|---|---|---|
| **Scanner** | Zero-dependency AST analysis (Python/pytest), one self-contained module | CLI, CI, pre-commit | the mechanical patterns (21 codes) |
| **Semantic pass** | A Claude Code skill (`/falsegreen`) that reads the code | on demand, in Claude Code | the bug-freezing patterns no static tool can see (cases 10/11/12/15/18) |

The scanner is the fast, deterministic pre-filter. It overlaps in part with
`ruff`'s `PT` rules and with research tools like PyNose, and that overlap is fine:
run them together. The semantic pass is the part nobody else ships, and it is the
reason the project exists.

The semantic pass runs on whatever Claude model your Claude Code session uses. It
is not pinned to one model, and it does not need a frontier one: the research it
draws on (Agentic LMs, SBES 2025; Santana Jr. et al., 2025) shows that small,
locally-runnable models detect and refactor these patterns well. The value is in
the protocol, not in any single model.

---

## Download and use: the three ways

Pick one or combine them. The CLI and pre-commit need no Claude Code; the skill
adds the semantic pass on top.

### 1. As a Python package (CLI, no skill needed)

Install from PyPI:

```bash
pip install falsegreen
```

Run it:

```bash
falsegreen                      # scan the current directory
falsegreen tests/               # scan a folder or a single file
falsegreen --staged             # only the test files staged in git
falsegreen --format sarif       # text (default) | json | sarif | junit
falsegreen --summary            # one-line "N scanned, M flagged" to stderr
falsegreen --output report.sarif  # write the formatted output to a file
falsegreen --json               # alias for --format json
falsegreen --disable C6,C2b     # turn specific codes off
```

`--format sarif` emits SARIF 2.1.0 (HIGH -> error, LOW -> warning) for GitHub
code scanning / PR annotations; `--format junit` emits JUnit XML (HIGH ->
`<failure>`, LOW -> `<skipped>`) for CI dashboards.

`python -m falsegreen ...` is equivalent to the `falsegreen` command. Exit codes:
`0` clean, `10` low-confidence findings only, `20` at least one high-confidence
finding. Wire those into any CI step. No third-party runtime dependencies; Python
3.8+.

Try it on the bundled demo (one bad test per case):

```bash
pipx run falsegreen skills/falsegreen/examples/bad_tests_sample.py
```

### 2. As a pre-commit hook

This is the standard, version-pinned way to gate every commit. Add to your
`.pre-commit-config.yaml`:

```yaml
  - repo: https://github.com/vinicq/falsegreen
    rev: v0.1.0
    hooks:
      - id: falsegreen
```

Then `pre-commit install`. On each commit it scans the staged test files.
**HIGH-confidence findings block the commit.** Bypass once with
`git commit --no-verify`, or set `FALSEGREEN_BLOCK=0` in the environment to make
the hook warn-only.

If you do not use the pre-commit framework, install a raw git hook instead:

```bash
python -m falsegreen.hook_install --repo .      # install
python -m falsegreen.hook_install --uninstall   # remove
```

### 3. As a Claude Code skill (the semantic pass)

Install the plugin:

```
/plugin marketplace add vinicq/falsegreen
```

Then, in a Claude Code session, run:

```
/falsegreen
```

against a diff or a module. The skill triages the scanner output first, then does
the semantic work: for each test it finds the unit under test, derives the
intended behavior from the oracle hierarchy, and reports tests that pass while
asserting the wrong thing, with the cited evidence and a concrete fix. It is
read-only by default (it proposes fixes, it does not edit your tests unless you
ask).

The scanner is bundled inside the skill, so the plugin works on its own. On
another Agent Skills client that does not define `${CLAUDE_SKILL_DIR}`, install
the package (`pip install falsegreen`) and the skill falls back to the CLI.

### With the skill vs without the skill

- **Without the skill** (CLI / pre-commit / CI): you get the deterministic
  scanner. It catches the 16 mechanical codes and blocks commits on the
  high-confidence ones. This is everything a non-Claude-Code user needs and runs
  anywhere Python runs.
- **With the skill** (`/falsegreen` in Claude Code): you additionally get the
  semantic pass, which catches the five code-aware cases (10, 11, 12, 15, 18),
  including the headline one: a test that is green while its expected value
  contradicts the spec. No static tool, this one included, can find that on its
  own.

---

## Configuration

- **Inline suppression:** add `# falsegreen: ignore` to silence every code on a
  line, or `# falsegreen: ignore[C8]` to silence one.
- **Disable codes globally:** `--disable C6,C2b`.
- **Environment:** `FALSEGREEN_BLOCK=0` makes the pre-commit hook warn instead of
  block.

### Project config file

Put a `[tool.falsegreen]` table in `pyproject.toml`, or a flat `.falsegreen.toml`
at the repo root (the `.falsegreen.toml` wins if both exist):

```toml
[tool.falsegreen]
disable = ["C13b"]          # turn these codes off everywhere
exclude = ["tests/legacy/*"] # skip files matching these globs

[tool.falsegreen.severity]
C8 = "high"                  # promote: now blocks the commit (exit 20)
C6 = "off"                   # same as adding C6 to disable
```

`severity` values are `high`, `low`, or `off`. Precedence, highest first:
`--disable` on the CLI, then the inline `# falsegreen: ignore`, then this config,
then the built-in default. Point at a specific file with `--config PATH`. The
config reader uses the standard library on Python 3.11+ and `tomli` on older
versions; on 3.8 without `tomli` it is a silent no-op.

### Baseline (adopt on a legacy repo without blocking day one)

Record the findings you already have, then fail only on new ones:

```bash
falsegreen --write-baseline tests/   # writes .falsegreen-baseline.json, exits 0
falsegreen --baseline tests/         # suppresses the recorded findings, fails on new
```

A finding is fingerprinted by its relative path, code, detail, and the
normalized source line, not by line number, so prepending or moving code does not
re-trigger a baselined finding. Commit `.falsegreen-baseline.json` and the ratchet
only tightens. Both flags default to `.falsegreen-baseline.json`; pass a path to
override.

---

## Technologies used

- **Python 3.8+**, standard library only at runtime. The scanner uses `ast` for
  parsing, `argparse` for the CLI, `json` for machine output, and `subprocess`
  only to ask git for staged files.
- **Zero runtime dependencies.** Dev and test use `pytest` and `ruff` (installed
  via `pip install -e ".[dev]"`).
- **Packaging:** `hatchling` build backend, SPDX license metadata (PEP 639),
  console entry point, distributed on PyPI.
- **Distribution:** a [pre-commit](https://pre-commit.com) hook
  (`.pre-commit-hooks.yaml`) and a Claude Code plugin following the
  [Agent Skills](https://agentskills.io) open standard (`SKILL.md` plus a
  `.claude-plugin/` marketplace manifest).
- **CI:** GitHub Actions across Python 3.8 / 3.11 / 3.13, running `ruff`,
  `pytest`, a self-scan (the tool must stay clean on its own code), and a
  drift-check that the bundled scanner copy matches the package byte for byte.

---

## How it compares

- **ruff / flake8-pytest-style** - mature, fast lint rules. Overlaps on broad
  `raises` (PT011) and assert-in-except (PT017). Run both. falsegreen adds
  uncollected tests, always-true asserts, self-comparison, mock typos, and the
  semantic pass.
- **PyNose / pytest-smell / TEMPY** - test-smell catalogs from research. Broader
  taxonomy, but no commit gate and no oracle-correctness check.
- **mutmut / cosmic-ray** - mutation testing, the most honest measure of whether a
  green suite fails when the code is wrong. Complementary and heavier. falsegreen
  is the cheap pre-filter you run on every commit; mutation testing is the deep
  audit you run on the suites that matter.

The defensible gap: nobody else combines a deterministic commit gate with a
code-as-evidence semantic pass aimed at oracle correctness (cases 12 and 18).

---

## Project layout

```
falsegreen/
  src/falsegreen/scanner.py        the deterministic scanner (canonical)
  src/falsegreen/hook_install.py   raw git-hook installer
  skills/falsegreen/
    SKILL.md                       the semantic-pass protocol
    reference.md                   the 18-case detection rubric
    scripts/scan.py                bundled scanner (kept identical to the package)
    examples/bad_tests_sample.py   one bad test per case (demo + regression)
  docs/guide.md                    plain-language guide to every case
  tests/test_scanner.py            the scanner's own tests
  .pre-commit-hooks.yaml           pre-commit integration
  .claude-plugin/                  plugin + marketplace manifests
  pyproject.toml                   packaging
```

---

## Contributing, security, license

- [CONTRIBUTING.md](CONTRIBUTING.md) - dev setup, how to add a detection rule
  (the four places it touches), the false-positive policy, Conventional Commits.
- [SECURITY.md](SECURITY.md) - how to report a vulnerability privately.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - Contributor Covenant 2.1.
- [CREDITS.md](CREDITS.md) - the research falsegreen builds on (the Soares rotten-
  green work, PyNose, the test-smell catalog, and the agentic-LLM refactoring study),
  with author credit.
- License: **MIT**, see [LICENSE](LICENSE).
