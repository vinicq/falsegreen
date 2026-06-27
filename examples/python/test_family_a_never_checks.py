# falsegreen examples - Family A: the test never checks anything.
#
# Codes: C1, C2, C2b, C3, C4, C4b, C17, C20, C21, C27, C38, C39, C43, C45, CC
#
# The assertion is skipped, missing, swallowed, never collected, or stranded
# after a terminator. The test stays green whether the code is right or wrong.
#
# Each BAD function is one the scanner flags; each CLEAN look-alike is one it
# leaves alone. The bodies call helpers that do not exist on purpose: the
# scanner reads the syntax tree, it never imports or runs this file. The sibling
# conftest.py keeps pytest from collecting it.
import pytest


# --- C1: assert inside an if/for that may never run --------------------------

# BAD: a top-level assert runs, but the conditional one can be skipped.
def test_c1_conditional_assert(results):
    assert results is not None
    for row in results["rows"]:
        assert row["id"] == 1          # C1 - skipped when rows is empty

# CLEAN: a loop over a non-empty literal always runs the body.
def test_c1_literal_loop_clean(sm):
    for q in (sm.a, sm.b, sm.c):
        assert q.maxsize == 200


# --- C2 / C2b: no real check ------------------------------------------------

# BAD: empty body proves only that nothing raised.
def test_c2_empty_body():
    pass                               # C2 - no assertion at all

# BAD: calls the unit, discards the result.
def test_c2b_discards_result():
    result = process(data)             # C2b - result never asserted

# CLEAN: a real check on the returned value.
def test_c2b_asserts_result_clean():
    result = process(data)
    assert result == 42


# --- C3: assert inside a try whose except swallows it ------------------------

# BAD: the AssertionError is caught and silenced.
def test_c3_swallowed_assert():
    try:
        assert resp() == 1             # C3 - failure discarded by except
    except Exception:
        pass

# CLEAN: best-effort teardown with the real check outside the try.
def test_c3_cleanup_only_clean():  # falsegreen: ignore[M2]  (length is incidental, not the smell)
    assert compute() == 1
    try:
        cleanup()
    except Exception:
        pass


# --- C4 / C4b: the runner never collects the test ----------------------------

# BAD: a test-named function with no test_ prefix file collection path; pytest
# never collects this top-level helper, so its assert never runs.
def verifica_total():
    assert soma() == 3                 # C4 - not collected, never runs

# BAD: a Test* class with __init__ is skipped by pytest.
class TestProcessorBad:
    def __init__(self):                # C4b - pytest skips classes with __init__
        self.proc = Processor()

    def test_run(self):
        assert self.proc.run() == 1

# CLEAN: a real, collectible test.
def test_collected_clean():
    assert add(2, 2) == 4


# --- C17: skip inside a broad except turns a failure into a skip -------------

# BAD: a real assertion failure becomes a skip and the suite stays green.
def test_c17_skip_on_failure():
    try:
        run()
    except Exception:
        pytest.skip("broke")           # C17 - red turns yellow

# CLEAN: the optional-dependency guard - skip on ImportError, then assert.
def test_c17_optional_dep_clean():  # falsegreen: ignore[M2]  (length is incidental, not the smell)
    try:
        import numpy
    except ImportError:
        pytest.skip("numpy not installed")
    assert numpy.array([1]).sum() == 1


# --- C20: assertion stranded in dead code after a terminator -----------------

# BAD: the assert after the unconditional return never runs.
def test_c20_dead_assert():
    assert setup() == 1
    return
    assert teardown() == 0             # C20 - unreachable

# CLEAN: an early-return guard does not orphan a later assert.
def test_c20_guard_clean():
    if skip_condition():
        return
    assert compute() == 1


# --- C21: every assertion is conditional, none runs unconditionally ----------

# BAD: when cond is false, the test checks nothing.
def test_c21_all_conditional(cond):
    if cond:
        assert a() == 1                # C21 - vacuous when cond is false
    else:
        log("skip")

# CLEAN: a with-raises block is an unconditional check, never vacuous.
def test_c21_raises_clean():
    with pytest.raises(ValueError, match="bad"):
        boom()


# --- C27: try/except/pass instead of pytest.raises ---------------------------

# BAD: passes whether risky() raises or not.
def test_c27_try_except_pass():
    try:
        risky()                        # C27 - no raise: green; raise: swallowed
    except ValueError:
        pass

# CLEAN: pytest.raises states the intent and fails if nothing raises.
def test_c27_raises_clean():
    with pytest.raises(ValueError, match="out of range"):
        risky()


# --- C38: two tests share a name; the later one overrides the first ----------

# BAD: the first test_login is rebound and never runs.
def test_login():
    assert authenticate("alice", "pw1") is True   # C38 - shadowed below

def test_login():  # noqa: F811
    assert authenticate("bob", "pw2") is True

# CLEAN: distinct names, both run.
def test_login_alice_clean():
    assert authenticate("alice", "pw1") is True

def test_login_bob_clean():
    assert authenticate("bob", "pw2") is True


# --- C39: returns a comparison instead of asserting it -----------------------

# BAD: pytest ignores the returned value (PytestReturnNotNoneWarning).
def test_c39_returns_comparison():
    return add(2, 2) == 4              # C39 - nothing checks the comparison

# CLEAN: assert it.
def test_c39_clean():
    assert add(2, 2) == 4


# --- C43: pytest.skip() after test logic strands the checks below it ---------

# BAD: the arrange ran, then skip, so the assertion is dead.
def test_c43_mid_test_skip():
    result = build()
    pytest.skip("not ready")           # C43 - assertion below never runs
    assert result == 42

# CLEAN: a SUT method named skip() is not pytest.skip - no finding.
def test_c43_sut_skip_clean():
    reader = open_reader()
    reader.skip(1)
    assert reader.read() == 42


# --- C45: empty parametrize list - the test runs zero times ------------------

# BAD: zero cases generated, the test is collected but never runs.
@pytest.mark.parametrize("n", [])
def test_c45_empty_params(n):          # C45 - never executes
    assert process(n) > 0

# CLEAN: populate the table.
@pytest.mark.parametrize("n", [1, 2, 3])
def test_c45_clean(n):
    assert process(n) > 0


# --- CC: commented-out assert ------------------------------------------------

# BAD: the check was switched off; the test is green by omission.
def test_cc_commented():
    result = compute()
    # assert result == 42              # CC - the only check is commented out
    log(result)

# CLEAN: the assertion is live.
def test_cc_clean():
    result = compute()
    assert result == 42
