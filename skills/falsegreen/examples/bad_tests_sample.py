"""
Sample of false-positive tests, one per detectable case.

This file is intentionally full of bad tests. It is the demo and regression
fixture for the scanner. Run:

    python -m falsegreen skills/falsegreen/examples/bad_tests_sample.py

and you should see HIGH and LOW findings mapped to the case codes.
Do not "fix" these: they exist to prove the scanner still fires.
"""
import pytest
from unittest import mock


def test_c5_always_true():
    assert True


def test_c5_tuple_bug():
    assert (1 == 2, "this tuple is always truthy")


def test_c2_empty():
    pass


def test_c2b_no_assert():
    compute(3)


def test_c7_self_compare():
    assert format(d) == format(d)


def test_c6_weak():
    assert result
    assert len(out) > 0
    assert "ok" in str(resp)


def test_c8_float():
    assert total == 0.3


def test_c1_conditional():
    if result:
        assert result.status == "ok"


def test_c3_swallow():
    try:
        assert resp["code"] == "err"
    except Exception:
        pass


def test_c17_skip_on_except():
    try:
        run()
    except Exception:
        pytest.skip("broke")


def test_c13_mock_typo(mock_send):
    mock_send.assert_called_once  # no parens: checks nothing


def test_c13_false_name(mock_send):
    mock_send.called_once_with(1)  # not part of the mock API


def test_c13b_no_autospec():
    mock.patch("mod.fn", return_value=1)


def test_c9_broad_raises():
    with pytest.raises(Exception):
        withdraw(acc, -1)


def test_c16_fixed_sleep():
    import time
    time.sleep(2)


def verifica_total():  # C4: name does not start with test_, pytest skips it
    assert soma([1, 2]) == 3


class TestThing:  # C4b: __init__ means pytest skips it unless subclassed
    def __init__(self):
        self.x = 1

    def test_inside_bad_class(self):
        assert self.x == 1
