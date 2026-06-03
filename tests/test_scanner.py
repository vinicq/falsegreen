"""Real tests for the scanner. The false-positive detector cannot itself be one."""
import textwrap

from falsegreen.scanner import run, analyze_file


def scan_source(tmp_path, code):
    f = tmp_path / "test_sample.py"
    f.write_text(textwrap.dedent(code), encoding="utf-8")
    return {a.code for a in analyze_file(str(f))}


# --- it must flag the real smells -----------------------------------------

def test_flags_always_true(tmp_path):
    assert "C5" in scan_source(tmp_path, """
        def test_x():
            assert True
    """)


def test_flags_truthy_tuple(tmp_path):
    assert "C5" in scan_source(tmp_path, """
        def test_x():
            assert (1 == 2, "msg")
    """)


def test_flags_empty_test(tmp_path):
    assert "C2" in scan_source(tmp_path, """
        def test_x():
            pass
    """)


def test_flags_self_compare(tmp_path):
    assert "C7" in scan_source(tmp_path, """
        def test_x():
            assert f(d) == f(d)
    """)


def test_flags_swallowing_try(tmp_path):
    assert "C3" in scan_source(tmp_path, """
        def test_x():
            try:
                assert resp() == 1
            except Exception:
                pass
    """)


def test_flags_skip_on_broad_except(tmp_path):
    assert "C17" in scan_source(tmp_path, """
        import pytest
        def test_x():
            try:
                run()
            except Exception:
                pytest.skip("broke")
    """)


def test_flags_mock_typo_without_parens(tmp_path):
    assert "C13" in scan_source(tmp_path, """
        def test_x(mock):
            mock.assert_called_once
    """)


def test_flags_uncollected_function(tmp_path):
    assert "C4" in scan_source(tmp_path, """
        def verifica_total():
            assert soma() == 3
    """)


def test_flags_broad_raises(tmp_path):
    assert "C9" in scan_source(tmp_path, """
        import pytest
        def test_x():
            with pytest.raises(Exception):
                boom()
    """)


def test_flags_float_equality(tmp_path):
    assert "C8" in scan_source(tmp_path, """
        def test_x():
            assert total() == 0.3
    """)


# --- regressions: it must NOT flag legitimate code (review counter-examples) -

def test_clean_test_has_no_findings(tmp_path):
    assert scan_source(tmp_path, """
        def test_add():
            assert add(2, 2) == 4
    """) == set()


def test_specific_raises_is_clean(tmp_path):
    assert scan_source(tmp_path, """
        import pytest
        def test_x():
            with pytest.raises(ValueError, match="bad"):
                boom()
    """) == set()


def test_optional_dependency_skip_is_not_flagged(tmp_path):
    # try/except ImportError -> skip is the canonical optional-dep guard, not a smell
    codes = scan_source(tmp_path, """
        import pytest
        def test_numpy_path():
            try:
                import numpy
            except ImportError:
                pytest.skip("numpy not installed")
            assert numpy.array([1]).sum() == 1
    """)
    assert "C17" not in codes


def test_domain_method_named_called_with_is_not_a_mock_typo(tmp_path):
    # obj is not a mock; a domain method called_with must not trip C13
    codes = scan_source(tmp_path, """
        def test_observer():
            obj.called_with(3)
            assert obj.state == 1
    """)
    assert "C13" not in codes


def test_patch_decorator_injected_mock_typo_is_flagged(tmp_path):
    # @patch injects a mock as a positional arg; a no-parens assertion on it is C13
    codes = scan_source(tmp_path, """
        from unittest.mock import patch
        @patch("mod.svc")
        def test_injected(svc):
            do(svc)
            svc.assert_called_once
    """)
    assert "C13" in codes


def test_run_prefixed_forgotten_test_is_flagged(tmp_path):
    # run_/do_/get_ are no longer treated as helper prefixes
    assert "C4" in scan_source(tmp_path, """
        def run_full_pipeline():
            assert pipeline() == "done"
    """)


