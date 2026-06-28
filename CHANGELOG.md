# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- C20 no longer treats an arbitrary `obj.fail(...)` (e.g. `logger.fail()`, `result.fail()`) as a
  terminator, so a real assertion after it is not wrongly reported as dead code (HIGH false
  positive). Only `pytest.fail`, a bare imported `fail()`, and unittest `self.fail()`/`cls.fail()`
  terminate (#103).
- C16 no longer flags `requests.get(url, timeout=5)` / `cache.get(k, timeout=5)`: `get` is dropped
  from the concurrency-wait set because it collides with the recommended HTTP/cache form
  (`result`/`join`/`wait`/`wait_for` still flag a fixed concurrency-wait timeout) (#105).
- C48 no longer fires when a genuine assertion already runs before the test-mode flip
  (`assert pre(); os.environ["TESTING"]="1"; assert post()`): real behaviour is verified, so
  the post-flip asserts are incidental. A post-flip assertion that inspects the toggled flag
  itself still flags (#107).
- C41 (and the other per-assert LOW detectors) are suppressed on a statement already flagged
  C20 dead-code: the assertion never runs, so C20 owns the line, mirroring how C21 owns its
  conditional asserts (#108).
- `falsegreen.__version__` (the package attribute) was stale at `0.4.0` while
  `scanner.__version__` / `pyproject` were `0.6.0`; `__init__` now re-exports the single
  `scanner.__version__` so the two cannot drift, and `test_version_lockstep` checks the
  package-level value too (closes a gap left by #104).

### Docs
- ARCHITECTURE.md no longer lists C41 among codes "left out on purpose" — C41 is a live LOW
  detector (shipped 0.6.0). C40, C46, C47 remain (#109).

### Tests
- Precision-lock corpus (`tests/test_precision_corpus.py`): one legitimate look-alike per
  HIGH code, asserting no HIGH code fires on it, so a blocking false positive cannot merge
  silently. Pins the two historical false positives (C7 on a deliberate `__eq__` test, C4 on
  a route handler named `test_*`) plus a clean case for every other HIGH code, with a guard
  that fails if a new HIGH code lands without an entry (#88).

### Added
- `C48` (dark patch): a test that forces a known test-mode flag into test mode and then
  asserts is exercising the product's test-only branch (`if TESTING: ...`), not real
  behaviour. Detection-only; v1 covers raw writes (`os.environ["TESTING"] = "1"`,
  `settings.TESTING = True`, a `global`-declared `TESTING = True`). Config values and
  product feature flags are not flagged, and `C48` suppresses the `C29` env-leak report on
  the same line. New id, not `C46`/`C47`, which stay reserved for their cataloged concepts (#78).

### Fixed
- `C41` collects container evidence with `children_no_nesting`, so a list/dict/set literal
  bound to the same name inside a nested helper no longer misclassifies a custom-object
  receiver as a built-in container (#97).

## [0.6.0] - 2026-06-27

### Fixed
- C41 counts container evidence only before the assertion, so a list/dict/set literal bound to the receiver after the assert no longer misclassifies it (#83).
- codex-review-gate reruns on pull_request_review_thread resolve and paginates reviewThreads before counting blockers; the opt-in demo keeps the CLEAN look-alike examples clean (#81).


### Added
- `examples/python/`: a worked BAD plus CLEAN look-alike for every code the scanner
  detects, grouped by the five families. `falsegreen examples/` flags the BAD samples and
  leaves the CLEAN ones alone; the opt-in codes (C22, D1, D3, D4, D5, D6, M2) are shown via
  `examples/enable-optin.toml`. The samples are scan targets, not a runnable suite, so a
  conftest keeps pytest from collecting them.

### Documentation
- README and ARCHITECTURE now list the codes the scanner does not detect, with the reason:
  C40, C41, C46, C47 (high false-positive without deeper analysis, left to the skill), the
  PL runtime/culture series (PL1/PL3/PL4/PL5/PL6 are not a per-file property; PL2/PL7/PL8 are
  already in `--config-audit`), and the Family E / F7 semantic codes (reached by mutation
  testing and the LLM pass).

## [0.5.0] - 2026-06-23

### Added
- New codes from the consolidated catalog: C38 (two tests share a name — the later
  overrides the first), C39 (`return`s a comparison instead of asserting it), C42 (`assert`
  on a generator expression / lambda — always truthy), C43 (`pytest.skip()` after test logic
  strands the checks below it), C44 (numeric tautology, `len(x) >= 0`), C45 (empty
  `@pytest.mark.parametrize` list — zero cases run).
- Documented test-pyramid coverage: unit, integration (API and database), and E2E.
- Status report output: every finding now carries its pyramid level (unit / integration /
  e2e, detected from the file's import roots) and a one-line fix hint. The text summary adds
  a per-level breakdown and the top fixes by frequency; JSON gains `level` and `fix` fields;
  SARIF carries the level as a tag.
- `--config-audit` mode (project layer): reads the project's pytest/coverage config
  (`pyproject.toml` `[tool.pytest.ini_options]`, `pytest.ini`, `tox.ini`, `setup.cfg`) and
  reports the ways a suite stays green by configuration: PL2 (`filterwarnings` not promoted to
  `error`), PL7 (no `--cov-fail-under` / `fail_under` coverage gate), PL8 (`addopts` stops the
  run early with `-x`/`--maxfail`). Findings carry level `project` and a fix hint. The per-file
  scan cannot see config; this closes the project-layer gap. Runtime-only project smells
  (PL1/PL4/PL6) stay out of static scope.
- `--output` accepts a directory (e.g. `.falsegreen/`): an extension-less or trailing-slash
  path, or an existing directory, receives `report.<ext>` for the chosen format. A path with
  an extension is still written as a single file. Parent directories are created in both cases.

## [0.4.0] - 2026-06-09

### Added
- **#31** — `unittest.TestCase` subclasses are now fully collected and
  analyzed, even when the class name does not start with `Test`. A new
  `is_testcase_subclass()` helper detects inheritance from `TestCase`,
  `unittest.TestCase`, `django.test.TestCase`, and common variants.
  `self.assert*` methods (`assertEqual`, `assertRaises`, `assertFalse`,
  etc.) count as assertions in all existing checks (C2, C2b, C21, …),
  so xUnit-style tests no longer produce spurious "no assertion" findings.
- **C6b** (LOW, issue #6): assertion subscripts a mock call-args list by a
  computed or index-derived position rather than a stable name.
  `mock.call_args.args[idx]` where `idx` was obtained via `.index()` or an
  expression makes the assertion silently break whenever the argument order of
  the called function changes. Access by `kwargs["name"]` is not flagged.
- **C11a** (LOW, issue #5): self-confirming literal. Fires when
  `assert obj.attr == VALUE` confirms a literal that the test itself assigned
  as a constructor keyword argument in the same function
  (`obj = MyClass(attr=VALUE); assert obj.attr == VALUE`). The assertion
  verifies what the test wrote, not what the SUT produced.
- **C24** (LOW, issue #21): module-level mutable state mutated by a test.
  A module-global `list`, `dict`, `set`, `Counter`, or `defaultdict` that is
  written inside a test function (via `.append()`, `.update()`, subscript
  assignment, augmented assignment, etc.) can leak side effects into later
  tests. Globals reset by an `autouse=True` fixture are excluded because the
  fixture provides the required teardown.
- **C16** extended (issue #7): now detects hardcoded timeouts in concurrency
  primitives. `future.result(timeout=N)`, `thread.join(timeout=N)`,
  `queue.get(timeout=N)`, `asyncio.wait_for(coro, timeout=N)`, and similar
  calls with a literal numeric `timeout=` argument are flagged because the
  value may be too short in a loaded CI environment, turning a real race into
  a non-deterministic failure.

## [0.3.0] - 2026-06-08

### Added
- C37 (LOW): duplicate test case in `@pytest.mark.parametrize`. When the same
  argument set appears more than once in the case list — e.g.
  `parametrize("x", [1, 2, 1])` — the repeated case runs the exact same
  scenario twice: no extra coverage, just redundant CI time. Detected by
  comparing the canonical AST dump of each element; works for scalars,
  strings, tuples, and nested structures.
- C36 (LOW): `pytest.fail()` called without a reason argument. A bare
  `pytest.fail()` leaves the build log with an empty failure message, giving
  no context about what invariant was violated. Pass a descriptive string as
  the first positional argument or as `reason=`. The check fires when neither
  a positional argument nor `reason=`/`msg=` keyword is present; it does not
  fire for `from pytest import fail; fail("message")` calls.
- C35 (LOW): `@pytest.mark.flaky`, `@pytest.mark.repeat`, `@pytest.mark.retry`,
  `@pytest.mark.rerun`, or equivalent decorator present on the test. Retrying
  a test until it passes hides flaky behaviour instead of fixing it. The root
  cause — non-determinism, race condition, test-order dependency — stays in the
  SUT and the suite reports green by chance. Remove the decorator and fix the
  underlying issue; if the flakiness is expected and intentional, add a comment
  explaining why. Fires for both bare marker usage (`@pytest.mark.flaky`) and
  parametrised form (`@pytest.mark.flaky(reruns=3)`).
- C16 now also fires for PyTorch and TensorFlow random operations without a
  fixed seed. `torch.rand`, `torch.randn`, `torch.randint`, and the other
  PyTorch random-tensor functions require `torch.manual_seed()` in the same
  test to produce deterministic output. `tf.random.normal`, `tf.random.uniform`,
  and the other TensorFlow random ops require `tf.random.set_seed()`.
  Both checks respect any call matching `manual_seed` or `set_seed` as an alias.
- D6 (off by default, `info` severity): `print()` call in test body — debug
  artifact that bypasses the test oracle. Print statements left after debugging
  produce CI noise but check nothing; remove or replace with an assertion.
  Enable with `D6 = "info"` in `[tool.falsegreen.severity]`. Only bare
  `print(...)` is flagged; `logging.info()` and similar are not.
- D5 (off by default, `info` severity): test body has too many inline setup
  statements before the first assert. When a test arranges its objects and
  transforms its data directly rather than delegating to a fixture, the act
  and assert phases are buried under boilerplate. Enable with
  `D5 = "info"` in `[tool.falsegreen.severity]`. The threshold defaults to 5
  setup statements (assignments and bare function calls before the first
  `assert`); override with `inline_setup_threshold = N` at the top level of
  `[tool.falsegreen]`.
- C34 (LOW): suboptimal assert form. Flags patterns where pytest itself (or
  Python) provides a simpler, more idiomatic alternative that produces better
  failure messages and is clearer about intent:
  - `assert not x in y`  →  `assert x not in y`
  - `assert len(x) == 0`  →  `assert not x`
  - `assert x == True`  →  `assert x`
  - `assert x == False`  →  `assert not x`
  - `assert x == None`  →  `assert x is None`
  - `assert x != None`  →  `assert x is not None`
  Literal on either side is detected (e.g. `True == x` also triggers).
  Does not fire when C5 or C7 already own the assertion.
- C33 (LOW): sklearn metric result never asserted. Calling `model.score()`,
  `accuracy_score()`, `f1_score()`, `roc_auc_score()`, and similar metric
  functions without asserting on the return value means the test passes
  regardless of actual model performance. A model with 10% accuracy passes
  as easily as one with 95%. Covers both discarded results and names assigned
  but never used in any assert.
- C16 now also fires for `train_test_split()` without `random_state=`. Without
  a fixed seed, different runs produce different train/test splits and the same
  test can pass or fail by chance depending on the random allocation.
- D4 (off by default, `info` severity): `@pytest.mark.parametrize` with more than two
  cases and no `ids=` argument. Without ids, pytest names each case `test_foo[0]`,
  `test_foo[1]`, etc. — the failing case cannot be identified from the test name alone.
  Enable with `D4 = "info"` in `[tool.falsegreen.severity]`. Add
  `ids=["name1", "name2", ...]` or a callable to label each case.
- C32 (LOW): `@pytest.mark.skip` without `reason=`. An undocumented skip makes it
  impossible to know when the test should be re-enabled and may mask a permanently
  broken suite. Add `reason="<why>"` or remove the skip entirely. Applies at both
  function and class level. Does not flag `skipif`/`skipUnless`, which carry a
  condition by design.
- C31 (LOW): `capsys.readouterr()` (or `capfd.readouterr()`) called in a test where
  the result is never asserted. The test captures stdout/stderr but checks nothing
  about the content — the capture has no effect on pass/fail. Either assert on the
  captured output (`assert captured.out == "..."`) or remove the call.
- C30 (LOW): `responses.add()` or `httpretty.register_uri()` called in a test without
  activating the library's HTTP interceptor (`@responses.activate`, `@httpretty.activate`,
  or an equivalent context manager). Without activation the mock is registered but every
  HTTP call bypasses it and reaches the real network — the test passes only when the
  live service is reachable and returns the expected data.
- C25 (LOW): `@pytest.mark.xfail` without `strict=True`. When the test unexpectedly
  passes (XPASS), pytest silently treats it as a success — the bug was fixed but
  the test is never promoted to a normal passing test. Enable globally with
  `xfail_strict = true` in `[tool.pytest.ini_options]`, or per-marker with
  `@pytest.mark.xfail(strict=True)`.
- C27 (HIGH): `try/except/pass` used as a substitute for `pytest.raises`. A try
  block that makes calls, has no assertion in its body, and whose only handler
  silently swallows a specific exception passes whether the exception is raised or
  not. Replace with `with pytest.raises(ExpectedError):`.
- C28 (LOW): `with pytest.raises(E) as exc:` where the binding `exc` is never read
  after the block. The exception type is verified but the message, args, and
  attributes are not — a wrong exception with the right type passes undetected.
  Add `assert "expected text" in str(exc.value)` or use `match=`.
- C29 (LOW): `os.environ["KEY"] = value` (or `os.environ.update` / `os.putenv`)
  directly in a test. The mutation outlives the test function and leaks to every
  test that runs after. Use `monkeypatch.setenv()` which restores the original
  value automatically.
- C23 (LOW): test opens a real file at a hard-coded literal path — `open("path/to/file")`,
  `Path("literal").read_text()`, or `Path("literal").read_bytes()`. A hard-coded path ties
  the test to a specific directory layout and is often a credential file or a fixture that
  lives outside the repo (Mystery Guest, J6).
- D1 (off by default, `info` severity): 2+ assertions in one test where every `assert`
  omits the `msg` argument. When the test fails, pytest cannot report which assertion
  triggered it (Assertion Roulette, J4). Enable with `D1 = "info"` in
  `[tool.falsegreen.severity]`.
- D3 (off by default, `info` severity): the same assertion written twice in the same test
  body. The duplicate adds no coverage (Duplicate Assert, J4). Enable with `D3 = "info"`.
- M2 (off by default, `info` severity): test body exceeds the configured line-count limit
  (Long Test Method, J5). Enable with `M2 = "info"`. Default limit is 50 lines; override
  with `long_test_threshold = N` at the top level of `[tool.falsegreen]`, not inside
  `[severity]`.
- `info` severity level: a new severity below `low`. Info findings appear in separate
  DIAGNOSTIC and COUPLING output sections and leave the exit code at 0. Existing HIGH,
  LOW, and OFF behaviour is unchanged.
- `long_test_threshold` config key: integer, default 50. Top-level key in
  `[tool.falsegreen]`; controls the line-count threshold for M2.
- `freezegun` and `time_machine` imports suppress C16 clock-read findings in the
  same file (`TIME_CONTROL_IMPORTS` + `file_controls_time()` check): when a file
  controls time externally, `datetime.now()`/`time.time()` calls inside tests are
  not non-deterministic.
- `trio.run` added to the drives-the-loop exemption; C22 no longer fires when a
  test drives its own async loop via `trio.run`.
- 14 regression tests covering the changes below.

### Changed
- `WEB_IMPORT_ROOTS` expanded: `responses`, `httpretty`, `respx`, `aioresponses`,
  `vcr`, `requests_mock`, `pook`, `pytest_httpserver` are now classified as web
  layer, so C6/C14 softening applies to HTTP-mock-heavy test files.
- `BROWSER_IMPORT_ROOTS` expanded: `helium`, `pyppeteer`, `seleniumbase`.
- `sure` (`.should` attribute access) and `expects`/`ward` (`expect()` call) are
  now recognized as real assertion calls (`FLUENT_ASSERT_CALLS`); C2/C2b no longer
  fires on tests that use those fluent libraries.

## [0.2.2] - 2026-06-08

### Changed
- Skill and Claude plugin removed from this repo: the LLM semantic pass, the
  detection reference, and multi-language support now live in
  [falsegreen-skill](https://github.com/vinicq/falsegreen-skill).
- README, CONTRIBUTING, and CREDITS updated to reflect the split.

## [0.2.1] - 2026-06-08

### Fixed
- C2 (HIGH) no longer flags an empty body under sympy's `@SKIP` decorator
  (`from sympy.testing.pytest import SKIP`), which raises `Skipped` at runtime,
  same semantics as `@pytest.mark.skip`. Found validating sympy.

## [0.2.0] - 2026-06-05

### Fixed
- C7 (HIGH) no longer flags a deliberate `__eq__`/`__hash__` test. `assert x == x`
  beside a discriminating or membership check on the same operand (`assert x != y`,
  `assert not x == y`, `assert x in {x}`) is reflexive-equality testing, not a
  tautology. A lone `assert x == x` still fires. Found validating aiohttp/starlette.
- C4 (HIGH) no longer flags a `test*`-named web route handler / WSGI app
  (`@app.get`/`@app.post`/`@Request.application`/`@click.command`), nor a function
  that is referenced (called, awaited, `asyncio.create_task`, or passed as a
  callback) - a referenced function runs, so it is not a forgotten test. Only a
  nested `test*` with a check in its own body that is never referenced, or a
  top-level test-shaped function never called, still fires. Found validating
  fastapi, werkzeug, sanic, flask, aiohttp.
- C2/C2b (HIGH) no longer flag a `test*` function in a file pytest does not collect
  (not `test_*.py`/`*_test.py`/`conftest.py`), such as pylint's `tests/functional`
  lint fixtures or black's `tests/data/cases` format fixtures - those are never run.
- C2/C2b no longer flag an empty body under a skip/xfail marker
  (`@pytest.mark.skip`/`skipif`/`skipUnless`/`xfail`, on the function or its class):
  the marker stops it running, so it is a deliberate placeholder.
- C7 exemption broadened to count `is not <peer>`, `!=`/`not ==` against a
  non-trivial literal, and a companion `hash(x)` as the discriminating counterpart
  (scrapy/urllib3 identity tests, attrs/hypothesis/arrow eq tests). A lone
  self-compare with no counterpart still fires.
- C3 (HIGH) fires only when the `except` would actually swallow `AssertionError`
  (bare `except`, `except Exception`/`BaseException`/`AssertionError`, or a tuple of
  those). A specific `except SomethingException` no longer trips it.

### Added
- C22 (OFF by default): an `async def test_*` that asserts but never awaits the unit
  (The Liar). Opt in via `[tool.falsegreen] severity = { C22 = "low" }`. First
  off-by-default code; the resolver already supported an `off` catalog default.
- Layer detection: each finding carries a `layer` (logic | web | browser) inferred
  from the file's imports, surfaced in JSON output and as a `layer:*` SARIF tag.
- Layer-aware softening (issue #20): in a web/browser test, C6 no longer flags the
  truthiness of an element-visibility predicate, an HTTP request, or a
  response/page/locator object (that presence IS the assertion), and C14 is
  suppressed for snapshot/visual-regression writes. Softening only: it never adds a
  finding or raises confidence, and the vacuity codes (C2/C5/C7/C13/C3/...) are
  layer-agnostic.

## [0.1.0] - 2026-06-03

First release.

### Added
- Deterministic AST scanner for Python/pytest (`falsegreen`, `python -m falsegreen`)
  covering false-positive patterns across the semantic-logic families. HIGH codes
  block a commit; LOW codes warn. Codes: C1 C2 C2b C3 C4 C4b C5 C6 C7 C8 C9 C13
  C13b C14 C16 C17 C18 C19 C20 C21 CC. Each code carries a judgment tag (J1-J6).
- C18 (LOW): assertion comparing `str()`/`repr()`/an f-string of a value to a
  string literal, which checks formatting instead of the value (Sensitive Equality).
- C19 (LOW): a `pytest.raises` block wrapping more than one statement, where an
  earlier line can raise and the intended call is never reached.
- PyPI publishing via Trusted Publishing (OIDC) in `.github/workflows/release.yml`;
  see `RELEASE.md`.
- Inline suppression (`# falsegreen: ignore[C8]`) and `--disable` flag.
- Project config: `[tool.falsegreen]` in `pyproject.toml` or `.falsegreen.toml`
  (`disable`, `exclude`, per-code `severity`); precedence CLI > inline > config.
- Baseline / ratchet mode (`--baseline`, `--write-baseline`) with content
  fingerprints that survive line shifts, for adopting on a legacy repo.
- Output formats: `--format {text,json,sarif,junit}`, `--summary`, `--output`.
  SARIF 2.1.0 carries the judgment tag; `--summary` rolls up by code and judgment.
- C20 (HIGH): assertion in dead code after `return`/`raise`/`fail()`. C21 (LOW):
  every assertion conditional, none runs unconditionally. Both from the rotten-
  green-test line of work (Soares 2023).
- Distribution as a pip package and a `pre-commit` hook.
- Plain-language guide (`docs/guide.md`); the detection reference and LLM semantic
  pass live in [falsegreen-skill](https://github.com/vinicq/falsegreen-skill).

### Validated
- Two real-project passes (bailiff, md-bridge) settled the rules and fixed three
  false positives: C6 on called boolean predicates, C1 on literal-collection
  loops, and C7 on `f() is f()` (the lru_cache / singleton identity test).

[Unreleased]: https://github.com/vinicq/falsegreen/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/vinicq/falsegreen/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/vinicq/falsegreen/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/vinicq/falsegreen/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/vinicq/falsegreen/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/vinicq/falsegreen/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/vinicq/falsegreen/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vinicq/falsegreen/releases/tag/v0.1.0
