---
name: falsegreen
description: >-
  Audit unit tests for false positives: tests that stay green without protecting
  anything, and tests that pass while asserting the wrong expected value. The
  test's expected value is judged against the INTENDED behavior (spec, contract,
  then code), not against what the code happens to return today.
when_to_use: >-
  When reviewing unit tests, before merging, when auditing AI-generated tests, or
  when the user asks to check test quality, find fake/ghost tests, find tests that
  do not add value, or runs /falsegreen.
allowed-tools: Read, Grep, Glob, Bash
---

# falsegreen

A test that is green but does not fail when the code breaks is worse than no
test: it tells you a broken program is safe. This skill finds those. AI coding
assistants are a high-volume source of them (tests that assert nothing, mock the
unit they claim to test, or copy the expected value from current output). It
works in two layers.

1. **Scanner (deterministic, fast).** An AST pass that flags the mechanical
   patterns (always-true asserts, empty tests, swallowed exceptions, mock typos,
   uncollected tests, and so on). This is what runs on every commit.
2. **Semantic pass (you, on demand).** When invoked as `/falsegreen`, you do the
   part a parser cannot: read the production code and judge whether each test
   asserts the *right* value. This catches the worst case, a test that passes
   while locking in a bug.

The catalog of patterns lives in `reference.md` (cases A-E plus the semantic smell
index). The plain-language guide is in `../../docs/guide.md`.

### What this skill judges, and what it does not

A **test smell** is a symptom of a design or maintainability problem in test code
(Soares 2023, *A Multimethod Study of Test Smells*). Most smells do not make a test
lie: an Eager Test or a badly named test still goes red when the code breaks. This
skill targets the narrower, more dangerous set: tests that are smelly **and
ineffective**, where the green bar is false. The sharpest case is the **rotten
green test** (Soares's term; Delplanque, Ducasse et al., ICSE 2019): a passing test
that holds at least one assertion that never executes. Test effectiveness (does it
fail when the code is wrong?) is the property to protect, judged against the test's
intent and an independent oracle, not against the code's current output. In AAA
terms, every defect here lives in the Assert phase: either the assertion does not
run (rotten green), or it runs against the wrong oracle.

---

## Layer 1: the scanner

Run the bundled scanner (works whether or not the package is pip-installed):

```
python "${CLAUDE_SKILL_DIR}/scripts/scan.py" --format json tests/
```

`${CLAUDE_SKILL_DIR}` is set by Claude Code. On another Agent Skills client that
does not define it, install the package (`pip install falsegreen`) and run the
`falsegreen` CLI or `python -m falsegreen` instead. They are equivalent.

If the package is installed, the `falsegreen` CLI and `python -m falsegreen` are
also equivalent here. Useful flags:

```
falsegreen tests/            # scan a folder or file
falsegreen --staged          # only test files staged in git
falsegreen --format json     # machine-readable, for this pass (--json is an alias)
falsegreen --disable C6,C2b  # turn off specific codes
falsegreen --config PATH     # honor a project's [tool.falsegreen] / .falsegreen.toml
falsegreen --baseline        # suppress findings already in .falsegreen-baseline.json
```

Suppress one finding inline with `# falsegreen: ignore` or
`# falsegreen: ignore[C8]` on its line. Exit codes: `0` clean, `10` low only,
`20` at least one high-confidence finding.

If the repo has a `[tool.falsegreen]` config, a `.falsegreen.toml`, or a
`.falsegreen-baseline.json`, run with `--config`/`--baseline` so your triage
matches what the team's pre-commit and CI actually report. A finding the project
has deliberately disabled or baselined is not something to resurface as new -
treat it as accepted, unless the semantic reading shows a frozen bug (case 18)
hiding behind the suppression. A baseline can silence a real case-18 bug, and the
human pass is the only thing that can see through it.

Findings come in two buckets. **HIGH** = almost certainly a false positive
(assert True, empty test, except that swallows, `pytest.skip` in a broad except,
mock assertion misspelled or not called, test pytest does not collect, a value
compared to itself). **LOW** = a smell that needs judgment.

### Run it on every commit

Preferred, in `.pre-commit-config.yaml`:

```yaml
  - repo: https://github.com/vinicq/falsegreen
    rev: v0.1.0
    hooks:
      - id: falsegreen
```

Or a raw git hook: `python -m falsegreen.hook_install --repo .`. HIGH-confidence
findings block the commit; bypass once with `git commit --no-verify`, or set
`FALSEGREEN_BLOCK=0` to warn only.

---

## Layer 2: the semantic pass (the point of /falsegreen)

The scanner proves structure. It cannot read intent. The two most dangerous
cases are invisible to a parser:

- **Case 12** - the test re-implements the production formula to compute its own
  expected value, so both agree on the same wrong number.
- **Case 18** - the expected value was copied from what the function returns
  today, not from what it should return. The test passes and defends the bug.

The expected value must come from an **independent oracle**. Use this hierarchy,
highest first: explicit spec or requirement, documented contract (docstring,
types, API doc), independent human judgment, and only last the current code.
Code is the lowest-priority oracle. Promoting it above the others is exactly how
you end up rubber-stamping a bug.

### Step 0: classify the test's intent (do this first)

Before judging any expected value, decide which kind of test it is, because the
oracle changes:

- **Spec / TDD test** - asserts behavior the code should have. The test is the
  authority; if the code disagrees, the *code* is likely wrong. A red test here
  is not a false positive.
- **Characterization / golden-master test** - intentionally freezes current
  behavior to create a refactoring safety net. "Expected copied from output" is
  the point, not a smell, *if it is clearly labeled as such*.
- **Regression test for a known bug** - pins behavior on purpose; check the
  intent is documented.
- **Plain behavior test** - the common case; apply the oracle hierarchy.

A test that pins `calculate_freight(150) == 14.9` is case-18-bad only if the rule
says 15.0 AND the test is not an explicitly labeled characterization snapshot.
When you cannot tell the intent, say so and ask rather than guess.

### Protocol

Work test by test. Prioritize tests touched in the current diff, then scanner
flags, then the rest.

1. **Infer the layer and stack first.** Before judging anything, read the test and
   the code it touches and decide what you are looking at, because the smell idioms
   change with the layer. Two axes: the **layer** (pure logic vs web/API vs
   browser/UI) and the **stack** (Python/pytest vs JS/TS frontend: Jest, Vitest,
   React Testing Library, Playwright, Cypress). The bundled scanner is Python-only;
   JS/TS is yours alone (reference.md, "Frontend cues by language"). State the layer
   in one line, then apply each judgment with that layer's idioms (reference.md,
   "Layer-aware adjustments"). When a unit test quietly hits the network or the DOM,
   treat the hidden layer as the real one and say so.
