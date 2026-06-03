# Reference: the 18 false-positive patterns

Detection rubric behind `falsegreen`. Each case lists what it looks like, how it
fools you, who catches it (scanner code or semantic pass), the confidence the
scanner assigns, and the related rule in established tooling. The plain-language
version of every case is in `../../docs/guide.md`.

Confidence in the scanner: **HIGH** blocks the commit, **LOW** warns only.
"Semantic" means only the `/falsegreen` deep pass catches it.

---

## Family A: the test never checks anything

| # | Pattern | Scanner code | Confidence | Maps to |
|---|---|---|---|---|
| 1 | `assert` inside an `if`/`for`/`while` that may not run | `C1` | LOW | testsmells.org: Conditional Test Logic |
| 2 | Test with no assertion at all | `C2` (empty body) / `C2b` (has calls) | HIGH / LOW | Empty Test, Unknown Test; flake8-aaa AAA01 |
| 3 | `assert` inside a `try` whose `except` swallows the error | `C3` | HIGH | testsmells.org: Exception Handling; ruff PT017 |
| 4 | Forgotten test pytest never collects (name not `test_`, or a nested test def) | `C4` | HIGH | pytest collection rules |
| 4b | `Test*` class with `__init__` (collected only if subclassed) | `C4b` | LOW | Constructor Initialization |
| 20 | `assert` in dead code after `return`/`raise`/`break`/`continue`/`fail()` | `C20` | HIGH | Fully Rotten Green Test (Soares 2023) |
| 21 | every `assert` is conditional, none runs unconditionally | `C21` | LOW | Context-Dependent Rotten Green (Soares 2023) |
| 22 | `async` test asserts but never awaits the unit | `C22` | OFF by default | The Liar (async) |

Notes.
- Every scanner code carries a judgment tag (J1-J6, see SKILL.md) in `CASES`, so
  text/SARIF/`--summary` output and these docs can group findings by category
  without splitting the module. Family A maps to J1 (does the assertion run?).
- **C1**: legitimate when the loop runs over a fixed, non-empty list. The smell is
  an `assert` guarded by a condition that can be false, so the check silently
  skips. Confirm the loop/branch always executes.
- **C20**: an `assert` (or mock-assert / `pytest.raises`) that follows an
  unconditional `return`/`raise`/`break`/`continue`/`pytest.fail()`/`assert False`
  in the SAME block is structurally unreachable, it never runs. HIGH, same
  certainty as `assert True`. Scanned per block, so a `return` inside one branch
  does not orphan a sibling check at the parent level (that stays clean).
- **C21**: a function-scoped cousin of C1. Fires when the test has at least one
  check but NONE runs unconditionally (every assert is inside an `if`/branch). A
  false condition then passes the test vacuously. Stays clean when a check runs on
  every path: a top-level assert, an exhaustive `if/else` where both sides assert,
  a `with pytest.raises`, or a `for` over a non-empty literal. When C21 fires it
  owns the function's conditional asserts and the per-assert C1 is suppressed, so
  one smell is reported once.
- **C2 vs C2b**: a truly empty body is a near-certain false positive (HIGH). A body
  that calls production code but never asserts is flagged LOW, because the check
  may live in a helper it calls.
- **C4**: the cruelest one, because coverage tools and the green bar both lie. Verify
  with `pytest --collect-only`; the count must rise when you add a test. Function
  names matching a helper prefix (`assert_`, `check_`, `verify_`, ...) are skipped
  to avoid flagging assertion helpers.
- **C4b**: a `Test*` class with `__init__` is not collected by pytest, but that is
  intentional for abstract base test classes that are subclassed. So it only warns
  (LOW), it does not block.

---

## Family B: the check exists but is weak or always true

