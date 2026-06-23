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

## Siblings

Same idea, different language surface: [falsegreen-js](https://github.com/vinicq/falsegreen-js)
(JS/TS, TypeScript compiler API) and
[falsegreen-robot](https://github.com/vinicq/falsegreen-robot) (Robot Framework,
`robot.api`). Codes share an id across the family where the smell is the same concept
(C2, C2b, C5, C7, C16).
