# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/vinicq/falsegreen/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/vinicq/falsegreen/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/vinicq/falsegreen/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/vinicq/falsegreen/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vinicq/falsegreen/releases/tag/v0.1.0