| # | Pattern | Scanner code | Confidence | Maps to |
|---|---|---|---|---|
| 5 | Always-true assertion (`assert True`, `assert 1`, non-empty tuple, `... or True`) | `C5` | HIGH | testsmells.org: Redundant Assertion |
| 6 | Weak check: truthiness only, `len(x) > 0`, loose `"x" in y` | `C6` | LOW | flake8-assertive; weak-assertion smell |
| 7 | Compares a value to itself (`assert x == x`, both sides identical) | `C7` | HIGH | testsmells.org: Redundant Assertion |
| 8 | Exact equality on a float | `C8` | LOW | floating-point equality smell |
| 9 | `pytest.raises` too broad (`Exception`/`BaseException`, or no `match`) | `C9` | LOW | ruff PT011 / PT010 |
| 18 | Compares `str()`/`repr()`/f-string of a value to a literal | `C18` | LOW | Sensitive Equality (testsmells.org) |
| 19 | `pytest.raises` block wraps more than one statement | `C19` | LOW | Expecting Exceptions Anywhere |

Notes.
- **C5**: the famous `assert (x == y, "msg")` bug, a non-empty tuple, is always
  truthy and silently passes. The scanner flags any non-empty tuple assertion.
- **C6**: a bare truthiness check (`assert result`, `assert obj.attr`) or a
  not-empty proxy (`len(x) > 0`, loose `"x" in y`). A *called* boolean predicate is
  NOT weak and is exempt: `assert isinstance(x, T)`, `assert path.exists()`,
  `assert any(...)`, `assert obj.is_ready()`. The exemption is name-based (the AST
  has no return types), so a predicate-named method that returns a non-bool slips
  through; the semantic pass is the backstop. A bare `assert path.exists` (missing
  parens, always truthy) stays flagged.
- **C7**: detected when both sides are AST-identical. `assert f(d) == f(d)` is the
  disguised version. But `f() is f()` is NOT C7: with `is`, two separate calls
  assert they return the SAME object, the canonical lru_cache / singleton identity
  test. Only an `is` with no call (`x is x`) is always true.
- **C8**: exact `==` on a fractional float (0.1, 0.3, 2.5) fails on rounding, not
  on a bug. `== 0.0` and `== 1.0` are exempt: both are exactly representable and
  are the usual all/none ratio sentinels (0/N, N/N).
- **C9**: a `pytest.raises(Exception)` swallows the wrong error too, including a
  `NameError` from a typo in the test. Pin the exact type and a `match`.
- **C22**: an `async def test_*` that makes calls and asserts but never awaits
  (no `await`/`async with`/`async for`) and does not drive a loop itself
  (`asyncio.run`/`run_until_complete`/`anyio.run`). The unit call returns an
  un-awaited coroutine, so the assertion checks the wrong object or the coroutine
  never runs. OFF by default (async suites opt in via
  `[tool.falsegreen] severity = { C22 = "low" }`); some async tests legitimately
  assert pre-computed sync data.
- **C19**: a `with pytest.raises(...)` block that wraps more than one statement.
  An earlier line can raise the expected error, so the call you meant to test is
  never reached and the test still passes. Keep only the one call that should raise
  inside the block; do the setup above it.
- **C18**: `assert str(x) == "..."` / `repr(x) == "..."` / `f"{x}" == "..."` checks
  how `x` formats, not its value. A repr tweak breaks the test for no real defect,
  and a value bug can hide behind matching text. Assert the value, or a field, not
  its stringification. Comparing a real string attribute (`obj.name == "x"`) is
  fine, not C18.

---

## Family C: the test checks itself, not the program

| # | Pattern | Scanner code | Confidence | Maps to |
|---|---|---|---|---|
| 10 | Mocks the unit under test instead of its edges | semantic | Semantic | over-mocking; testsmells.org: Mock-related |
| 11 | Asserts exactly the value fed to the mock | semantic | Semantic | tautological mock test |
| 12 | Re-implements the production formula to build the expected value | semantic | Semantic | duplicated-logic oracle smell |
| 13 | Misspelled or uncalled mock assertion (`assert_called_once` with no parens, `called_once_with`) | `C13` | HIGH | classic mock footgun; fixed by `autospec` |
| 13b | `patch` without `autospec`/`spec` | `C13b` | LOW | enables case 13; ruff/flake8-mock guidance |
| 14 | Golden/snapshot file written from the current output, then compared | `C14` | LOW | self-confirming snapshot |

