# falsegreen (Claude Code skill)

The semantic half of [falsegreen](../../README.md). Invoked as `/falsegreen`, it
reads the production code and judges whether each unit test asserts the *right*
expected value, catching tests that pass while freezing a bug.

- `SKILL.md` - the skill definition and the semantic-pass protocol.
- `reference.md` - the 18-pattern detection rubric.
- `scripts/scan.py` - bundled copy of the deterministic scanner (kept identical
  to `src/falsegreen/scanner.py`; CI fails if it drifts).
- `examples/bad_tests_sample.py` - one bad test per case, for demos and testing.

## Install

```
/plugin marketplace add vinicq/falsegreen
```

Then run `/falsegreen` against a diff or a module. The scanner half also runs
standalone: `pip install falsegreen` then `falsegreen --staged`.

License: MIT (see [../../LICENSE](../../LICENSE)).
