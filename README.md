# falsegreen

[![CI](https://github.com/vinicq/falsegreen/actions/workflows/ci.yml/badge.svg)](https://github.com/vinicq/falsegreen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/falsegreen.svg)](https://pypi.org/project/falsegreen/)
[![Python](https://img.shields.io/pypi/pyversions/falsegreen.svg)](https://pypi.org/project/falsegreen/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Find unit tests that give false positives: tests that stay green without
protecting anything, and tests that pass while asserting the **wrong** expected
value.

A green test that never fails when the code breaks is worse than no test: it
tells you a broken program is safe. AI coding assistants produce these in volume:
tests that assert nothing, mock the function they claim to test, or copy the
expected value straight from current (possibly buggy) output. falsegreen is built
to catch exactly that.

The expected value is judged against the **intended** behavior (spec, contract,
then code), not against what the code happens to return today.

## Two layers

| Layer | What it is | When it runs |
|---|---|---|
| **Scanner** | Zero-dependency AST analysis (Python/pytest). Catches the mechanical patterns. | CLI, CI, and a pre-commit gate |
| **Semantic pass** | A Claude Code skill (`/falsegreen`). Reads the production code and judges whether each test asserts the *right* thing. Catches bug-freezing tests no static tool can see. | On demand, in Claude Code |

The scanner is commodity (it overlaps with `ruff`'s `PT` rules and tools like
PyNose). The semantic pass is the point: cases 12 and 18, where a test passes
while its expected value contradicts what the code should do.

## Quick look

```
pipx run falsegreen skills/falsegreen/examples/bad_tests_sample.py
```

That demo file is one bad test per case, so you see the real output in one screen.

## Install

Three ways, pick what you need.

**1. Python package (CLI + CI)**
```
pip install falsegreen
falsegreen                  # scan the current directory
falsegreen --staged         # only files staged in git
```

**2. pre-commit hook** (the standard, version-pinned way). Add to
`.pre-commit-config.yaml`:
```yaml
  - repo: https://github.com/vinicq/falsegreen
    rev: v0.1.0
    hooks:
      - id: falsegreen
```
HIGH-confidence findings block the commit. Bypass once with
`git commit --no-verify`, or set `FALSEGREEN_BLOCK=0` to warn only. No-framework
alternative: `python -m falsegreen.hook_install --repo .`

**3. Claude Code skill** (the semantic pass)
```
/plugin marketplace add vinicq/falsegreen
```
Then run `/falsegreen` to audit a diff or a module.

Requires Python 3.8+. No runtime dependencies.

## What it catches

18 cases in five families. Codes the scanner emits:
`C1 C2 C2b C3 C4 C4b C5 C6 C7 C8 C9 C13 C13b C14 C16 C17 CC`. Cases 10, 11, 12,
15 and 18 are semantic-only. Full rubric in
[`skills/falsegreen/reference.md`](skills/falsegreen/reference.md); the
plain-language guide is in [`docs/guide.md`](docs/guide.md).

Suppress a finding inline: `assert total == 0.3  # falsegreen: ignore[C8]`.
Turn codes off globally with `--disable C6,C2b`.

## How it compares

- **ruff / flake8-pytest-style** - mature, fast lint rules; overlaps on broad
  `raises` (PT011) and assert-in-except (PT017). Run both. falsegreen adds
  uncollected tests, always-true asserts, self-comparison, mock typos, and the
  semantic pass.
- **PyNose / pytest-smell / TEMPY** - test-smell catalogs from research; broader
  taxonomy, no commit gate, no oracle-correctness check.
- **mutmut / cosmic-ray** - mutation testing, the honest measure of whether a
  green suite fails when the code is wrong. Complementary; heavier. Recommended
  for suites that matter.

## License

MIT. See [LICENSE](LICENSE). Community guidelines:
[Code of Conduct](CODE_OF_CONDUCT.md), [Contributing](CONTRIBUTING.md),
[Security](SECURITY.md).