Notes.
- **C13**: `mock.assert_called_once` accessed as an attribute (no `()`), or a name
  that is not in the mock API, creates a child mock that accepts everything and
  never verifies. The scanner flags both forms, but only when the receiver is a
  recognized mock (a param whose name contains `mock`, or a name assigned from
  `Mock`/`MagicMock`/`patch`/`create_autospec`). A domain method that happens to be
  named `called_with` is not flagged. `autospec=True` turns the real typos into
  immediate errors.
- **C10/C11/C12** are semantic: a parser sees a mock or an arithmetic expression but
  cannot tell whether the mock replaced an edge or the target, or whether the
  expected value was independently derived. Apply the deep-pass protocol.

---

## Family D: green depends on outside factors

| # | Pattern | Scanner code | Confidence | Maps to |
|---|---|---|---|---|
| 15 | Passes only if another test ran first (shared state, order dependence) | semantic | Semantic | testsmells.org: General Fixture / Mystery Guest |
| 16 | Depends on the clock, randomness, or a fixed `sleep` | `C16` | LOW | testsmells.org: Sleepy Test |
| 17 | `skip` inside a BROAD `except` to hide a real failure | `C17` | HIGH | testsmells.org: Ignored Test (abused) |

Notes.
- **C15** needs runtime evidence (run isolated, run shuffled with
  `pytest -p no:randomly` / `pytest-randomly`). The scanner cannot see cross-test
  state, so this is semantic plus a runtime check.
- **C16**: `time.sleep`, `datetime.now()`, `time.time()`, and unseeded `random.*`
  are flagged. Freeze the clock, seed the RNG, wait on a condition instead of a
  fixed pause.
- **C17**: a `pytest.skip` reached from a BROAD `except` (`Exception`/`BaseException`/
  bare) turns red into yellow and the defect disappears from the radar. A narrow
  guard like `except ImportError: pytest.skip(...)` is the canonical optional-
  dependency pattern and is NOT flagged. Skip only on a declared environment
  condition.

---

## Family E: the test passes but checks the wrong thing

| # | Pattern | Scanner code | Confidence | Maps to |
|---|---|---|---|---|
| 18 | The expected value contradicts what the code should do (freezes a bug) | semantic | Semantic | the core of the oracle-correctness check |

This is the reason the semantic pass exists. The test runs, fails when the code
breaks, checks a real result, and is still wrong, because the expected value was
read off the current (buggy) output instead of derived from an independent oracle.
No static tool catches this. The deep-pass protocol in `SKILL.md` is built around
it: first classify the test's intent (spec/TDD, characterization, regression, or
plain behavior), then derive the intended result from the oracle hierarchy (spec
> contract > human judgment > code, code last), compare against what the test
asserts, and when they disagree, name which side is wrong. TDD tests and labeled
characterization tests are not case-18 violations; do not flag them as such.

---

## Semantic smell index (catalog cross-walk)

The semantic pass decides six questions (SKILL.md, "The six judgments"). This index
maps the test-smell catalog into those six, with a one-line cue. `[scanner: Cn]`
means the AST already flags it (cross-reference the code, do not re-derive it);
`[semantic]` means only this pass catches it. Aliases are folded into one entry.

### J1. Does the assertion actually run? (rotten-green family)

| Catalog smell | Cue | Owner |
|---|---|---|
| Conditional Test Logic / Guarded Test / Nested Conditional | assert nested in `if`/`for` that may not run | `[scanner: C1]` |
| Context-Dependent Rotten Green | every assert is conditional, none unconditional | `[scanner: C21]` |
| Fully Rotten Green | assert after an early `return`/`raise`, structurally bypassed | `[scanner: C20]` |
| The Liar / Asynchronous Code | async test returns before the awaited assertion or callback runs | `[semantic]` (no async modeling yet) |
| Skip Rotten Green | a `skip` reached before the assertion silences it | `[scanner: C17]` / `[semantic]` |

Ask: trace control flow; is there a path on this test's inputs where no assertion
fires?

### J2. Is the oracle independent of the code?

| Catalog smell | Cue | Owner |
|---|---|---|
| Frozen bug (case 18) | magic-number expected read off current output | `[semantic]` |
| Re-implemented oracle (case 12) | expected computed by repeating the SUT's own formula | `[semantic]` |
| Self-confirming snapshot | golden written from current output, then compared to it | `[scanner: C14]` / `[semantic]` |
| Sensitive Equality | asserts `str()`/`repr()` of a value, not the value | `[scanner: C18]` |

