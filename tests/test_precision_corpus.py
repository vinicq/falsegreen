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
    # C2 fires on an empty body (only a docstring/pass). A body that is a docstring
    # PLUS one real statement sits one token from empty and must stay clean — a
    # too-broad empty_body that skipped the assertion would fire here.
    "C2": '''
        def test_docstring_then_check():
            """Exercises the happy path."""
            assert compute() == 5
    ''',
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
    # C5 fires on a constant-truthy check, including `X or <truthy-const>`. A boolean
    # OR of two real calls reaches the same BoolOp(Or) branch but has no constant
    # operand, so it must stay clean — one token from `assert is_ready() or True`.
    "C5": """
        def test_or_of_two_calls():
            assert is_ready() or has_fallback()
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
    # C20 fires on an assertion after a terminator (return/raise/pytest.fail). An
    # arbitrary obj.fail() is NOT a terminator, so the assertion after logger.fail()
    # runs — locks the #103 fix (one token from the `pytest.fail()` dead-code case).
    "C20": """
        def test_logs_then_asserts():
            logger.fail("transient")
            assert run() == 1
    """,
    # C27 fires on try/except/pass used instead of pytest.raises. The proper form is clean.
    "C27": """
        import pytest
        def test_proper_raises():
            with pytest.raises(ValueError):
                risky()
    """,
    # C38 fires when two test functions share an identical name. Two names that share
    # a prefix but differ are clean — a too-broad prefix/fuzzy match would fire here.
    "C38": """
        def test_user_create():
            assert create() == 1
        def test_user_update():
            assert update() == 2
    """,
    # C39 fires when a test RETURNS a comparison (`return x == y`). A test that
    # asserts and then returns a non-comparison value reaches the same return branch
    # but is clean — a too-broad "any return" check would fire here.
    "C39": """
        def test_asserts_then_returns_value():
            result = compute()
            assert result == 1
            return result
    """,
    # C42 fires on a bare generator expression / lambda (always truthy). A LIST
    # comprehension is explicitly excluded (it can be empty), so it reaches the same
    # discrimination and stays clean — one bracket from the genexp that WOULD fire.
    "C42": """
        def test_list_comprehension_not_genexp():
            assert [x for x in items() if x > 0]
    """,
    # C44 fires on a numeric tautology (len(x) >= 0, always true). `len(x) > 1`
    # reaches the same len()-comparison branch but can be false, so it must stay
    # clean — one token from the `>= 0` tautology.
    "C44": """
        def test_length_strictly_greater():
            assert len(result()) > 1
    """,
    # C45 fires on an empty parametrize list (generates zero cases). A single-element
    # list reaches the same length check and is clean — one token from the empty `[]`.
    "C45": """
        import pytest
        @pytest.mark.parametrize("a", [1])
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
