# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-02

First release.

### Added
- Deterministic AST scanner for Python/pytest (`falsegreen`, `python -m falsegreen`)
  covering 17 false-positive patterns across five families. HIGH-confidence codes
  block a commit; LOW codes warn. Codes: C1 C2 C2b C3 C4 C4b C5 C6 C7 C8 C9 C13
  C13b C14 C16 C17 CC.
- Inline suppression (`# falsegreen: ignore[C8]`) and `--disable` flag.
- Claude Code skill (`/falsegreen`) for the semantic pass: judges a test's
  expected value against intended behavior using an oracle hierarchy and a
  test-intent classification step (catches cases 12 and 18).
- Distribution as a pip package, a `pre-commit` hook, and a Claude plugin.
- Plain-language guide (`docs/guide.md`), detection reference, and a demo file.

[Unreleased]: https://github.com/vinicq/falsegreen/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vinicq/falsegreen/releases/tag/v0.1.0
