# Architecture

falsegreen is a deterministic static scanner for Python/pytest tests. It reads test files,
parses them with the standard-library `ast`, and flags the structural patterns that let a
test pass without protecting anything. It never imports or runs the code it scans.

## The pipeline

```
paths ──▶ discover ──▶ parse (ast) ──▶ rule engine ──▶ findings ──▶ report / exit code
```

1. **Discover.** Walk the given paths (or the cwd). Keep files that match the pytest
   naming conventions (`test_*.py`, `*_test.py`) and skip vendored or build directories.
   `--staged` narrows the set to test files staged in git.
2. **Parse.** `ast.parse` turns each file into a syntax tree. No execution, no imports, so
   a malicious or broken test file cannot do anything beyond fail to parse.
3. **Rule engine.** A visitor walks the tree once. Each test function is checked against
   the case catalog: empty body, assertion that can never fail, self-compare, swallowed
   exception, skipped test, and the rest of C1-C37.
4. **Report.** Findings print as readable text or JSON (`--json`). The process exit code
   is the contract for CI.

## Output contract

| Exit | Meaning |
|------|---------|
| `0`  | clean |
| `10` | low-confidence findings only |
| `20` | at least one high-confidence finding |

Wire exit `20` into CI to block the merge; treat `10` as a warning. Each finding carries a
code, confidence (`high`/`low`), file, line, and the judgment (J1-J6) it belongs to, so the
output can be grouped by category without splitting the module.

## Multiplicity and counting

How findings are counted and deduplicated. This is the reference for the three sibling
scanners (Python here, falsegreen-js, robotframework-falsegreen): the rule has to match
across all three or per-code prevalence numbers do not line up. Adjudicated in issue #64.

- **Granularity is per-code, by the code's own logical unit.** A code whose smell is a
  property of the whole test (the test never checks anything: C2, C2b, C21) fires once per
  test. A code whose smell is a property of a specific occurrence (this assert is
  always-true, this line reads the clock, this raises is too broad: C5, C7, C16, C44, ...)
  fires once per occurrence. The per-assert value-shape codes are an `elif` chain, so one
  assert line yields at most one of them; different detector families run in separate loops,
  so one test yields many findings and one line can carry findings from different families.
- **Dead-code suppression is threaded into every reader.** Dead lines are collected first
  (`dead_lines`), and every per-assert branch skips them, so a line C20 owns as unreachable
  does not also fire the per-assert codes. One dead-line set per test, consulted by every
  detector that reads those lines.
- **Same-line multi-code is allowed only across distinct mechanisms.** Within one assert's
  value shape the codes are mutually exclusive (the `elif` chain). Across mechanisms (a line
  that is both a broad-raises and a sleep) two codes co-fire: two distinct false-green
  reasons.
- **A global output dedup on `(file, line, code, detail)` is mandatory.** The driver
  collapses identical tuples so each is emitted once (`scanner.py`, the final scan loop). It
  makes counts comparable and is the safety net for any detector that double-pushes. The
  sibling scanners add the same collapse at their top-level emit.

The cross-scanner contract is recorded in the research hub's `shared/PROTOCOL.md`
("Multiplicity and counting"), so Dataset A/B/C counts are computed under one rule.


## The case catalog

The catalog is a single table: code to `(title, confidence, judgment)`. Confidence decides
the exit code; the judgment (J1-J6) ties the code to the semantic pass's questions
(does the assertion run, is the oracle independent, and so on). Three groups, selected by
prefix: `false-positive` (C*, on by default), `diagnostic` (D*, opt-in), `coupling` (M*,
opt-in). The diagnostic and coupling groups are off by default because they flag
maintainability, not a test that lies about being green.

## What it is, on purpose

- **Zero runtime dependencies.** Only the standard library. A scanner that guards a test
  suite should not drag a dependency tree into every project that installs it.
- **One module.** `scanner.py` holds discovery, parsing, the catalog, and the rules. The
  tool is small enough that one file is easier to read than a package of indirection.
- **Inline suppression.** A `# falsegreen: ignore` comment on the line silences a finding;
  `# falsegreen: ignore[C8]` silences one code. The author keeps the final say.

## The boundary: static, semantic, runtime

The scanner owns what the syntax tree can prove. Two kinds of smell sit outside that line:

- **Semantic.** Whether the expected value contradicts the intended behavior (Case 18), or
  the test re-implements the production logic instead of an independent oracle (Case 12).
  These need intent, not structure. They belong to
  [falsegreen-skill](https://github.com/vinicq/falsegreen-skill), the LLM pass, which
  compares the expected value against the spec, then the contract, then the code.
- **Runtime.** Order dependence across tests, flakiness that only appears under a real
  clock or network. Seeing these needs execution, which the scanner does not do.

Precision over recall is the standing rule: a low-confidence code that misses a case is
preferred to one that flags correct code. A false alarm trains people to ignore the tool.

## Codes left out on purpose

The static layer is close to saturated, so a few catalog codes are deliberately not
implemented. They are recorded here, with the reason, so the boundary is explicit.

- **High false-positive without deeper analysis.** `C40` (assert on a spec-less `Mock`
  attribute, always truthy), `C46` (real network or DB call with no double), and `C47`
  (assertion that depends on dict or set ordering). Each looks identical to a valid
  pattern at the AST level: a real object, an integration test, a deterministic
  collection. The parser cannot tell them apart, so these stay in the LLM semantic pass.
- **Runtime and culture (the `PL` series).** Not a per-file property. `PL1`, `PL2`, `PL7`, and
  `PL8` are covered by `--config-audit` (it reads the project's pytest and coverage
  config: `python -O`/`PYTHONOPTIMIZE` stripping asserts, warnings, coverage gate,
  early-exit addopts). `PL4` (a collection error counted as zero tests), and `PL3`,
  `PL5`, `PL6` need execution or pipeline inspection, so they are documented rather
  than promised.
- **Semantic Family E / F7.** Mocking the unit under test, echoing the value fed to a
  mock, re-implementing the production formula, borrowing state, an expected value that
  contradicts the spec. Structure cannot prove intent. `C14` is the only codable corner;
  the rest are reached by mutation testing (mutmut, cosmic-ray) and the skill.

`examples/python/` carries a BAD plus a CLEAN look-alike for every code the scanner does
detect; the codes above have no example there because the scanner is not meant to flag
them.

## Siblings

Same idea, different language surface: [falsegreen-js](https://github.com/vinicq/falsegreen-js)
(JS/TS, TypeScript compiler API) and
[robotframework-falsegreen](https://github.com/vinicq/robotframework-falsegreen) (Robot Framework,
`robot.api`). Codes share an id across the family where the smell is the same concept
(C2, C2b, C5, C7, C16).