Ask: where did this expected value come from, and would it still be right if the
code were wrong? Oracle hierarchy: spec > contract > human > code, code last.

### J3. Real unit or a stand-in? (over-mock / neverfail family)

| Catalog smell | Cue | Owner |
|---|---|---|
| Mocks the unit under test (case 10) | the SUT itself is patched, not its edges | `[semantic]` |
| Tautological mock (case 11) | asserts exactly the value fed to the mock | `[semantic]` |
| Neverfail / Tests That Can't Fail | no assertion, or only mocked calls with no verification | `[scanner: C2/C2b/C5/C13]` / `[semantic]` |

Ask: did the mock replace an edge (network, disk, clock) or the thing being tested?

### J4. Enough, and the right thing? (under-checking family)

| Catalog smell | Cue | Owner |
|---|---|---|
| Weak assertion | truthiness only, `len(x) > 0`, loose `in` | `[scanner: C6]` |
| Underspecification | tests MIN+1 and MIN-1 but never the boundary MIN | `[semantic]` |
| Sneaky Checking | the real check lives in a helper at the wrong level | `[semantic]`; C2b exempts helper-held checks, so open the helper and confirm it asserts |
| Lazy Test / Only Happy Path | covers the trivial case, skips what can break | `[semantic]` |
| Web body-unverified | response asserted only by status, body never read | `[semantic]` (deferred C25) |

Ask: name one real defect that would still pass this test. Never resolve by
weakening; add the missing assertion.

### J5. Coupled to internals? (brittle / false-alarm family)

| Catalog smell | Cue | Owner |
|---|---|---|
| Testing Internal Implementation / The Inspector / X-Ray Specs | asserts private fields, internal state, or call order | `[semantic]` |
| Invasion Of Privacy | test calls a private method directly (`obj._m`) | `[semantic]` |
| Patched private method | the SUT's own internal method is mocked, enabling a tautology | `[semantic]` |

Ask: would a behavior-preserving refactor break this test? If yes, it tests
implementation, not behavior. Flag as a false-alarm risk, not a frozen bug.

### J6. Passes in isolation, or only via shared state?

| Catalog smell | Cue | Owner |
|---|---|---|
| Chain Gang / Order Dependent Tests / Dependent Test | one test mutates shared/DB state; the next depends on it | `[semantic]` (planned C24) |
| Litter Bugs / Test Pollution | a module-global / singleton carries across tests | `[semantic]` (planned C24) |
| Lonely Test / Interacting Tests | passes in the suite, fails alone | `[semantic]` |
| Hidden Dependency | passes only when ambient pre-populated data exists | `[semantic]` |

Ask: does this test pass run first, alone, with a fresh process and clean fixtures?
If you cannot tell from the code, recommend the runtime check (run isolated; run the
module shuffled with `pytest -p no:randomly`; a divergent result confirms it).

### Deliberately out of scope

NOT false-positive detectors, so the pass does not chase them: **Testing many
things** (Eager Test, The Giant, Split Personality, Indirect Testing) is a
localization smell, the test still fails on a real defect; **Assertion Roulette**
and multi-assert-no-message are high-noise in pytest (the runner shows the failing
expression); **naming/wording/size** smells have no bearing on whether the test can
fail; **random-data** smells are C16's deterministic job. Flagging a maintainability
smell as a false positive is itself a false positive, and the guardrail is precision
over recall.

---

## Layer-aware adjustments

The judgments are the same across layers; the idioms that count as a smell are not.
Infer the layer first (SKILL.md protocol step 1), then read each judgment through it.
The rule throughout: soften where a web/UI idiom is legitimately terser than pure
logic, and raise priority only where the layer adds a real false-green path. Never
weaken a pure-logic test by borrowing a web exemption.

- **J1, UI/async layer - RAISE.** An un-awaited async assertion is the top J1 check
  here: `expect(locator).toBeVisible()` with the `await` missing, a Playwright
  `expect` whose promise is never awaited, a Python coroutine asserted without
  `await`, or an assert fired after a fixed `sleep` instead of awaiting a condition.
  All return a pending/truthy object and the test verifies nothing. Flag it high.
