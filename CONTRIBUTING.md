# Contributing to falsegreen

falsegreen finds unit tests that pass green without protecting anything. The bar for a contribution is direct: the tool itself must not become a false positive. A new rule that cries wolf is worse than no rule.

## 30-second start

```bash
git clone https://github.com/vinicq/falsegreen
cd falsegreen
pip install -e ".[dev]"     # runtime has no deps; this adds pytest + ruff
pytest -q                   # run the test suite
python -m falsegreen src tests   # the tool must stay clean on itself
ruff check src tests        # lint
```

Run a single test or pattern:

```bash
pytest tests/test_scanner.py -k C17            # all tests matching C17
pytest tests/test_scanner.py::test_c17_fires   # one exact test
```

Check the installed scanner version:

```bash
python -m falsegreen --version
```

Then branch, make your change, add tests, and open a pull request.

## How the project is built

One module, one job: `src/falsegreen/scanner.py` is a zero-dependency AST pass. It parses test files; it never imports or runs them. Each pattern is a case code (`C1`, `C5`, `C13`, ...). HIGH findings block a commit; LOW ones warn; `info` ones are informational and do not affect the exit code.

The plain-language rubric is `docs/guide.md`. The LLM semantic pass and the multi-language detection reference live in [falsegreen-skill](https://github.com/vinicq/falsegreen-skill).

## Filing an issue

A useful bug report for a false positive includes the smallest test snippet that gets wrongly flagged, the code falsegreen emitted, and what you expected. For a false negative, show the bad test that slipped through.

## Adding or changing a detection rule

This is the most common contribution. A rule touches up to three places, and the pull request needs all that apply:

1. **Logic** in `src/falsegreen/scanner.py`. Decide HIGH vs LOW. The rule of thumb: HIGH only if a legitimate test can almost never trigger it, because HIGH blocks commits. When in doubt, ship it LOW.
2. **Guide** entry in `docs/guide.md` if it is a new case, in the same real-world-analogy style as the others.
3. **Tests** in `tests/test_scanner.py`: one test proving the rule fires on the bad pattern, at least one proving it stays quiet on the legitimate look-alike. The second matters more.

Then run `pytest` and `python -m falsegreen src tests`. Both must exit clean. The self-scan is not optional: a new rule that triggers on the scanner's own code is a false positive by definition.

### Off-by-default codes

Some rules are real but too noisy for every project, so they ship disabled. A code is off by default when its confidence in `CASES` is `"off"` (for example `C22`, the async-never-awaits check; or `D1`–`D6` and `M2`). The resolver returns the catalog default, so an `"off"` code stays quiet until a user opts in via `[tool.falsegreen] severity = { D1 = "info" }`.

Use this when a rule has genuine false-positive risk on common patterns: ship it `"off"`, document why, and let teams turn it on. Its tests must enable it through `--config`/`severity`, since `run()` filters off-by-default codes.

### The false-positive policy

falsegreen blocks commits, so precision beats recall. A rule that wrongly blocks one legitimate test will get the whole tool disabled. Before promoting a rule to HIGH, try to break it: parametrized tests, fixtures that assert, abstract base test classes, `@patch`-injected mocks, helper functions. If any of those trips it, keep it LOW or tighten the heuristic.

## Tests are required

No behavior change merges without a test. The detector of false positives cannot have any of its own. Every legitimate-code counter-example you can think of should have a test asserting the scanner stays quiet on it.

## Commit messages: Conventional Commits

Format: `type(scope): summary`. Recognized types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`. Examples:

```
feat(scanner): detect skip() inside a broad except (C17)
fix(scanner): stop flagging len(x) == N as a weak check (C6)
docs(guide): add the bug-freezing case (18)
```

Keep a pull request to roughly three commits. Squash noise before review.

## Authorship and AI tooling

Use any tools you like, including AI assistants. The authorship is yours. Do not add `Co-Authored-By` trailers for AI agents, and do not add AI agents to the contributor list. Human co-authors are welcome and encouraged.

## License

By contributing, you agree that your contributions are licensed under the MIT License (see `LICENSE`).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be respectful, assume good faith, report problems to `vinicq@gmail.com`.
