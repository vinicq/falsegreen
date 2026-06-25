# falsegreen examples - Family B: the check is weak or always true.
#
# Codes: C5, C6, C6b, C7, C8, C9, C11a, C13, C13b, C14, C16, C18, C25, C34,
#        C42, C44
#
# The assertion passes by construction, accepts almost any output, or checks an
# implementation detail instead of a meaningful property.
#
# Each BAD function is flagged; each CLEAN look-alike is left alone. The scanner
# only reads the syntax tree, so the undefined helpers never run.
import datetime

import pytest
from unittest.mock import patch


# --- C5: always-true check ---------------------------------------------------

# BAD: a constant is true by construction.
def test_c5_assert_true():
    assert True                        # C5 - always green

# BAD: a non-empty tuple is always truthy (the classic "msg in the tuple" typo).
def test_c5_truthy_tuple():
    assert (1 == 2, "msg")             # C5 - the tuple is truthy, the == is lost

# CLEAN: assert the real value.
def test_c5_clean():
    assert compute() == 4


# --- C6: weak truthiness check -----------------------------------------------

# BAD: only proves something came back, not what it is.
def test_c6_truthiness():
    assert get_user()                  # C6 - passes for any non-empty result

# BAD: a bare attribute without parens is always truthy.
def test_c6_bare_attribute(path):
    assert path.exists                 # C6 - missing (), always truthy

# CLEAN: an exact-count assertion is a real check.
def test_c6_exact_count_clean():
    assert len(html.split()) == 5

# CLEAN: a boolean predicate call is the result itself, not weak truthiness.
def test_c6_predicate_clean():
    assert isinstance(get_backend(), Backend)
    assert user().is_admin()


# --- C6b: assertion coupled to positional argument layout --------------------

# BAD: an index computed from one call drives a positional subscript on another.
def test_c6b_positional_coupling(mock_fn):
    idx = mock_fn.call_args_list[0].args.index(42)
    assert mock_fn.call_args.args[idx] == 42   # C6b - fragile positional coupling

# CLEAN: assert by name, not position.
def test_c6b_named_clean(mock_fn):
    assert mock_fn.call_args.kwargs["key"] == "value"


# --- C7: self-comparison -----------------------------------------------------

# BAD: both operands are the same name - true by reflexivity.
def test_c7_self_compare():
    assert d == d                      # C7 - always True

# CLEAN: two separate calls test value-equality (would fail under identity).
def test_c7_value_equality_clean():
    assert f(d) == f(d)

# CLEAN: reflexive plus a discriminating peer is a deliberate __eq__ test.
def test_c7_eq_semantics_clean():
    x = IntList([1, 2, 3])
    assert x == x
    assert x != "foo"


# --- C8: exact equality on a float -------------------------------------------

# BAD: floating-point rounding makes exact equality fail on noise, not on bugs.
def test_c8_float_eq():
    assert total() == 0.3              # C8 - fragile exact float

# CLEAN: 0.0 and 1.0 are exact all/none sentinels, not the rounding smell.
def test_c8_sentinel_clean():
    assert ratio() == 1.0
    assert ratio() == 0.0


# --- C9: pytest.raises too broad ---------------------------------------------

# BAD: any exception passes, including a typo in the test itself.
def test_c9_broad_raises():
    with pytest.raises(Exception):     # C9 - too broad
        boom()

# CLEAN: a specific type plus a message pattern.
def test_c9_clean():
    with pytest.raises(ValueError, match="bad"):
        boom()


# --- C11a: self-confirming literal -------------------------------------------

# BAD: asserts the very value the test handed the constructor.
def test_c11a_self_confirming():
    obj = MyClass(name="alice")
    assert obj.name == "alice"         # C11a - checks Python assignment, not logic

# CLEAN: the value comes from the unit under test.
def test_c11a_from_sut_clean():
    obj = service.get_user(1)
    assert obj.name == "alice"


