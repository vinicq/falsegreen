# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