def test_valid_mock_assert_call_is_clean(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x(mock):
            do(mock)
            mock.assert_called_once_with(1)
    """)
    assert "C13" not in codes


def test_abstract_base_test_class_is_low_not_high(tmp_path):
    codes = scan_source(tmp_path, """
        class TestBase:
            def __init__(self, subject):
                self.subject = subject
            def test_contract(self):
                assert self.subject().run() == 1
    """)
    assert "C4" not in codes      # must not BLOCK
    assert "C4b" in codes         # warns instead


def test_cleanup_only_try_is_not_swallow(tmp_path):
    # best-effort teardown with no assertion in the try body is not C3
    codes = scan_source(tmp_path, """
        def test_x():
            assert compute() == 1
            try:
                cleanup()
            except Exception:
                pass
    """)
    assert "C3" not in codes


def test_exact_len_count_is_not_weak(tmp_path):
    # len(x) == N is an exact-count assertion, the fix the guide recommends
    codes = scan_source(tmp_path, """
        def test_x():
            assert len(html.split()) == 5
    """)
    assert "C6" not in codes


def test_helper_function_is_not_uncollected_test(tmp_path):
    codes = scan_source(tmp_path, """
        def assert_valid_user(u):
            assert u.id is not None
    """)
    assert "C4" not in codes


# --- C6: boolean predicates are real checks, not weak truthiness -----------
# (regression: real-project validation flagged isinstance/exists/any as C6)

def test_isinstance_assert_is_not_weak(tmp_path):
    # isinstance(...) inside assert is a genuine type assertion, not "came back"
    codes = scan_source(tmp_path, """
        def test_x():
            assert isinstance(get_backend(), Backend)
    """)
    assert "C6" not in codes


def test_path_predicate_calls_are_not_weak(tmp_path):
    # .exists()/.is_dir()/any() return real booleans: the expected result itself
    codes = scan_source(tmp_path, """
        def test_x(tmp_path):
            p = build(tmp_path)
            assert p.exists()
            assert p.is_dir()
            assert any(p.iterdir())
    """)
    assert "C6" not in codes


def test_is_prefixed_predicate_call_is_not_weak(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x():
            assert user().is_admin()
    """)
    assert "C6" not in codes


def test_bare_truthiness_call_is_still_weak(tmp_path):
    # the actual smell C6 targets: assert <value that just came back> survives
    codes = scan_source(tmp_path, """
        def test_x():
            assert get_user()
    """)
    assert "C6" in codes


def test_bare_attribute_predicate_without_parens_is_still_weak(tmp_path):
    # a bare `path.exists` (missing parens) is always truthy: must stay flagged
    codes = scan_source(tmp_path, """
        def test_x(path):
            assert path.exists
    """)
    assert "C6" in codes


# --- C1: a loop over a non-empty literal always runs -----------------------

def test_assert_in_loop_over_literal_tuple_is_not_c1(tmp_path):
    # for q in (a, b, c): assert ... -> the body always runs, not C1
    codes = scan_source(tmp_path, """
        def test_x(sm):
            for q in (sm.a, sm.b, sm.c):
                assert q.maxsize == 200
    """)
    assert "C1" not in codes


def test_assert_in_loop_over_runtime_value_is_still_c1(tmp_path):
    # iterating a runtime value that may be empty -> assert can be skipped: C1
    codes = scan_source(tmp_path, """
        def test_x(results):
            for meta in results["rows"]:
                assert meta["id"] == 1
    """)
    assert "C1" in codes


def test_assert_in_if_is_still_c1(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x(cond):
            if cond:
                assert compute() == 1
    """)
    assert "C1" in codes


# --- config knobs ----------------------------------------------------------

def test_inline_ignore_silences_finding(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x():
            assert total() == 0.3  # falsegreen: ignore[C8]
    """)
    assert "C8" not in codes


def test_disable_flag(tmp_path):
    f = tmp_path / "test_d.py"
    f.write_text("def test_x():\n    assert total() == 0.3\n", encoding="utf-8")
    codes = {a.code for a in run([str(f)], disable={"C8"})}
    assert "C8" not in codes


def test_exit_code_high(tmp_path):
    f = tmp_path / "test_h.py"
    f.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    from falsegreen.scanner import main
    assert main([str(f)]) == 20