# --- C13 / C13b: mock assertion that does not run ----------------------------

# BAD: missing parentheses - the bound method is truthy, nothing is checked.
def test_c13_missing_parens(mock):
    mock.assert_called_once             # C13 - no (), no check

# BAD: a @patch-injected mock without autospec lets argument typos pass.
@patch("mod.svc")
def test_c13b_no_autospec(svc):
    do(svc)
    svc.assert_called_once             # C13 - no-parens assertion on a mock

# CLEAN: a real mock assertion with parentheses.
def test_c13_clean(mock):
    do(mock)
    mock.assert_called_once_with(1)


# --- C14: golden file written from the code's own output ---------------------

# BAD: the first run records whatever the code produces - including bugs.
def test_c14_golden_from_output():
    if not golden.exists():
        golden.write_text(render_output())   # C14 - today's output becomes truth

# CLEAN: a committed golden value, compared but never overwritten by the test.
def test_c14_committed_golden_clean():
    expected = golden.read_text()
    assert render_output() == expected


# --- C16: result depends on the clock, randomness, or a sleep ----------------
#
# C16 is suppressed file-wide when a time-control library (freezegun,
# time_machine) is imported, so the frozen-clock clean look-alike lives in its
# own file: test_c16_time_controlled.py.

# BAD: datetime.now() is not frozen, so the assertion drifts with wall time.
def test_c16_raw_now():
    assert datetime.datetime.now().hour == 12   # C16 - clock not controlled

# BAD: a thread join with a wall-clock timeout is a fragile timing wait.
def test_c16_join_timeout():
    import threading
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join(timeout=10)                          # C16 - wall-clock timing
    assert not t.is_alive()

# CLEAN: a deterministic value, no clock or randomness read at all.
def test_c16_deterministic_clean():
    assert compute() == 42


# --- C18: comparing str()/repr()/f-string to a literal -----------------------

# BAD: couples to formatting, not the value.
def test_c18_str_compare():
    assert str(amount) == "5.00"       # C18 - checks the formatted string

# CLEAN: compare the real attribute.
def test_c18_clean():
    assert user.name == "ada"


# --- C25: xfail without strict=True ------------------------------------------

# BAD: if the test unexpectedly passes, pytest reports XPASS but stays green.
@pytest.mark.xfail
def test_c25_no_strict():
    assert compute() == 42             # C25 - XPASS silently accepted

# CLEAN: strict=True turns an unexpected pass into a failure.
@pytest.mark.xfail(strict=True)
def test_c25_strict_clean():
    assert compute() == 42


# --- C34: suboptimal assert form ---------------------------------------------

# BAD: each has a clearer idiomatic form.
def test_c34_not_in():
    assert not "x" in ["a", "b"]       # C34 - use: assert "x" not in [...]

def test_c34_eq_true():
    assert is_valid() == True          # C34 - use: assert is_valid()

def test_c34_eq_none():
    assert get_result() == None        # C34 - use: assert get_result() is None

# CLEAN: the idiomatic forms.
def test_c34_clean():
    assert "x" not in ["a", "b"]
    assert is_valid()
    assert get_result() is None


# --- C42: assert on a generator expression or lambda (always truthy) ---------

# BAD: the generator object is truthy regardless of its contents.
def test_c42_genexpr():
    assert (x for x in get_items())    # C42 - always passes, even when empty

# BAD: a lambda object is always truthy and never called.
def test_c42_lambda():
    assert lambda: do()                # C42 - never runs do()

# CLEAN: a list comprehension can be empty, so the check is real (if weak).
def test_c42_listcomp_clean():
    assert [x for x in get_items()]


# --- C44: numeric tautology --------------------------------------------------

# BAD: len() is never negative, so this is always true.
def test_c44_len_ge_zero():
    assert len(get_items()) >= 0       # C44 - passes for any input

# CLEAN: compare to a real expected bound.
def test_c44_clean():
    assert len(get_items()) >= 3
