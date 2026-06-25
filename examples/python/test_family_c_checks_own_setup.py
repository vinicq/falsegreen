# falsegreen examples - Family C: the test checks its own setup, not the unit.
#
# Codes: C19, C28, C29
#
# The test configures something (an exception block, a raises binding, an
# environment variable) and then verifies only that the configuration took
# effect, not that the production code behaved correctly.
import os

import pytest


# --- C19: pytest.raises wraps more than one call -----------------------------

# BAD: if build() raises, the SUT call is never reached - wrong line under test.
def test_c19_two_calls():
    with pytest.raises(ValueError):    # C19 - which call raised?
        obj = build()                  # setup may raise here...
        obj.boom()                     # ...target may never run

# CLEAN: only the single target call sits inside the block.
def test_c19_single_call_clean():
    with pytest.raises(ValueError, match="bad"):
        boom()


# --- C28: pytest.raises binding declared but never inspected -----------------

# BAD: the exception type is checked, but its content never is.
def test_c28_binding_unused():
    with pytest.raises(ValueError) as exc_info:   # C28 - exc_info never read
        parse("bad")

# CLEAN: the exception content is asserted by reading its attribute.
def test_c28_inspected_clean():
    with pytest.raises(ValueError) as exc_info:
        parse("bad")
    assert exc_info.value.code == "INVALID_INPUT"

# CLEAN: match= checks the message without a binding.
def test_c28_match_clean():
    with pytest.raises(ValueError, match="invalid"):
        parse("bad")


# --- C29: os.environ mutated directly ----------------------------------------

# BAD: the mutation outlives the test and leaks into sibling tests.
def test_c29_direct_assignment():
    os.environ["DB_URL"] = "sqlite:///:memory:"   # C29 - leaks across tests
    result = load_config()
    assert result.db_url == "sqlite:///:memory:"

# BAD: os.environ.update leaks the same way.
def test_c29_update():
    os.environ.update({"DEBUG": "1"})             # C29
    assert load_config().debug is True

# CLEAN: monkeypatch restores the original value after the test.
def test_c29_monkeypatch_clean(monkeypatch):
    monkeypatch.setenv("DB_URL", "sqlite:///:memory:")
    result = load_config()
    assert result.db_url == "sqlite:///:memory:"
