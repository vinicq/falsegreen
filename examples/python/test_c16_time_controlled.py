# falsegreen examples - C16 clean look-alike (time-controlled file).
#
# C16 flags a test whose result depends on the clock, randomness, or a sleep.
# The scanner suppresses C16 for an entire file when a time-control library is
# imported (freezegun, time_machine), because the clock is no longer free.
#
# This file imports freezegun on purpose, so reading datetime.now() here is not
# flagged. The BAD counterpart (a raw datetime.now() with no clock control)
# lives in test_family_b_weak_always_true.py, which imports no time-control library.
#
# Expected scanner result for this file: no findings.
import datetime

from freezegun import freeze_time


# CLEAN: the clock is frozen, so this assertion is deterministic - not C16.
def test_c16_frozen_clock_clean():
    with freeze_time("2024-01-01 12:00:00"):
        assert datetime.datetime.now().hour == 12
