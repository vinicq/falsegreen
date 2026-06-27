# Status

Public product state of `falsegreen` at a glance. For the full code catalog and usage,
see the [README](README.md); for the change history, see the [CHANGELOG](CHANGELOG.md).

Research artifacts, datasets, and unpublished numbers live in the private research hub,
never in this repo. This file tracks the public product only.

## Version

- Current: **0.6.0** (PyPI: `pip install falsegreen`)
- Versioning: semver; releases via trusted publishing (OIDC).

## CI health

- `ci.yml`: tests on Python 3.8 / 3.11 / 3.13.
- `release.yml`: PyPI publish on tag.
- `codex-review-gate.yml`, `release-drafter.yml`, `credit-contributor.yml`.

## Catalog coverage

Deterministic AST scan over pytest and unittest test code. Active codes:

- **False-green (structural):** C1, C2, C2b, C3, C4, C4b, C5, C6, C6b, C7, C8, C9, C11a,
  C13, C13b, C14, C16, C17, C18, C19, C20, C21, C22, C23, C24, C25, C27, C28, C29, C30,
  C31, C32, C33, C34, C35, C36, C37, C38, C39, C41, C42, C43, C44, C45, C48, CC.
- **Diagnostic (opt-in, maintainability):** D1, D3, D4, D5, D6.
- **Coupling (opt-in):** M2.
- **Project layer (`--config-audit`):** PL2, PL7, PL8.

Each code carries a judgment tag (J1-J6) and a risk family (F1-F8); see the README catalog
and the docs site for what each one flags, with a BAD plus CLEAN example.

## Pyramid levels

Findings are tagged unit / integration / e2e by import roots. A real database or HTTP call
in a unit test is itself the smell the level-awareness surfaces.

## Supported test code

pytest and unittest (xUnit `assert*` forms), plus the common assertion and mock libraries.

## Scope

Static layer only. Statically provable false-green with a low false-positive rate. Semantic
judgment goes to `falsegreen-skill`; runtime and culture are out of scope by design.
