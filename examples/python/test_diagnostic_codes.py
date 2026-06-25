# falsegreen examples - diagnostic and coupling codes (opt-in, OFF by default).
#
# Codes: D1, D3, D4, D5, D6, M2
#
# These do not create false positives: the test still protects something. They
# hurt observability and maintainability, so they are off by default and do not
# affect the exit code. Enable per project, for example in .falsegreen.toml:
#
#     [severity]
#     D1 = "info"
#     D3 = "info"
#     D4 = "info"
#     D5 = "info"
#     D6 = "info"
#     M2 = "info"
#
# Because they are off by default, a plain `falsegreen examples/` reports none of
# these; they appear only when enabled. The CLEAN look-alikes stay quiet even
# when the codes are enabled.
import pytest


# --- D1: Assertion Roulette (2+ asserts, none with a message) ----------------

# BAD: when one of these fails, the output names only a line number.
def test_d1_roulette():
    assert subtotal() == 30            # D1 - which assert failed?
    assert discount() == 3             # D1
    assert total() == 27               # D1

# CLEAN: a single assertion is never Assertion Roulette.
def test_d1_single_clean():
    assert total() == 27

# CLEAN: at least one assertion carries a message.
def test_d1_message_clean():
    assert subtotal() == 30
    assert total() == 27, "total mismatch"


# --- D3: Duplicate Assert (the same assertion written twice) -----------------

# BAD: the repeated assertion adds no coverage.
def test_d3_duplicate():
    user = create_user("alice")
    assert user.email == "alice@example.com"   # D3 - first
    assert user.is_active is True
    assert user.email == "alice@example.com"   # D3 - exact duplicate

# CLEAN: each assertion checks something distinct (messages keep it out of D1).
def test_d3_distinct_clean():
    assert a() == 1, "a wrong"
    assert b() == 2, "b wrong"


# --- D4: Unnamed Parametrize (3+ cases, no ids=) -----------------------------

# BAD: CI shows test[0], test[1], test[2] - hard to read.
@pytest.mark.parametrize("x", [1, 2, 3])
def test_d4_unnamed(x):                # D4 - 3+ cases, no ids=
    assert x > 0

# CLEAN: two cases are below the threshold.
@pytest.mark.parametrize("x", [1, 2])
def test_d4_two_cases_clean(x):
    assert x > 0

# CLEAN: human-readable ids.
@pytest.mark.parametrize("x", [1, 2, 3], ids=["one", "two", "three"])
def test_d4_with_ids_clean(x):
    assert x > 0


# --- D5: Inline Setup Excess (too many setup statements before the assert) ---

# BAD: five setup statements precede the first assert (default threshold 5).
def test_d5_excessive_setup():
    raw = load_data()                  # setup 1
    cleaned = clean(raw)               # setup 2
    normalised = normalise(cleaned)    # setup 3
    grouped = group_by(normalised)     # setup 4
    result = compute(grouped)          # setup 5 - D5: too much inline setup
    assert result > 0

# CLEAN: fewer setup statements before the assert.
def test_d5_lean_clean():
    raw = load_data()
    result = compute(raw)
    assert result > 0


# --- D6: Debug Print in the test body ----------------------------------------

# BAD: a print left over from debugging - suppressed by pytest, just noise.
def test_d6_debug_print():
    result = compute()
    print(result)                      # D6 - debug print
    assert result == 42

# CLEAN: no stray print.
def test_d6_clean():
    result = compute()
    assert result == 42


# --- M2: Long Test Method (body exceeds long_test_threshold lines) -----------
#
# M2 is a coupling code: a test body longer than the threshold (default 50
# lines) tries to verify too many concerns at once. It is shown here as the
# clean, focused alternative - one concern per test - because a 50-line body
# would only add noise to this file. Enable M2 and tune long_test_threshold to
# flag the long form in your own suite.

def test_m2_user_name_clean():
    assert create_user("alice").name == "alice"

def test_m2_user_role_clean():
    assert create_user("alice").role == "guest"
