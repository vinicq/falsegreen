# falsegreen examples - Family E: the test runs but checks the wrong thing.
#
# Codes: C33, C36, C37
#
# The assertion runs against a real result, yet a metric is never asserted, a
# failure carries no message, or duplicate parametrize cases give false
# confidence in coverage. The deeper Family E smells - an expected value that
# contradicts the spec, a re-implemented formula - need the semantic pass; see
# the NOT-IMPLEMENTED section in README.md and ARCHITECTURE.md.
import pytest
from sklearn.metrics import accuracy_score


# --- C33: an ML metric is computed but never asserted ------------------------

# BAD: model.score() result is discarded - the test asserts nothing about it.
def test_c33_score_discarded(model, X_test, y_test):
    model.score(X_test, y_test)        # C33 - return value thrown away

# BAD: the metric is computed as a bare expression and dropped.
def test_c33_accuracy_discarded(model, X_test, y_test):
    y_pred = model.predict(X_test)
    accuracy_score(y_test, y_pred)     # C33 - never checked

# CLEAN: assert the metric against a meaningful threshold.
def test_c33_threshold_clean(model, X_test, y_test):
    acc = model.score(X_test, y_test)
    assert acc >= 0.8


# --- C36: pytest.fail() without a reason -------------------------------------

# BAD: an empty failure message makes CI output unreadable.
def test_c36_no_reason():
    if compute() < 0:
        pytest.fail()                  # C36 - no message, CI shows just "FAILED"

# CLEAN: a descriptive failure message.
def test_c36_reason_clean():
    if compute() < 0:
        pytest.fail("expected a non-negative result")


# --- C37: duplicate case in @pytest.mark.parametrize -------------------------

# BAD: (1) repeats - the second run covers nothing new.
@pytest.mark.parametrize("x", [1, 2, 1])
def test_c37_duplicate(x):             # C37 - case 1 runs twice
    assert x > 0

# CLEAN: each case is distinct.
@pytest.mark.parametrize("x", [1, 2, 3])
def test_c37_clean(x):
    assert x > 0
