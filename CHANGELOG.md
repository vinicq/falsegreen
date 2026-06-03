# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Layer detection: each finding carries a `layer` (logic | web | browser) inferred
  from the file's imports, surfaced in JSON output and as a `layer:*` SARIF tag.
  Lets a team triage by layer (a finding in pure logic is higher-signal than one in
  a web/UI test). Metadata only, no change to which findings fire.

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
- Claude Code skill (`/falsegreen`) for the semantic pass: judges a test's
  expected value against intended behavior using an oracle hierarchy and a
  test-intent classification step (catches cases 12 and 18).
- Distribution as a pip package, a `pre-commit` hook, and a Claude plugin.
- Plain-language guide (`docs/guide.md`), detection reference, and a demo file.

### Validated
- Two real-project passes (bailiff, md-bridge) settled the rules and fixed three
  false positives: C6 on called boolean predicates, C1 on literal-collection
  loops, and C7 on `f() is f()` (the lru_cache / singleton identity test).

[Unreleased]: https://github.com/vinicq/falsegreen/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vinicq/falsegreen/releases/tag/v0.1.0
