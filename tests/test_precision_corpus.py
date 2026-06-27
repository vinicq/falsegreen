"""Precision lock for the HIGH-confidence codes.

HIGH findings block a commit, so a false positive on a HIGH code is the most
expensive mistake the scanner can make. Two real ones have shipped before (C7 on a
deliberate __eq__ test, C4 on a route handler named test_*). This file pins one
legitimate look-alike per HIGH code and asserts that no HIGH code fires on it. CI
fails if any HIGH code lights up on the corpus, so a precision regression cannot
merge silently. LOW and diagnostic findings on these snippets are allowed (they do
not block a commit); only HIGH findings are the contract here.
"""
import textwrap

import pytest

from falsegreen.scanner import analyze_file, CASES

HIGH_CODES = sorted(c for c, v in CASES.items() if v[1] == "high")


def _scan(tmp_path, code):
    f = tmp_path / "test_sample.py"
    f.write_text(textwrap.dedent(code), encoding="utf-8")
    return {a.code for a in analyze_file(str(f))}


# One legitimate look-alike per HIGH code: each is a real, correct test (or a
# correctly-recognized non-test) that sits close to the pattern the HIGH code flags,
# yet must stay quiet because it actually protects something / is collected / runs.
CLEAN_LOOKALIKES = {
    # C2 fires on an empty body. A test with a real assertion is the clean case.
    "C2": """
        def test_has_real_check():
            assert compute() == 5
    """,
    # C3 fires when an except swallows the asserted error. Here the except re-raises
    # via pytest.fail and there is a real assertion after the block.
    "C3": """
        import pytest
        def test_except_does_not_swallow():
            try:
                risky()
            except ValueError:
                pytest.fail("should not raise")
            assert after() == 1
    """,
    # C4 fires on a test_* function the runner never collects. A route handler named
    # test_* but decorated as a route is recognized as not-a-test and must not fire C4.
    "C4": """
        from fastapi import APIRouter
        router = APIRouter()
        @router.get("/health")
        def test_health_route():
            return {"ok": True}
    """,
    # C5 fires on an always-true check. A parenthesized real comparison is not C5.
    "C5": """
        def test_parenthesized_comparison():
            assert (compute() == 5)
    """,
    # C7 fires on a value compared to itself. Two separately constructed equal
    # instances (a deliberate __eq__ test) are different expressions, not self-compare.
    "C7": """
        def test_eq_two_instances():
            assert Point(1, 2) == Point(1, 2)
    """,
    # C13 fires on a misspelled mock assertion. A correctly spelled one is clean.
    "C13": """
        from unittest.mock import Mock
        def test_mock_called():
            m = Mock()
            use(m)
            m.assert_called_once()
    """,
    # C17 fires on a skip inside a broad except. A narrow except ImportError that
    # skips an optional-dependency test is a legitimate skip, not a hidden failure.
    "C17": """
        import pytest
        def test_optional_dependency():
            try:
                import fancylib
            except ImportError:
                pytest.skip("fancylib not installed")
            assert fancylib.run() == 1
    """,
    # C20 fires on an assertion after a terminator. The assertion before the return runs.
    "C20": """
        def test_assert_before_return():
            assert compute() == 1
            return
    """,
    # C27 fires on try/except/pass used instead of pytest.raises. The proper form is clean.
    "C27": """
        import pytest
        def test_proper_raises():
            with pytest.raises(ValueError):
                risky()
    """,
    # C38 fires when two test functions share a name. Two distinct names are clean.
    "C38": """
        def test_alpha():
            assert a() == 1
        def test_beta():
            assert b() == 2
    """,
    # C39 fires when a test returns a comparison instead of asserting it. Asserting is clean.
    "C39": """
        def test_asserts_the_result():
            assert compute() == 1
    """,
    # C42 fires on an assertion on a bare generator/lambda (always truthy). Wrapping the
    # generator in all() is a real check, not the always-truthy object.
    "C42": """
        def test_all_over_generator():
            assert all(x > 0 for x in items())
    """,
    # C44 fires on a numeric tautology (len(x) >= 0). An exact count is a real check.
    "C44": """
        def test_exact_length():
            assert len(result()) == 3
    """,
    # C45 fires on an empty parametrize list. A list with cases is clean.
    "C45": """
        import pytest
        @pytest.mark.parametrize("a", [1, 2, 3])
        def test_parametrized(a):
            assert a > 0
    """,
}


def test_every_high_code_has_a_clean_lookalike():
    # If a HIGH code is added without a precision-lock entry, fail loudly so the
    # corpus cannot silently fall behind the catalog.
    missing = [c for c in HIGH_CODES if c not in CLEAN_LOOKALIKES]
    assert not missing, "HIGH codes with no clean look-alike: %s" % missing


@pytest.mark.parametrize("code", sorted(CLEAN_LOOKALIKES))
def test_clean_lookalike_fires_no_high_code(tmp_path, code):
    fired = _scan(tmp_path, CLEAN_LOOKALIKES[code])
    high_fired = fired & set(HIGH_CODES)
    assert not high_fired, (
        "%s clean look-alike triggered HIGH code(s) %s (a blocking false positive)"
        % (code, sorted(high_fired))
    )
