# Contributing to falsegreen

Thanks for helping. falsegreen finds unit tests that give false positives. The
bar for a contribution is simple: the tool itself must not become a false
positive. A new rule that cries wolf is worse than no rule.

## 30-second cheat sheet

```bash
git clone https://github.com/vinicq/falsegreen
cd falsegreen
pip install -e ".[dev]"     # runtime has no deps; this adds pytest + ruff
pytest -q                   # run the test suite
python -m falsegreen src tests   # the tool must stay clean on itself
ruff check src tests        # lint
```

Then branch, change, add a test, and open a pull request.

## How the project is built

Two layers, one repo:

- **Scanner** (`src/falsegreen/scanner.py`): a zero-dependency AST pass. It parses
  test files, it never imports or runs them. Each pattern is a case code
  (`C1`, `C5`, `C13`, ...). HIGH-confidence codes block a commit; LOW only warn.
- **Skill** (`skills/falsegreen/`): the Claude Code semantic pass. It bundles a
  byte-identical copy of the scanner at `skills/falsegreen/scripts/scan.py`; CI
  fails if it drifts from `src/falsegreen/scanner.py`.

The plain-language rubric is `docs/guide.md`; the detection reference is
`skills/falsegreen/reference.md`.

## Filing an issue

A useful bug report for a false positive includes the smallest test snippet that
gets wrongly flagged, the code falsegreen emitted, and what you expected. For a
false negative, show the bad test that slipped through. Use the demo file
`skills/falsegreen/examples/bad_tests_sample.py` as a format reference.

## Adding or changing a detection rule

This is the most common contribution. A rule touches up to five places, and the
pull request needs all that apply:

1. **Logic** in `src/falsegreen/scanner.py`. Decide HIGH vs LOW. The rule of
   thumb: HIGH only if a legitimate test can almost never trigger it, because
   HIGH blocks commits. When in doubt, ship it LOW.
2. **Reference** entry in `skills/falsegreen/reference.md` (what it looks like,
   why it fools you, confidence, the tool it maps to).
3. **Guide** entry in `docs/guide.md` if it is a new case, in the same
   real-world-analogy style as the others.
4. **Tests** in `tests/test_scanner.py`: one test proving the rule fires on the
   bad pattern, and at least one proving it does NOT fire on the legitimate
   look-alike. The second test matters more than the first.
5. **Skill prose** in `skills/falsegreen/SKILL.md`, *only if* the change alters a
   confidence level, an exemption, a flag, or the operator's mental model. CI
   byte-checks `scripts/scan.py` against the scanner, so detector *logic* is
   mirrored automatically; the SKILL.md prose and its flag list are NOT, so they
   must be kept consistent with `reference.md` and the README CLI section by hand.

Then run `pytest`, `python -m falsegreen src tests` (must stay clean), and
`diff src/falsegreen/scanner.py skills/falsegreen/scripts/scan.py` (must be
identical, copy the file if you changed the scanner).

### Off-by-default codes

Some rules are real but too noisy to run on every project, so they ship disabled.
A code is off by default when its confidence in `CASES` is `"off"` (for example
`C22`, the async-never-awaits check). The resolver already returns the catalog
default, so an `"off"` code stays quiet until a user opts in with
`[tool.falsegreen] severity = { C22 = "low" }`. Use this when a rule has a genuine
false-positive risk on common patterns: ship it `"off"`, document why, and let
teams turn it on. Its tests must run it through `--config`/`severity` to enable it,
since `run()` filters off-by-default codes (and the `analyze_file` path does not).

### The false-positive policy

falsegreen blocks commits, so precision beats recall. A rule that wrongly blocks
one legitimate test will get the whole tool disabled. Before promoting a rule to
HIGH, try to break it: parametrized tests, fixtures that assert, abstract base
test classes, `@patch`-injected mocks, helper functions. If any of those trips
it, keep it LOW or tighten the heuristic.

## Tests are required

No behavior change merges without a test. The detector of false positives cannot
have any of its own. Every legitimate-code counter-example you can think of
should have a test asserting the scanner stays quiet on it.

## Commit messages: Conventional Commits

Format: `type(scope): summary`. Recognised types: `feat`, `fix`, `docs`,
`test`, `refactor`, `chore`, `ci`. Examples:

```
feat(scanner): detect skip() inside a broad except (C17)
fix(scanner): stop flagging len(x) == N as a weak check (C6)
docs(guide): add the bug-freezing case (18)
```

Keep a pull request to roughly three commits. Squash noise before review.

## Authorship and AI tooling

Use any tools you like, including AI assistants. But the authorship is yours.
Do not add `Co-Authored-By` trailers for AI agents, and do not add AI agents to
the contributor list. Human co-authors are welcome and encouraged.

## License

By contributing, you agree that your contributions are licensed under the MIT
License (see `LICENSE`).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be
respectful, assume good faith, report problems to `vinicq@gmail.com`.