- **J2, all layers - a labeled snapshot is NOT a frozen bug.** `toMatchSnapshot()` /
  visual-diff / golden-master is a characterization test (Step 0); do not flag it as
  case 18. The real J2 web smell is an oracle on the response's plumbing (a spy on
  how a fetch was called, an ambient env file) instead of the rendered outcome.
- **J3, web/UI layer.** Mocking the network edge (`fetch`, `axios`, `requests`, an
  MSW handler) is correct, that is the edge. The smell is mocking the
  component/handler under test itself, or asserting the canned response back.
- **J4, web/UI layer - SOFTEN truthiness, ADD body-unverified.** A bare truthiness
  check on a locator or response is the layer's normal idiom
  (`expect(locator).toBeVisible()`), not C6-weak. Pure-logic truthiness stays weak.
  The web-specific J4 smell to add: a response asserted only by status
  (`assert resp.status_code == 200`) with the body never verified.
- **J5, UI layer.** Asserting framework-internal structure (a React component's
  private state, an exact DOM tree, hashed CSS-module class names) breaks on a safe
  refactor. A user-visible assertion (role, accessible name, visible text) is fine.
- **J6, all layers.** Browser/UI suites add their own shared state: a reused
  page/context, `localStorage`/cookies a prior test left, a logged-in session a
  sibling established. Same question: does it pass from a clean context, alone?

---

## Frontend cues by language

The bundled scanner is Python/pytest only. JS/TS tests (Jest, Vitest, React Testing
Library, Playwright, Cypress) get no static pass: judge them by hand against the six
judgments. Precision still rules; a green JS suite means the author's prior is that
you are wrong, so say "needs review" when unsure.

- **Jest / Vitest (unit):** `expect(x).toBeTruthy()` / `.toBeDefined()` as the only
  check -> J4 weak oracle. `expect(value).toBe(value)` both sides identical -> J1
  self-compare. No `expect(...)` at all, or `expect` built but never asserted -> J1
  assertionless. `expect(spy).toHaveBeenCalled()` with no assertion on the real
  output -> J2/J3 interaction-only oracle. `it.skip`/`xit`/`it.only` narrowing the
  run so the rest never executes -> J1. A dropped `await` on `expect(await fn())`,
  or a returned promise the runner does not await -> J1 un-awaited (RAISE).
- **React Testing Library:** `expect(element).toBeInTheDocument()` / `.toBeVisible()`
  is the normal idiom, NOT weak. Querying by `data-testid` or asserting a hashed CSS
  class name -> J5 coupled-to-internals (prefer role/text). A `queryBy...` whose null
  result is never asserted -> J1 (the negative path is never checked).
- **Playwright / Cypress (browser/E2E):** un-awaited `expect` is the top J1 check
  (web-first assertions are promises; without `await` they resolve truthy and check
  nothing). `toMatchSnapshot()`/`toHaveScreenshot()` -> labeled characterization
  (J2/Step 0), not a frozen bug. `page.waitForTimeout(ms)` as the only sync before an
  assert -> J6/C16-style race. Asserting only `response.status()` with the body never
  read -> J4 body-unverified. Reusing a logged-in `context`/`storageState` a prior
  test created -> J6 shared-state.

---

## Scanner code index

`C1 C2 C2b C3 C4 C4b C5 C6 C7 C8 C9 C13 C13b C14 C16 C17 C18 C19 C20 C21 C22 CC` (C22 off by default)

Each code carries a judgment tag (J1-J6) in the scanner's `CASES`, so text/SARIF/
`--summary` output groups by category. Findings also carry a `layer` (logic | web |
browser) in JSON and as a `layer:*` SARIF tag. `CC` is a commented-out `assert`,
flagged LOW by a text scan. Cases 10, 11, 12, 15, and 18 carry no scanner code: they
are semantic-only (see the index above).

## Tools worth pairing with this

- **ruff** (`flake8-pytest-style`, the `PT` rules) for PT010/PT011/PT017/PT018.
- **PyNose**, **pytest-smell**, **TEMPY** for broader test-smell catalogs.
- **mutmut** or **cosmic-ray** for mutation testing, the most honest measure of
  whether a green suite actually fails when the code is wrong.