2. **Find the unit under test.** Read the production function. Derive its
   intended behavior from the oracle hierarchy above. Write down, in one line,
   what the correct output should be for the inputs the test uses.
3. **Read what the test actually asserts.** Not what its name promises, what the
   assertions check. A test named `test_rejects_negative` that never asserts a
   rejection is lying about its purpose.
4. **Compare expected against intended.** Outcomes: matches intent -> sound;
   contradicts intent while passing -> the code has a bug the test froze (case
   18), report the code bug first; expected computed by repeating the production
   logic (case 12) -> flag and propose a concrete hand-written expected value.
   Run the six judgments below; the oracle hierarchy settles J1 and J2, your
   reading settles J3-J6.
5. **Confirm the test can fail.** Ask: "is there a way for the code to be wrong
   and this test to stay green?" If yes, it does not protect what it claims.
   Mutation testing (`mutmut`, `cosmic-ray`) is the automated version; recommend
   it for suites that matter.
6. **Judge value.** A test adds value only if it would go red for a real defect
   in the behavior it covers. Tests that restate the implementation, assert on
   mocks you configured, or duplicate coverage are noise.

### The six judgments (the semantic pass in full)

The scanner proves structure. Every false positive a parser cannot see reduces to
one of six questions about a test. Ask all six, in order, for each test. Each has a
detection cue and the oracle to settle it. The full catalog of smells under each
judgment, marked scanner-owned vs semantic, is in `reference.md` ("Semantic smell
index"). Consult it when a test resists a quick verdict.

**J1. Does the assertion actually run?** A green test whose assertion never
executes protects nothing (the rotten-green family). Cue: assertions only inside an
`if`/`for`, after an early `return`/`raise`, inside an un-awaited coroutine or a
callback the test never forces, or only in one branch of a conditional. Scanner
catches the plain cases (C1, C20, C21); you own the async-never-awaited one. Trace
the control flow: is there an input on this test's path where zero assertions fire?

**J2. Is the oracle independent of the code?** The expected value must come from
spec, contract, or human judgment, never read off current output (case 18) and
never recomputed by repeating the production formula (case 12). Cue: a magic-number
expected with no origin; an expected built by calling the same helper the SUT uses;
a snapshot written from today's output. Settle by the oracle hierarchy.

**J3. Does the test exercise the real unit, or a stand-in?** Mocking the unit under
test (case 10) and then asserting the value you fed the mock (case 11) verifies the
setup, not the program. Cue: the SUT itself is patched; an integration test mocks
the integration it exists to prove; every dependency mocked and none verified. Did
the mock replace an edge (network, disk, clock) or the thing being tested?

**J4. Does it check enough, and the right thing?** A real assertion against a real
result can still be vacuous. Cue: truthiness only (C6); tests MIN+1 and MIN-1 but
never the boundary MIN itself; a name that promises a check the body never makes;
a web response asserted only by status with the body unverified. Name one real
defect that would still pass this test; if you can, the assertion is too weak.
Never resolve this by weakening; add the missing assertion.

**J5. Is it coupled to internals it should not see?** A test bound to private
fields, internal call order, or patched private methods goes red on a safe refactor
with no real defect, and it is what makes tautological mocks possible. Cue: access
to private/underscore attributes, asserting call sequence, patching an internal
method of the SUT. Would a behavior-preserving refactor break this test?

**J6. Does it pass in isolation, or only via shared state?** A test green in the
suite but red alone protects nothing on its own; the green came from a sibling's
leftover state. Cue: it reads state it never writes (a module-global, a singleton,
a DB/file row a prior test set up), or it must run in a fixed order. Would it pass
run first, alone, with a fresh process and clean fixtures? If you cannot tell from
the code, recommend the runtime check (`pytest -p no:randomly`, or run it isolated).

Two cross-cutting reminders the parser is weak on: read `unittest.TestCase`
assertions (`self.assertEqual`, `self.assertRaises(Exception)`) by hand, the scanner
is pytest-`assert`-focused; and a `test_*` whose name promises a behavior the body
never asserts is lying regardless of which judgment catches it.

---

## Output to the user

Lead with the code, then the tests. Group by severity, keep each item actionable.

```
## Tests that do not protect what they claim

### Blocks a real bug (fix the code first)
- src/shipping.py:42  calculate_freight(150) returns 14.9 (rounding bug);
  the rule says 15.0. tests/test_shipping.py:18 asserts 14.9, so the test
  freezes the bug. Fix the function, then update the expected to 15.0.

### Green but checks nothing
- tests/test_parser.py:30  [C2] calls parse() and asserts nothing.
  Add an assertion on the returned structure.

### Smells to confirm
- tests/test_totals.py:12  [C8] exact float equality; use pytest.approx.
```

For each finding give: the location, why it is a false positive (or a frozen
bug), the evidence (cite the code line and the contract you used), and the
concrete fix. When code and test disagree, always say which one you believe is
wrong and why.

---

## Guardrails

- Precision over recall. A wrong verdict on a passing test (the user's CI is
  green, so their prior is that you are wrong) burns trust fast. If you are not
  confident, say "needs review", do not assert a verdict.
- Always ground a case-18 verdict in a cited spec/contract line, never in the
  code's current output alone.
- Never recommend changing a test to match buggy output. If the test contradicts
  the code, decide by the oracle hierarchy and explain.
- Bash is granted only to run the scanner. Never use it to modify test or source
  files (no `>`, `sed -i`, `python -c "...write..."`). Do not edit files unless
  the user explicitly asks; propose the fix and let them apply it.
- Never weaken a test to make a suite pass. Removing an assertion to get green is
  manufacturing a false positive.
- Low-confidence scanner hits are starting points, not verdicts. An `assert`
  inside a `for` over a fixed non-empty list is fine; judge it.
- A disabled code or a baselined finding reflects a team decision; respect it. But
  a baseline does not make a frozen-bug (case 18) verdict wrong - say so if you see
  one behind a suppression.
- The scanner is Python/pytest. The patterns are language-agnostic; apply the
  semantic protocol by hand for other stacks.
