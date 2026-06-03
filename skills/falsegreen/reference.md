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

Notes.
- **C1**: legitimate when the loop runs over a fixed, non-empty list. The smell is
  an `assert` guarded by a condition that can be false, so the check silently
  skips. Confirm the loop/branch always executes.
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
- **C7**: detected when both sides are AST-identical. Calling the same function on
  both sides (`assert f(d) == f(d)`) is the disguised version.
- **C9**: a `pytest.raises(Exception)` swallows the wrong error too, including a
  `NameError` from a typo in the test. Pin the exact type and a `match`.

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

## Scanner code index

`C1 C2 C2b C3 C4 C4b C5 C6 C7 C8 C9 C13 C13b C14 C16 C17 CC`

`CC` is a commented-out `assert` (a check switched off), flagged LOW by a text
scan. Cases 10, 11, 12, 15, and 18 carry no scanner code: they are semantic-only.

## Tools worth pairing with this

- **ruff** (`flake8-pytest-style`, the `PT` rules) for PT010/PT011/PT017/PT018.
- **PyNose**, **pytest-smell**, **TEMPY** for broader test-smell catalogs.
- **mutmut** or **cosmic-ray** for mutation testing, the most honest measure of
  whether a green suite actually fails when the code is wrong.
