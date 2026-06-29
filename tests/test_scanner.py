"""Real tests for the scanner. The false-positive detector cannot itself be one."""
import textwrap

import json
import xml.etree.ElementTree as ET

from falsegreen.scanner import (
    run, analyze_file, main, load_config, effective_conf,
    render_sarif, render_junit, render_json, summary_line,
    fingerprint, load_baseline,
)


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


def test_c2c_empty_subtest_block(tmp_path):
    # A self.subTest(...) that wraps work but asserts nothing is the subTest analogue
    # of an empty test. C2c owns it (more specific than the generic C2b).
    codes = scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_x(self):
                for i in [1, 2]:
                    with self.subTest(i=i):
                        do_thing(i)
    """)
    assert "C2c" in codes
    assert "C2b" not in codes


def test_no_c2c_when_subtest_asserts(tmp_path):
    assert "C2c" not in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_x(self):
                for i in [1, 2]:
                    with self.subTest(i=i):
                        self.assertEqual(f(i), i)
    """)


def test_c2c_does_not_fire_when_a_verification_helper_is_called_in_subtest(tmp_path):
    # A subTest that delegates to a check_*/verify_* helper is exempt (mirrors C2b).
    codes = scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_x(self):
                with self.subTest():
                    check_invariant(state)
    """)
    assert "C2c" not in codes


def test_no_c2c_on_a_non_unittest_subtest_method(tmp_path):
    # An arbitrary object's .subTest() is not unittest's: receiver-anchored to self/cls,
    # so this must NOT fire C2c (guards against a leaf-match false positive).
    assert "C2c" not in scan_source(tmp_path, """
        def test_x():
            ctx = make_ctx()
            with ctx.subTest():
                ctx.run()
    """)


def test_hypothesis_given_is_not_empty(tmp_path):
    # a @given body with no explicit assert is idiomatic: the oracle is
    # "no exception over all generated inputs", so it is neither C2 nor C2b.
    out = scan_source(tmp_path, """
        @given(st.integers())
        def test_x(n):
            encode(n)
    """)
    assert "C2" not in out
    assert "C2b" not in out


def test_hypothesis_given_empty_body_is_not_c2(tmp_path):
    out = scan_source(tmp_path, """
        @given(st.integers())
        def test_x(n):
            pass
    """)
    assert "C2" not in out


def test_skip_decorated_empty_test_is_not_c2(tmp_path):
    # an empty body under @pytest.mark.skip is a deliberate placeholder, not a
    # rotten-green test: the marker stops it running and passing silently.
    out = scan_source(tmp_path, """
        @pytest.mark.skip(reason="not implemented")
        def test_x():
            pass
    """)
    assert "C2" not in out


def test_sympy_SKIP_decorator_not_c2(tmp_path):
    # sympy defines its own uppercase SKIP decorator (sympy.testing.pytest.SKIP)
    # that wraps the test to raise Skipped — same semantics as @pytest.mark.skip.
    # Empty bodies under @SKIP("abstract class") are deliberate placeholders.
    out = scan_source(tmp_path, """
        from sympy.testing.pytest import SKIP
        @SKIP("abstract class")
        def test_abstract_thing():
            pass
    """)
    assert "C2" not in out


def test_class_level_skip_exempts_empty_methods(tmp_path):
    # a class-level @mark.skip makes every empty method a placeholder (paramiko
    # TestCanonicalizationOfCNAMEs). No C2 on the methods.
    out = scan_source(tmp_path, """
        @mark.skip
        class TestCanon:
            def test_one_to_one(self):
                pass
            def test_many_to_many(self):
                pass
    """)
    assert "C2" not in out


def test_empty_test_in_non_collected_file_is_not_flagged(tmp_path):
    # a `def test_*` in a module pytest does not collect (not test_*.py / *_test.py
    # / conftest.py) is never run, so its empty body is not a false-green test:
    # pylint's tests/functional/*.py lint fixtures, black's tests/data/cases/*.py.
    f = tmp_path / "missing_param_doc.py"
    f.write_text("def test_tolerate(x, y):\n    pass\n", encoding="utf-8")
    codes = {a.code for a in analyze_file(str(f))}
    assert "C2" not in codes


def test_empty_test_in_collected_file_still_flagged(tmp_path):
    # the same empty test in a real test_*.py file IS a rotten-green test.
    f = tmp_path / "test_real.py"
    f.write_text("def test_tolerate():\n    pass\n", encoding="utf-8")
    codes = {a.code for a in analyze_file(str(f))}
    assert "C2" in codes


def test_flags_self_compare(tmp_path):
    # bare operands compared to themselves are a tautology (no call: a call on
    # each side could be a real __eq__/identity check, see test below).
    assert "C7" in scan_source(tmp_path, """
        def test_x():
            assert d == d
    """)


def test_self_compare_with_call_is_not_c7(tmp_path):
    # `cls(1) == cls(1)` / `f(d) == f(d)` is the canonical value-equality test,
    # not a tautology: with default identity __eq__ it would fail.
    assert "C7" not in scan_source(tmp_path, """
        def test_x():
            assert f(d) == f(d)
    """)


def test_flags_bare_name_is_compare(tmp_path):
    # `x is x` (no call) is always true: still C7
    assert "C7" in scan_source(tmp_path, """
        def test_x():
            assert obj is obj
    """)


def test_is_between_two_calls_is_not_self_compare(tmp_path):
    # f() is f() asserts two calls return the SAME object: the canonical
    # lru_cache / singleton identity test, NOT a tautology. Must not be C7.
    codes = scan_source(tmp_path, """
        def test_loader_is_cached():
            assert load_module() is load_module()
    """)
    assert "C7" not in codes


def test_is_between_two_method_calls_is_not_self_compare(tmp_path):
    codes = scan_source(tmp_path, """
        def test_singleton():
            assert registry.get() is registry.get()
    """)
    assert "C7" not in codes


def test_lone_self_compare_still_flags_with_a_distinct_neq(tmp_path):
    # a `x == x` next to `y != z` (a DIFFERENT operand) is still a lone
    # tautology: the inequality is not about x, so C7 must still fire.
    codes = scan_source(tmp_path, """
        def test_x():
            assert a == a
            assert b != c
    """)
    assert "C7" in codes


def test_eq_reflexivity_pair_is_not_self_compare(tmp_path):
    # reflexive (x == x) + discriminating (not x == y) on the SAME operand is a
    # legitimate __eq__ semantics test (aiohttp test_stream_response_eq). No C7.
    codes = scan_source(tmp_path, """
        def test_stream_response_eq():
            resp1 = web.StreamResponse()
            resp2 = web.StreamResponse()
            assert resp1 == resp1
            assert not resp1 == resp2
    """)
    assert "C7" not in codes


def test_eq_reflexivity_with_neq_is_not_self_compare(tmp_path):
    # same pattern written with != (aiohttp test_eq): reflexive + !=. No C7.
    codes = scan_source(tmp_path, """
        def test_eq():
            req1 = make_request()
            req2 = make_request()
            assert req1 != req2
            assert req1 == req1
    """)
    assert "C7" not in codes


def test_self_compare_with_neq_none_still_flags(tmp_path):
    # `x == x` next to `x != None` is NOT an __eq__ semantics test: None is a
    # constant, not a distinct peer object. The tautology must still fire.
    codes = scan_source(tmp_path, """
        def test_x():
            assert obj == obj
            assert obj != None
    """)
    assert "C7" in codes


def test_self_compare_with_unrelated_membership_still_flags(tmp_path):
    # `x == x` next to `x in some_registry` (membership in a non-literal
    # container) is not eq/hash testing of a literal holding x. Still C7.
    codes = scan_source(tmp_path, """
        def test_x():
            assert obj == obj
            assert obj in some_registry
    """)
    assert "C7" in codes


def test_eq_hash_membership_pair_is_not_self_compare(tmp_path):
    # reflexive (ws == ws) + a membership check on the same operand (ws in {ws})
    # is a deliberate __eq__/__hash__ test (starlette test_websockets). No C7.
    codes = scan_source(tmp_path, """
        def test_websocket_hashable():
            websocket = WebSocket(scope)
            assert websocket == websocket
            assert websocket in {websocket}
    """)
    assert "C7" not in codes


def test_identity_pair_with_is_not_peer_is_not_self_compare(tmp_path):
    # reflexive identity (x is x) + a discriminating `is not <distinct peer>` on
    # the same operand is a deliberate identity test (scrapy cached-property /
    # urllib3 copy tests). No C7.
    codes = scan_source(tmp_path, """
        def test_copy():
            assert request.flags is request.flags
            assert request.flags is not original_flags
    """)
    assert "C7" not in codes


def test_self_compare_with_neq_literal_is_not_self_compare(tmp_path):
    # `x == x` next to `x != "foo"` (a non-trivial literal of a different type)
    # is an __eq__ semantics test: it proves __eq__ discriminates (hypothesis
    # test_basic_equality / arrow test_eq). No C7.
    codes = scan_source(tmp_path, """
        def test_basic_equality():
            x = IntList([1, 2, 3])
            assert x == x
            assert x != "foo"
    """)
    assert "C7" not in codes


def test_eq_hash_pair_is_not_self_compare(tmp_path):
    # reflexive (i == i) + a companion hash(i) check is the __hash__ half of a
    # deliberate eq/hash test (attrs test_dunders). No C7.
    codes = scan_source(tmp_path, """
        def test_hash():
            i = C(1)
            assert i == i
            assert hash(i) == hash(i)
    """)
    assert "C7" not in codes


def test_lone_cached_property_identity_still_flags(tmp_path):
    # `x.attr is x.attr` with NO discriminating counterpart stays C7: statically
    # indistinguishable from a typo tautology like scrapy's
    # `r2.errback is r2.errback`. The semantic pass / an inline-ignore adjudicates.
    codes = scan_source(tmp_path, """
        def test_x():
            assert obj.attr is obj.attr
    """)
    assert "C7" in codes


def test_flags_swallowing_try(tmp_path):
    assert "C3" in scan_source(tmp_path, """
        def test_x():
            try:
                assert resp() == 1
            except Exception:
                pass
    """)


def test_specific_non_assertion_except_is_not_c3(tmp_path):
    # `except TestingException` does NOT catch AssertionError, so an assert in the
    # try is not silenced - AssertionError propagates and still fails the test
    # (pydantic test_generics). No C3, even though the name ends in "Exception".
    codes = scan_source(tmp_path, """
        def test_x():
            try:
                assert recursively_defined_type_refs()
                raise TestingException
            except TestingException:
                pass
    """)
    assert "C3" not in codes


def test_except_assertion_error_still_flags_c3(tmp_path):
    # `except AssertionError` explicitly swallows the assert. Still C3.
    assert "C3" in scan_source(tmp_path, """
        def test_x():
            try:
                assert resp() == 1
            except AssertionError:
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


def test_exact_zero_and_one_floats_are_not_c8(tmp_path):
    # 0.0 and 1.0 are exact all/none ratio sentinels, not the rounding smell
    codes = scan_source(tmp_path, """
        def test_x():
            assert ratio() == 1.0
            assert ratio() == 0.0
    """)
    assert "C8" not in codes


def test_fractional_float_is_still_c8(tmp_path):
    assert "C8" in scan_source(tmp_path, """
        def test_x():
            assert ratio() == 0.1
    """)


# --- C8b: approximate equality with no explicit tolerance -------------------

def test_c8b_assert_almost_equal_without_tolerance(tmp_path):
    assert "C8b" in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_x(self):
                self.assertAlmostEqual(total(), 4.2)
    """)


def test_no_c8b_assert_almost_equal_with_places(tmp_path):
    assert "C8b" not in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_x(self):
                self.assertAlmostEqual(total(), 4.2, places=2)
    """)


def test_no_c8b_assert_almost_equal_with_positional_places(tmp_path):
    # places is the 3rd positional arg; supplying it sizes the tolerance.
    assert "C8b" not in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_x(self):
                self.assertAlmostEqual(total(), 4.2, 2)
    """)


def test_c8b_eq_pytest_approx_without_tolerance(tmp_path):
    assert "C8b" in scan_source(tmp_path, """
        def test_x():
            assert total() == pytest.approx(4.2)
    """)


def test_no_c8b_pytest_approx_with_rel(tmp_path):
    assert "C8b" not in scan_source(tmp_path, """
        def test_x():
            assert total() == pytest.approx(4.2, rel=1e-3)
    """)


# --- C18: comparing str()/repr()/f-string to a literal (Sensitive Equality) -

def test_flags_str_of_value_vs_literal(tmp_path):
    assert "C18" in scan_source(tmp_path, """
        def test_x():
            assert str(amount) == "5.00"
    """)


def test_flags_repr_vs_literal(tmp_path):
    assert "C18" in scan_source(tmp_path, """
        def test_x():
            assert repr(node) == "Node(1)"
    """)


def test_flags_fstring_vs_literal(tmp_path):
    assert "C18" in scan_source(tmp_path, """
        def test_x():
            assert f"{user.id}" == "42"
    """)


def test_real_string_field_equality_is_not_c18(tmp_path):
    # comparing an actual string attribute to a literal is a real value check
    codes = scan_source(tmp_path, """
        def test_x():
            assert user.name == "ada"
    """)
    assert "C18" not in codes


def test_numeric_equality_is_not_c18(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x():
            assert compute() == 5
    """)
    assert "C18" not in codes


# --- C19: pytest.raises wraps more than one call ----------------------------

def test_flags_raises_wrapping_two_statements(tmp_path):
    assert "C19" in scan_source(tmp_path, """
        import pytest
        def test_x():
            with pytest.raises(ValueError):
                obj = build()
                obj.boom()
    """)


def test_raises_wrapping_single_call_is_clean(tmp_path):
    codes = scan_source(tmp_path, """
        import pytest
        def test_x():
            with pytest.raises(ValueError, match="bad"):
                boom()
    """)
    assert "C19" not in codes


# --- #20: layer detection (metadata only, no behavior change) ---------------

def _findings(tmp_path, code):
    f = tmp_path / "test_sample.py"
    f.write_text(textwrap.dedent(code), encoding="utf-8")
    return run([str(f)])


def test_layer_logic_by_default(tmp_path):
    fs = _findings(tmp_path, """
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "logic" for a in fs)


def test_layer_web_from_web_import(tmp_path):
    fs = _findings(tmp_path, """
        import fastapi
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "web" for a in fs)


def test_layer_browser_from_playwright_import(tmp_path):
    fs = _findings(tmp_path, """
        from playwright.sync_api import Page
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "browser" for a in fs)


def test_layer_in_json_and_sarif(tmp_path):
    f = _write(tmp_path / "test_h.py", "import httpx\ndef test_x():\n    assert True\n")
    findings = run([f])
    assert json.loads(render_json(findings))[0]["layer"] == "web"
    doc = json.loads(render_sarif(findings))
    assert "layer:web" in doc["runs"][0]["results"][0]["properties"]["tags"]


# --- #20: layer-aware softening of C6/C14 on web/UI tests --------------------

def test_c6_softened_for_http_request_in_web_ctx(tmp_path):
    # `assert client.get(...)` in a web test: the response presence is the check,
    # not a weak truthiness. No C6.
    codes = scan_source(tmp_path, """
        import fastapi
        def test_endpoint(client):
            assert client.get("/health")
    """)
    assert "C6" not in codes


def test_c6_softened_for_locator_visibility_in_browser_ctx(tmp_path):
    codes = scan_source(tmp_path, """
        from playwright.sync_api import Page
        def test_ui(page):
            assert page.locator("#ok").is_visible()
    """)
    assert "C6" not in codes


def test_c6_still_fires_for_plain_truthiness_in_web_file(tmp_path):
    # a non-web operand in a web file is still a weak truthiness check: softening
    # is scoped to web presence operands, it does not blanket-exempt the layer.
    codes = scan_source(tmp_path, """
        import fastapi
        def test_x():
            assert some_value
    """)
    assert "C6" in codes


def test_c6_still_fires_in_logic_layer(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x():
            assert get_response()
    """)
    assert "C6" in codes


def test_c14_suppressed_in_browser_ctx(tmp_path):
    codes = scan_source(tmp_path, """
        from playwright.sync_api import Page
        def test_snapshot(page):
            if not snap.exists():
                snap.write_bytes(page.screenshot())
    """)
    assert "C14" not in codes


def test_c14_still_fires_in_logic_layer(tmp_path):
    codes = scan_source(tmp_path, """
        def test_golden():
            if not golden.exists():
                golden.write_text(render_output())
    """)
    assert "C14" in codes


def test_layer_softening_does_not_touch_c7_or_c5(tmp_path):
    # the guardrail: layer only softens C6/C14. Vacuity codes are language- and
    # layer-agnostic and must still fire in a web file.
    codes = scan_source(tmp_path, """
        import fastapi
        def test_x(client):
            assert True
            assert obj == obj
    """)
    assert "C5" in codes
    assert "C7" in codes


# --- #14 off-by-default infra + #13 C22 async never-awaits (The Liar) --------

_C22_BAD = """
    async def test_x():
        result = fetch()
        assert result == 1
"""


def test_c22_is_off_by_default(tmp_path):
    # the async-liar code does not fire unless explicitly enabled (run() filters
    # off-by-default codes via effective_conf; analyze_file alone does not)
    f = _write(tmp_path / "test_x.py", textwrap.dedent(_C22_BAD))
    assert "C22" not in {a.code for a in run([f])}


def test_c22_fires_when_enabled_via_config(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC22 = "low"\n')
    f = _write(tmp_path / "test_x.py", textwrap.dedent(_C22_BAD))
    codes = {a.code for a in run([f], config_path=cfg)}
    assert "C22" in codes


def test_c22_clean_when_it_awaits(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC22 = "low"\n')
    f = _write(tmp_path / "test_x.py", textwrap.dedent("""
        async def test_x():
            result = await fetch()
            assert result == 1
    """))
    assert "C22" not in {a.code for a in run([f], config_path=cfg)}


def test_c22_clean_for_sync_test(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC22 = "low"\n')
    f = _write(tmp_path / "test_x.py", textwrap.dedent("""
        def test_x():
            assert fetch() == 1
    """))
    assert "C22" not in {a.code for a in run([f], config_path=cfg)}


def test_c22_clean_when_it_drives_the_loop(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC22 = "low"\n')
    f = _write(tmp_path / "test_x.py", textwrap.dedent("""
        async def test_x():
            results = asyncio.run(gather())
            assert results == [1, 2]
    """))
    assert "C22" not in {a.code for a in run([f], config_path=cfg)}


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


def test_nested_forgotten_test_still_flags(tmp_path):
    # a no-arg, undecorated nested def named test* that is NEVER referenced is a
    # genuinely uncollected, never-run test (someone indented it by accident):
    # pytest skips it and nothing calls it, so C4 must still fire.
    assert "C4" in scan_source(tmp_path, """
        def test_outer():
            assert setup() == 1
            def test_inner():
                assert compute() == 2
    """)


def test_nested_referenced_helper_coroutine_is_not_uncollected_test(tmp_path):
    # a nested test*-named coroutine that the outer test awaits/schedules DOES
    # run (aiohttp test_run_app: `asyncio.create_task(test())`). Not forgotten.
    codes = scan_source(tmp_path, """
        async def test_shutdown():
            async def test():
                assert finished is False
            t = asyncio.create_task(test())
            await t
    """)
    assert "C4" not in codes


def test_nested_route_handler_is_not_uncollected_test(tmp_path):
    # @app.get/@app.post route handler named test* (fastapi, sanic). Not C4.
    codes = scan_source(tmp_path, """
        def test_multiple_path():
            app = FastAPI()

            @app.get("/test1")
            async def test(var=None):
                return {"foo": var}

            client = TestClient(app)
            assert client.get("/test1").status_code == 200
    """)
    assert "C4" not in codes


def test_nested_werkzeug_application_handler_is_not_uncollected_test(tmp_path):
    # @Request.application WSGI app named test_app (werkzeug). Not C4.
    codes = scan_source(tmp_path, """
        def test_multiple_cookies():
            @Request.application
            def test_app(request):
                return Response("ok")

            client = Client(test_app)
            assert client.get("/").text == "[]"
    """)
    assert "C4" not in codes


def test_nested_local_callback_handler_is_not_uncollected_test(tmp_path):
    # undecorated local callback that takes a `request` arg (aiohttp/sanic
    # style) named test*: a handler passed to the framework, not a test. No C4.
    codes = scan_source(tmp_path, """
        async def test_json():
            async def test_handler(request):
                return web.Response()
            app.router.add_post("/", test_handler)
            assert (await client.post("/")).status == 200
    """)
    assert "C4" not in codes


def test_top_level_route_handler_is_not_uncollected_test(tmp_path):
    # a top-level route handler decorated with @app.get that happens to assert
    # in its body (fastapi test_dependency_contextvars get_user). Not C4.
    codes = scan_source(tmp_path, """
        @app.get("/user")
        def get_user():
            request_state = ctx.get()
            assert request_state
            return request_state
    """)
    assert "C4" not in codes


def test_forgotten_test_not_excused_by_unrelated_samename_local(tmp_path):
    # a top-level forgotten test stays flagged even if a DIFFERENT function
    # rebinds the same name locally and Loads it - that reference does not run
    # the forgotten test, so the module-level check must ignore it.
    codes = scan_source(tmp_path, """
        def verifica_total():
            assert soma() == 3

        def unrelated():
            verifica_total = 5
            print(verifica_total)
    """)
    assert "C4" in codes


def test_top_level_entry_point_called_is_not_uncollected_test(tmp_path):
    # a standalone-script entry point `main` that is invoked (asyncio.run(main()))
    # runs - it is not a forgotten pytest test (aiohttp tests/isolated/check_*.py).
    codes = scan_source(tmp_path, """
        async def main():
            assert leak_detected() is False

        asyncio.run(main())
    """)
    assert "C4" not in codes


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


def test_only_conditional_asserts_is_c21_not_c1(tmp_path):
    # every assert is conditional and none runs unconditionally: C21 owns it,
    # and the per-assert C1 is suppressed to avoid double-reporting one smell.
    codes = scan_source(tmp_path, """
        def test_x(results):
            for meta in results["rows"]:
                assert meta["id"] == 1
    """)
    assert "C21" in codes
    assert "C1" not in codes


def test_single_conditional_assert_is_c21_not_c1(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x(cond):
            if cond:
                assert compute() == 1
    """)
    assert "C21" in codes
    assert "C1" not in codes


def test_c1_still_fires_when_an_unconditional_assert_exists(tmp_path):
    # a top-level assert means the test is not vacuous (not C21), but the extra
    # conditional assert can still be skipped, so C1 still applies.
    codes = scan_source(tmp_path, """
        def test_x(results):
            assert results is not None
            for meta in results["rows"]:
                assert meta["id"] == 1
    """)
    assert "C21" not in codes
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


# --- FG-CONFIG-1: config file + effective_conf resolver --------------------

def _write(p, text):
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(p)


def test_load_config_reads_pyproject_tool_table(tmp_path):
    _write(tmp_path / "pyproject.toml", """
        [tool.falsegreen]
        disable = ["C6"]
        exclude = ["legacy/*"]
        [tool.falsegreen.severity]
        C8 = "high"
    """)
    conf = load_config(start=str(tmp_path))
    assert conf["disable"] == {"C6"}
    assert conf["exclude"] == ["legacy/*"]
    assert conf["severity"] == {"C8": "high"}


def test_falsegreen_toml_takes_precedence_over_pyproject(tmp_path):
    _write(tmp_path / "pyproject.toml", '[tool.falsegreen]\ndisable = ["C6"]\n')
    _write(tmp_path / ".falsegreen.toml", 'disable = ["C8"]\n')
    conf = load_config(start=str(tmp_path))
    assert conf["disable"] == {"C8"}  # .falsegreen.toml wins


def test_no_config_is_a_noop(tmp_path):
    conf = load_config(start=str(tmp_path))
    assert conf["disable"] == set()
    assert conf["exclude"] == []
    assert conf["severity"] == {}
    assert conf["long_test_threshold"] == 50


def test_invalid_severity_is_ignored_not_fatal(tmp_path):
    _write(tmp_path / ".falsegreen.toml", '[severity]\nC8 = "bogus"\n')
    conf = load_config(start=str(tmp_path))
    assert conf["severity"] == {}  # invalid value dropped, no crash


def test_config_disable_suppresses_code(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", 'disable = ["C8"]\n')
    f = _write(tmp_path / "test_d.py", "def test_x():\n    assert total() == 0.3\n")
    codes = {a.code for a in run([f], config_path=cfg)}
    assert "C8" not in codes


def test_config_severity_promotes_low_to_high(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC8 = "high"\n')
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert total() == 0.3\n")
    assert main([f, "--config", cfg]) == 20  # C8 is normally low (exit 10)


def test_config_severity_off_suppresses(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC8 = "off"\n')
    f = _write(tmp_path / "test_o.py", "def test_x():\n    assert total() == 0.3\n")
    assert main([f, "--config", cfg]) == 0  # the only finding is turned off


def test_config_exclude_glob_skips_file(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", 'exclude = ["test_skip_me.py"]\n')
    _write(tmp_path / "test_skip_me.py", "def test_x():\n    assert True\n")
    _write(tmp_path / "test_keep.py", "def test_y():\n    assert True\n")
    files = {a.file for a in run([str(tmp_path)], config_path=cfg)}
    assert not any(f.endswith("test_skip_me.py") for f in files)
    assert any(f.endswith("test_keep.py") for f in files)


def test_cli_disable_overrides_config_severity(tmp_path):
    # precedence: CLI --disable wins over a config that promotes the code
    conf = {"disable": set(), "exclude": [], "severity": {"C8": "high"}}
    assert effective_conf("C8", conf, cli_disable={"C8"}) == "off"
    assert effective_conf("C8", conf, cli_disable=set()) == "high"
    assert effective_conf("C8", None, None) == "low"  # catalog default


# --- FG-FORMAT-1: text/json/sarif/junit + summary + output ----------------

def _mixed_findings(tmp_path):
    # one HIGH (C5 assert True) and one LOW (C8 float equality)
    f = _write(tmp_path / "test_mixed.py", """
        def test_high():
            assert True
        def test_low():
            assert total() == 0.3
    """)
    return f, run([f])


def test_sarif_is_valid_and_maps_severity(tmp_path):
    _f, findings = _mixed_findings(tmp_path)
    doc = json.loads(render_sarif(findings))
    assert doc["version"] == "2.1.0"
    run0 = doc["runs"][0]
    assert run0["tool"]["driver"]["name"] == "falsegreen"
    rule_ids = {r["id"] for r in run0["tool"]["driver"]["rules"]}
    assert {"C5", "C8"} <= rule_ids
    levels = {r["ruleId"]: r["level"] for r in run0["results"]}
    assert levels["C5"] == "error"     # HIGH -> error
    assert levels["C8"] == "warning"   # LOW -> warning


def test_sarif_uris_are_forward_slash_relative(tmp_path):
    _f, findings = _mixed_findings(tmp_path)
    doc = json.loads(render_sarif(findings))
    results = doc["runs"][0]["results"]
    assert len(results) == 2  # the C5 (high) and C8 (low) findings; guards the loop below
    locs = [r["locations"][0]["physicalLocation"] for r in results]
    assert all("\\" not in p["artifactLocation"]["uri"] for p in locs)
    assert all(p["region"]["startLine"] >= 1 for p in locs)


def test_sarif_level_follows_severity_override(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC8 = "high"\n')
    f = _write(tmp_path / "test_o.py", "def test_x():\n    assert total() == 0.3\n")
    findings = run([f], config_path=cfg)
    doc = json.loads(render_sarif(findings))
    levels = {r["ruleId"]: r["level"] for r in doc["runs"][0]["results"]}
    assert levels["C8"] == "error"  # promoted via config, SARIF reflects it


def test_sarif_rules_carry_distinct_helpuri_and_fulldescription(tmp_path):
    _f, findings = _mixed_findings(tmp_path)
    doc = json.loads(render_sarif(findings))
    assert doc["version"] == "2.1.0"
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    uris = [r["helpUri"] for r in rules]
    assert len(set(uris)) == len(uris)  # one distinct deep link per rule id
    assert all(u.startswith("https://vinicq.github.io/falsegreen-docs/catalog/python/#")
               for u in uris)
    by_id = {r["id"]: r for r in rules}
    assert by_id["C5"]["helpUri"].endswith("#c5")  # deterministic per rule id
    assert all(r["fullDescription"]["text"] for r in rules)  # non-empty


def test_junit_is_valid_xml_with_counts(tmp_path):
    _f, findings = _mixed_findings(tmp_path)
    root = ET.fromstring(render_junit(findings))
    suite = root.find("testsuite")
    assert suite.get("failures") == "1"   # the HIGH
    assert suite.get("skipped") == "1"    # the LOW
    kinds = {tc.find("failure") is not None for tc in suite.findall("testcase")}
    assert True in kinds  # at least one <failure>


def test_json_flag_is_alias_for_format_json(tmp_path, capsys):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    main([f, "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed[0]["code"] == "C5"
    assert parsed[0]["confidence"] == "high"


def test_output_flag_writes_file(tmp_path):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    out = tmp_path / "report.sarif"
    main([f, "--format", "sarif", "--output", str(out)])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"


def test_summary_goes_to_stderr(tmp_path, capsys):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    main([f, "--summary"])
    err = capsys.readouterr().err
    assert "scanned" in err and "finding(s)" in err and "C5:1" in err


def test_summary_line_counts(tmp_path):
    _f, findings = _mixed_findings(tmp_path)
    line = summary_line(findings, n_files=1)
    assert "1 high, 1 low" in line
    assert "scanned 1 test file(s)" in line


# --- FG-BASELINE-1: ratchet mode, content fingerprints ---------------------

def test_write_baseline_records_all_and_exits_zero(tmp_path):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    bl = tmp_path / "base.json"
    assert main([f, "--write-baseline", str(bl)]) == 0
    fps = load_baseline(str(bl))
    assert len(fps) == 1  # the C5 finding


def test_baseline_suppresses_known_and_keeps_clean_exit(tmp_path):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    bl = str(tmp_path / "base.json")
    main([f, "--write-baseline", bl])
    # with the finding baselined, the only finding is suppressed -> exit 0
    assert main([f, "--baseline", bl]) == 0
    assert run([f], baseline=load_baseline(bl)) == []


def test_baseline_still_fails_on_a_new_finding(tmp_path):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    bl = str(tmp_path / "base.json")
    main([f, "--write-baseline", bl])
    # add a NEW high finding; it is not in the baseline, so the scan fails
    _write(tmp_path / "test_h.py",
           "def test_x():\n    assert True\ndef test_y():\n    assert (1, 'm')\n")
    assert main([f, "--baseline", bl]) == 20


def test_fingerprint_survives_prepended_blank_lines(tmp_path):
    # the key regression: a finding shifted down by unrelated edits stays suppressed
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    bl = str(tmp_path / "base.json")
    main([f, "--write-baseline", bl])
    base_fps = load_baseline(bl)
    # prepend blank lines + a comment: the assert moves from line 2 to line 5
    _write(tmp_path / "test_h.py",
           "\n\n# unrelated\ndef test_x():\n    assert True\n")
    after = run([f])
    assert len(after) == 1
    assert after[0].line == 5                       # line moved
    assert fingerprint(after[0]) in base_fps        # fingerprint did not
    assert run([f], baseline=base_fps) == []        # still suppressed


def test_fingerprint_differs_by_code_and_snippet(tmp_path):
    f1 = _write(tmp_path / "test_a.py", "def test_x():\n    assert True\n")
    f2 = _write(tmp_path / "test_b.py", "def test_y():\n    assert d == d\n")
    a = run([f1])[0]
    b = run([f2])[0]
    assert fingerprint(a) != fingerprint(b)  # different file/code/snippet


# --- C20: assertion in dead code after a terminator (Fully Rotten Green) ----

def test_flags_assert_after_return(tmp_path):
    assert "C20" in scan_source(tmp_path, """
        def test_x():
            assert setup() == 1
            return
            assert teardown() == 0
    """)


def test_flags_assert_after_raise(tmp_path):
    assert "C20" in scan_source(tmp_path, """
        def test_x():
            do()
            raise SystemExit
            assert result() == 1
    """)


def test_flags_check_after_unreachable_fail(tmp_path):
    # a check stranded after pytest.fail()/assert False also never runs
    assert "C20" in scan_source(tmp_path, """
        def test_x():
            pytest.fail("nope")
            assert result() == 1
    """)


def test_assert_before_return_is_clean(tmp_path):
    assert "C20" not in scan_source(tmp_path, """
        def test_x():
            assert compute() == 1
            return
    """)


def test_return_inside_if_does_not_orphan_later_assert(tmp_path):
    # early-return guard: the assert after the if is still reachable
    assert "C20" not in scan_source(tmp_path, """
        def test_x():
            if skip_condition():
                return
            assert compute() == 1
    """)


def test_return_in_nested_branch_leaves_trailing_assert_reachable(tmp_path):
    assert "C20" not in scan_source(tmp_path, """
        def test_x():
            for x in items():
                if x:
                    return
            assert done() == 1
    """)


# --- FG-CONFIG-1/#19: every CASES entry carries a valid judgment ------------

def test_every_case_has_a_known_judgment(tmp_path):
    from falsegreen.scanner import CASES, JUDGMENTS
    entries = list(CASES.values())
    assert all(len(e) == 3 for e in entries)            # (title, confidence, judgment)
    assert all(e[1] in ("high", "low", "info", "off") for e in entries)
    assert all(e[2] in JUDGMENTS for e in entries)


def test_sarif_carries_the_judgment_tag(tmp_path):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    findings = run([f])
    doc = json.loads(render_sarif(findings))
    rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["properties"]["tags"] == ["J2"]  # C5 is J2 (rule tag = judgment only)
    result = doc["runs"][0]["results"][0]
    assert "J2" in result["properties"]["tags"]  # result tags also carry layer:*


def test_summary_has_by_judgment_line(tmp_path, capsys):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    main([f, "--summary"])
    err = capsys.readouterr().err
    assert "by judgment:" in err and "J2:1" in err


# --- C21: every assertion conditional (Context-Dependent Rotten Green) -------

def test_flags_only_branch_asserts(tmp_path):
    # the if asserts, the else only logs: a false cond means nothing is checked
    assert "C21" in scan_source(tmp_path, """
        def test_x(cond):
            if cond:
                assert a() == 1
            else:
                log("skip")
    """)


def test_top_level_assert_is_not_c21(tmp_path):
    codes = scan_source(tmp_path, """
        def test_x(cond):
            if cond:
                assert a() == 1
            assert b() == 2
    """)
    assert "C21" not in codes


def test_exhaustive_if_else_both_assert_is_not_c21(tmp_path):
    # every path asserts something, so a check always runs: not vacuous
    codes = scan_source(tmp_path, """
        def test_x(cond):
            if cond:
                assert a() == 1
            else:
                assert a() == 2
    """)
    assert "C21" not in codes


def test_with_raises_is_an_unconditional_check_not_c21(tmp_path):
    codes = scan_source(tmp_path, """
        import pytest
        def test_x():
            with pytest.raises(ValueError, match="bad"):
                boom()
    """)
    assert "C21" not in codes


def test_loop_over_literal_asserts_is_not_c21(tmp_path):
    # for over a non-empty literal always runs, so the assert is unconditional
    codes = scan_source(tmp_path, """
        def test_x(sm):
            for q in (sm.a, sm.b):
                assert q.maxsize == 200
    """)
    assert "C21" not in codes


# --- sure / expects / ward: fluent assertion libraries --------------------

def test_sure_should_is_not_c2(tmp_path):
    # sure library: result.should.equal(y) is a real assertion
    out = scan_source(tmp_path, """
        def test_x():
            result.should.equal(42)
    """)
    assert "C2" not in out
    assert "C2b" not in out


def test_expects_to_is_not_c2(tmp_path):
    # ward / expects: expect(x).to(equal(y)) is a real assertion
    out = scan_source(tmp_path, """
        def test_x():
            expect(result).to(equal(42))
    """)
    assert "C2" not in out
    assert "C2b" not in out


# --- HTTP mock libraries recognized as web layer --------------------------

def test_responses_import_is_web_layer(tmp_path):
    fs = _findings(tmp_path, """
        import responses
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "web" for a in fs)


def test_httpretty_import_is_web_layer(tmp_path):
    fs = _findings(tmp_path, """
        import httpretty
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "web" for a in fs)


def test_respx_import_is_web_layer(tmp_path):
    fs = _findings(tmp_path, """
        import respx
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "web" for a in fs)


def test_http_mock_softens_c6(tmp_path):
    # a web mock library signals the test targets HTTP interactions: response
    # presence checks are not weak truthiness in this context.
    codes = scan_source(tmp_path, """
        import responses
        def test_endpoint():
            assert client.get("/health")
    """)
    assert "C6" not in codes


# --- new browser libraries recognized ------------------------------------

def test_helium_import_is_browser_layer(tmp_path):
    fs = _findings(tmp_path, """
        import helium
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "browser" for a in fs)


def test_pyppeteer_import_is_browser_layer(tmp_path):
    fs = _findings(tmp_path, """
        import pyppeteer
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "browser" for a in fs)


def test_seleniumbase_import_is_browser_layer(tmp_path):
    fs = _findings(tmp_path, """
        import seleniumbase
        def test_x():
            assert True
    """)
    assert fs and all(a.layer == "browser" for a in fs)


# --- C16: time-control libraries suppress clock-read findings -------------

def test_c16_fires_for_raw_datetime_now(tmp_path):
    codes = scan_source(tmp_path, """
        import datetime
        def test_x():
            assert datetime.datetime.now().hour == 12
    """)
    assert "C16" in codes


def test_c16_suppressed_when_freezegun_imported(tmp_path):
    # freezegun controls the clock: datetime.now() is no longer non-deterministic
    codes = scan_source(tmp_path, """
        import datetime
        from freezegun import freeze_time
        def test_x():
            assert datetime.datetime.now().hour == 12
    """)
    assert "C16" not in codes


def test_c16_suppressed_when_time_machine_imported(tmp_path):
    # time_machine controls the clock: same exemption applies
    codes = scan_source(tmp_path, """
        import datetime
        import time_machine
        def test_x():
            assert datetime.datetime.now().hour == 12
    """)
    assert "C16" not in codes


# --- C22: trio.run drives the loop (not an async liar) -------------------

def test_c22_clean_when_trio_drives_the_loop(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nC22 = "low"\n')
    f = _write(tmp_path / "test_x.py", textwrap.dedent("""
        async def test_x():
            results = trio.run(gather)
            assert results == [1, 2]
    """))
    assert "C22" not in {a.code for a in run([f], config_path=cfg)}


# --- C23: opens a real file at a literal path (mystery guest) ----------------

def test_c23_flags_open_with_literal_path(tmp_path):
    assert "C23" in scan_source(tmp_path, """
        def test_loads_config():
            with open("tests/fixtures/config.json") as f:
                data = json.load(f)
            assert data["key"] == "value"
    """)


def test_c23_flags_open_without_with(tmp_path):
    assert "C23" in scan_source(tmp_path, """
        def test_reads_file():
            f = open("expected.txt")
            content = f.read()
            assert content == "ok"
    """)


def test_c23_flags_pathlib_read_text_with_literal(tmp_path):
    assert "C23" in scan_source(tmp_path, """
        from pathlib import Path
        def test_config():
            content = Path("config/settings.toml").read_text()
            assert "timeout" in content
    """)


def test_c23_flags_pathlib_read_bytes_with_literal(tmp_path):
    assert "C23" in scan_source(tmp_path, """
        from pathlib import Path
        def test_binary():
            data = Path("tests/data/image.png").read_bytes()
            assert data[:4] == b"\\x89PNG"
    """)


def test_c23_clean_when_open_receives_variable(tmp_path):
    codes = scan_source(tmp_path, """
        def test_file(data_file):
            with open(data_file) as f:
                content = f.read()
            assert content
    """)
    assert "C23" not in codes


def test_c23_clean_when_open_receives_tmp_path(tmp_path):
    codes = scan_source(tmp_path, """
        def test_writes(tmp_path):
            p = tmp_path / "out.txt"
            p.write_text("hello")
            with open(p) as f:
                assert f.read() == "hello"
    """)
    assert "C23" not in codes


def test_c23_clean_when_pathlib_receives_fixture(tmp_path):
    codes = scan_source(tmp_path, """
        from pathlib import Path
        def test_roundtrip(tmp_path):
            content = (tmp_path / "data.txt").read_text()
            assert content == "expected"
    """)
    assert "C23" not in codes


def test_c23_clean_when_open_receives_attribute(tmp_path):
    codes = scan_source(tmp_path, """
        def test_file(self):
            with open(self.fixture_path) as f:
                data = f.read()
            assert data
    """)
    assert "C23" not in codes


# --- D1: assertion roulette (diagnostic group, off by default) ---------------

def test_d1_flags_multiple_asserts_without_messages(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_d1.py", textwrap.dedent("""
        def test_order():
            assert subtotal() == 30
            assert discount() == 3
            assert total() == 27
    """))
    codes = {a.code for a in run([f], config_path=cfg)}
    assert "D1" in codes


def test_d1_is_off_by_default(tmp_path):
    f = _write(tmp_path / "test_d1.py", textwrap.dedent("""
        def test_order():
            assert subtotal() == 30
            assert discount() == 3
    """))
    assert "D1" not in {a.code for a in run([f])}


def test_d1_clean_for_single_assert(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_d1.py", textwrap.dedent("""
        def test_x():
            assert compute() == 42
    """))
    assert "D1" not in {a.code for a in run([f], config_path=cfg)}


def test_d1_clean_when_all_have_messages(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_d1.py", textwrap.dedent("""
        def test_order():
            assert subtotal() == 30, "subtotal wrong"
            assert total() == 27, "total wrong"
    """))
    assert "D1" not in {a.code for a in run([f], config_path=cfg)}


def test_d1_clean_when_at_least_one_has_message(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_d1.py", textwrap.dedent("""
        def test_order():
            assert subtotal() == 30
            assert total() == 27, "total wrong"
    """))
    assert "D1" not in {a.code for a in run([f], config_path=cfg)}


def test_d1_info_does_not_affect_exit_code(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_d1.py", textwrap.dedent("""
        def test_order():
            assert subtotal() == 30
            assert total() == 27
    """))
    findings = run([f], config_path=cfg)
    assert any(a.code == "D1" for a in findings)
    assert not any(a.conf in ("high", "low") for a in findings)
    assert main([f, "--config", cfg]) == 0


# --- D3: duplicate assert (diagnostic group, off by default) -----------------

def test_d3_flags_repeated_assertion(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD3 = "info"\n')
    f = _write(tmp_path / "test_d3.py", textwrap.dedent("""
        def test_user():
            user = create_user("alice")
            assert user.email == "alice@example.com"
            assert user.is_active is True
            assert user.email == "alice@example.com"
    """))
    codes = {a.code for a in run([f], config_path=cfg)}
    assert "D3" in codes


def test_d3_is_off_by_default(tmp_path):
    f = _write(tmp_path / "test_d3.py", textwrap.dedent("""
        def test_user():
            assert compute() == 1
            assert compute() == 1
    """))
    assert "D3" not in {a.code for a in run([f])}


def test_d3_clean_for_distinct_assertions(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD3 = "info"\n')
    f = _write(tmp_path / "test_d3.py", textwrap.dedent("""
        def test_x():
            assert a() == 1
            assert b() == 2
    """))
    assert "D3" not in {a.code for a in run([f], config_path=cfg)}


# --- M2: long test method (coupling group, off by default) -------------------

def test_m2_flags_long_test(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml",
                 'long_test_threshold = 5\n[severity]\nM2 = "info"\n')
    body = "\n".join("    x_%d = %d" % (i, i) for i in range(6))
    f = _write(tmp_path / "test_m2.py",
               "def test_long():\n" + body + "\n    assert x_0 == 0\n")
    codes = {a.code for a in run([f], config_path=cfg)}
    assert "M2" in codes


def test_m2_clean_within_threshold(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml",
                 'long_test_threshold = 50\n[severity]\nM2 = "info"\n')
    f = _write(tmp_path / "test_m2.py", textwrap.dedent("""
        def test_short():
            result = compute()
            assert result == 42
    """))
    assert "M2" not in {a.code for a in run([f], config_path=cfg)}


def test_m2_is_off_by_default(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", 'long_test_threshold = 1\n')
    f = _write(tmp_path / "test_m2.py", textwrap.dedent("""
        def test_x():
            assert a() == 1
            assert b() == 2
            assert c() == 3
    """))
    assert "M2" not in {a.code for a in run([f], config_path=cfg)}


def test_m2_info_does_not_affect_exit_code(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml",
                 'long_test_threshold = 2\n[severity]\nM2 = "info"\n')
    body = "\n".join("    x_%d = %d" % (i, i) for i in range(3))
    f = _write(tmp_path / "test_m2.py",
               "def test_long():\n" + body + "\n    assert x_0 == 0\n")
    assert main([f, "--config", cfg]) == 0


# --- render_text: info section appears only when enabled ---------------------

def test_render_text_shows_diagnostic_section(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_rt.py", textwrap.dedent("""
        def test_x():
            assert a() == 1
            assert b() == 2
    """))
    findings = run([f], config_path=cfg)
    from falsegreen.scanner import render_text
    out = render_text(findings)
    assert "DIAGNOSTIC" in out
    assert "D1" in out


def test_render_text_no_diagnostic_section_when_disabled(tmp_path):
    f = _write(tmp_path / "test_rt.py", textwrap.dedent("""
        def test_x():
            assert a() == 1
            assert b() == 2
    """))
    findings = run([f])
    from falsegreen.scanner import render_text
    out = render_text(findings)
    assert "DIAGNOSTIC" not in out


def test_render_text_shows_coupling_section(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml",
                 'long_test_threshold = 2\n[severity]\nM2 = "info"\n')
    body = "\n".join("    x_%d = %d" % (i, i) for i in range(3))
    f = _write(tmp_path / "test_rt2.py",
               "def test_long():\n" + body + "\n    assert x_0 == 0\n")
    findings = run([f], config_path=cfg)
    from falsegreen.scanner import render_text
    out = render_text(findings)
    assert "COUPLING" in out
    assert "M2" in out


# --- D3: additional edge-case and exit-code tests ----------------------------

def test_d3_info_does_not_affect_exit_code(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD3 = "info"\n')
    f = _write(tmp_path / "test_d3b.py", textwrap.dedent("""
        def test_user():
            user = create_user("alice")
            assert user.email == "alice@example.com"
            assert user.email == "alice@example.com"
    """))
    assert main([f, "--config", cfg]) == 0


def test_d3_flags_at_line_of_duplicate(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD3 = "info"\n')
    f = _write(tmp_path / "test_d3c.py", textwrap.dedent("""
        def test_x():
            assert compute() == 1
            assert other() == 2
            assert compute() == 1
    """))
    findings = [a for a in run([f], config_path=cfg) if a.code == "D3"]
    assert len(findings) == 1
    assert findings[0].line == 5


# --- D1: nested functions must not be counted --------------------------------

def test_d1_nested_asserts_not_counted(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD1 = "info"\n')
    f = _write(tmp_path / "test_d1b.py", textwrap.dedent("""
        def test_outer():
            def helper():
                assert inner_a() == 1
                assert inner_b() == 2
            helper()
            assert outer_result() == 42
    """))
    # Only one assert at the outer level; D1 should not fire on test_outer.
    assert "D1" not in {a.code for a in run([f], config_path=cfg)}


# --- M2: invalid threshold falls back to default ----------------------------

def test_m2_invalid_threshold_uses_default(tmp_path, capsys):
    cfg = _write(tmp_path / ".falsegreen.toml",
                 'long_test_threshold = "not-a-number"\n[severity]\nM2 = "info"\n')
    # Default threshold is 50; a 3-line test should not fire.
    f = _write(tmp_path / "test_m2b.py", textwrap.dedent("""
        def test_short():
            x = compute()
            assert x == 42
    """))
    assert "M2" not in {a.code for a in run([f], config_path=cfg)}
    captured = capsys.readouterr()
    assert "long_test_threshold" in captured.err


# ---------------------------------------------------------------------------
# C25: xfail without strict=True
# ---------------------------------------------------------------------------

def test_c25_bare_xfail_fires(tmp_path):
    f = _write(tmp_path / "test_c25.py", textwrap.dedent("""
        @pytest.mark.xfail
        def test_broken():
            assert compute() == 42
    """))
    assert "C25" in {a.code for a in analyze_file(str(f))}


def test_c25_xfail_with_reason_fires(tmp_path):
    f = _write(tmp_path / "test_c25b.py", textwrap.dedent("""
        @pytest.mark.xfail(reason="known bug")
        def test_broken():
            assert compute() == 42
    """))
    assert "C25" in {a.code for a in analyze_file(str(f))}


def test_c25_xfail_strict_false_fires(tmp_path):
    f = _write(tmp_path / "test_c25c.py", textwrap.dedent("""
        @pytest.mark.xfail(strict=False)
        def test_broken():
            assert compute() == 42
    """))
    assert "C25" in {a.code for a in analyze_file(str(f))}


def test_c25_xfail_strict_true_clean(tmp_path):
    f = _write(tmp_path / "test_c25d.py", textwrap.dedent("""
        @pytest.mark.xfail(strict=True)
        def test_broken():
            assert compute() == 42
    """))
    assert "C25" not in {a.code for a in analyze_file(str(f))}


def test_c25_class_level_fires(tmp_path):
    f = _write(tmp_path / "test_c25e.py", textwrap.dedent("""
        @pytest.mark.xfail
        class TestBroken:
            def test_one(self):
                assert compute() == 42
    """))
    assert "C25" in {a.code for a in analyze_file(str(f))}


def test_c25_exit_code_is_10(tmp_path):
    f = _write(tmp_path / "test_c25f.py", textwrap.dedent("""
        @pytest.mark.xfail
        def test_broken():
            assert compute() == 42
    """))
    assert main([f]) == 10


# ---------------------------------------------------------------------------
# C27: try/except/pass antipattern
# ---------------------------------------------------------------------------

def test_c27_try_except_pass_fires(tmp_path):
    f = _write(tmp_path / "test_c27.py", textwrap.dedent("""
        def test_raises_value_error():
            try:
                risky()
            except ValueError:
                pass
    """))
    assert "C27" in {a.code for a in analyze_file(str(f))}


def test_c27_try_except_pass_is_high(tmp_path):
    f = _write(tmp_path / "test_c27b.py", textwrap.dedent("""
        def test_raises_value_error():
            try:
                risky()
            except ValueError:
                pass
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C27"]
    assert findings and findings[0].conf == "high"


def test_c27_assertion_after_try_clean(tmp_path):
    f = _write(tmp_path / "test_c27c.py", textwrap.dedent("""
        def test_no_raise_then_assert():
            try:
                result = risky()
            except ValueError:
                pass
            assert result == expected
    """))
    assert "C27" not in {a.code for a in analyze_file(str(f))}


def test_c27_assertion_in_try_body_clean(tmp_path):
    f = _write(tmp_path / "test_c27d.py", textwrap.dedent("""
        def test_x():
            try:
                result = compute()
                assert result == 42
            except ValueError:
                pass
    """))
    assert "C27" not in {a.code for a in analyze_file(str(f))}


def test_c27_bare_except_fires(tmp_path):
    f = _write(tmp_path / "test_c27e.py", textwrap.dedent("""
        def test_something():
            try:
                risky()
            except ValueError:
                pass
    """))
    assert "C27" in {a.code for a in analyze_file(str(f))}


# ---------------------------------------------------------------------------
# C28: pytest.raises binding declared but never read
# ---------------------------------------------------------------------------

def test_c28_excinfo_declared_not_used_fires(tmp_path):
    f = _write(tmp_path / "test_c28.py", textwrap.dedent("""
        def test_raises():
            with pytest.raises(ValueError) as exc_info:
                parse("bad")
    """))
    assert "C28" in {a.code for a in analyze_file(str(f))}


def test_c28_excinfo_used_in_assert_clean(tmp_path):
    f = _write(tmp_path / "test_c28b.py", textwrap.dedent("""
        def test_raises():
            with pytest.raises(ValueError) as exc_info:
                parse("bad")
            assert "invalid" in str(exc_info.value)
    """))
    assert "C28" not in {a.code for a in analyze_file(str(f))}


def test_c28_excinfo_used_via_match_clean(tmp_path):
    f = _write(tmp_path / "test_c28c.py", textwrap.dedent("""
        def test_raises():
            with pytest.raises(ValueError) as exc_info:
                parse("bad")
            exc_info.match(r"invalid")
    """))
    assert "C28" not in {a.code for a in analyze_file(str(f))}


def test_c28_no_binding_clean(tmp_path):
    f = _write(tmp_path / "test_c28d.py", textwrap.dedent("""
        def test_raises():
            with pytest.raises(ValueError):
                parse("bad")
    """))
    assert "C28" not in {a.code for a in analyze_file(str(f))}


# ---------------------------------------------------------------------------
# C29: os.environ direct assignment
# ---------------------------------------------------------------------------

def test_c29_direct_assignment_fires(tmp_path):
    f = _write(tmp_path / "test_c29.py", textwrap.dedent("""
        def test_config():
            os.environ["DB_URL"] = "sqlite:///:memory:"
            result = load_config()
            assert result.db_url == "sqlite:///:memory:"
    """))
    assert "C29" in {a.code for a in analyze_file(str(f))}


def test_c29_environ_update_fires(tmp_path):
    f = _write(tmp_path / "test_c29b.py", textwrap.dedent("""
        def test_config():
            os.environ.update({"DB_URL": "sqlite:///:memory:"})
            result = load_config()
            assert result.db_url == "sqlite:///:memory:"
    """))
    assert "C29" in {a.code for a in analyze_file(str(f))}


def test_c29_putenv_fires(tmp_path):
    f = _write(tmp_path / "test_c29c.py", textwrap.dedent("""
        def test_config():
            os.putenv("DB_URL", "sqlite:///:memory:")
            result = load_config()
            assert result.db_url == "sqlite:///:memory:"
    """))
    assert "C29" in {a.code for a in analyze_file(str(f))}


def test_c29_monkeypatch_clean(tmp_path):
    f = _write(tmp_path / "test_c29d.py", textwrap.dedent("""
        def test_config(monkeypatch):
            monkeypatch.setenv("DB_URL", "sqlite:///:memory:")
            result = load_config()
            assert result.db_url == "sqlite:///:memory:"
    """))
    assert "C29" not in {a.code for a in analyze_file(str(f))}


def test_c29_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c29e.py", textwrap.dedent("""
        def test_config():
            os.environ["KEY"] = "val"
            assert something() == expected
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C29"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# C30: responses.add() without @responses.activate
# ---------------------------------------------------------------------------

def test_c30_add_without_activate_fires(tmp_path):
    f = _write(tmp_path / "test_c30.py", textwrap.dedent("""
        def test_fetch_user():
            responses.add(responses.GET, "http://api.example.com/user", json={"id": 1})
            result = fetch_user(1)
            assert result["id"] == 1
    """))
    assert "C30" in {a.code for a in analyze_file(str(f))}


def test_c30_add_callback_without_activate_fires(tmp_path):
    f = _write(tmp_path / "test_c30b.py", textwrap.dedent("""
        def test_fetch():
            responses.add_callback(responses.GET, "http://api.example.com/", callback=handler)
            result = fetch()
            assert result is not None
    """))
    assert "C30" in {a.code for a in analyze_file(str(f))}


def test_c30_with_activate_decorator_clean(tmp_path):
    f = _write(tmp_path / "test_c30c.py", textwrap.dedent("""
        @responses.activate
        def test_fetch_user():
            responses.add(responses.GET, "http://api.example.com/user", json={"id": 1})
            result = fetch_user(1)
            assert result["id"] == 1
    """))
    assert "C30" not in {a.code for a in analyze_file(str(f))}


def test_c30_with_context_manager_clean(tmp_path):
    f = _write(tmp_path / "test_c30d.py", textwrap.dedent("""
        def test_fetch_user():
            with responses.RequestsMock() as rsps:
                rsps.add(rsps.GET, "http://api.example.com/user", json={"id": 1})
                result = fetch_user(1)
                assert result["id"] == 1
    """))
    assert "C30" not in {a.code for a in analyze_file(str(f))}


def test_c30_httpretty_without_activate_fires(tmp_path):
    f = _write(tmp_path / "test_c30e.py", textwrap.dedent("""
        def test_get():
            httpretty.register_uri(httpretty.GET, "http://example.com/api", body="ok")
            result = get_data()
            assert result == "ok"
    """))
    assert "C30" in {a.code for a in analyze_file(str(f))}


def test_c30_httpretty_with_activate_clean(tmp_path):
    f = _write(tmp_path / "test_c30f.py", textwrap.dedent("""
        @httpretty.activate
        def test_get():
            httpretty.register_uri(httpretty.GET, "http://example.com/api", body="ok")
            result = get_data()
            assert result == "ok"
    """))
    assert "C30" not in {a.code for a in analyze_file(str(f))}


def test_c30_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c30g.py", textwrap.dedent("""
        def test_fetch():
            responses.add(responses.GET, "http://api.example.com/", json={})
            result = fetch()
            assert result == {}
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C30"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# C31: capsys/capfd.readouterr() result never asserted
# ---------------------------------------------------------------------------

def test_c31_discarded_result_fires(tmp_path):
    f = _write(tmp_path / "test_c31a.py", textwrap.dedent("""
        def test_output(capsys):
            print("hello")
            capsys.readouterr()
    """))
    assert "C31" in {a.code for a in analyze_file(str(f))}


def test_c31_captured_not_asserted_fires(tmp_path):
    f = _write(tmp_path / "test_c31b.py", textwrap.dedent("""
        def test_output(capsys):
            print("hello")
            captured = capsys.readouterr()
    """))
    assert "C31" in {a.code for a in analyze_file(str(f))}


def test_c31_tuple_not_asserted_fires(tmp_path):
    f = _write(tmp_path / "test_c31c.py", textwrap.dedent("""
        def test_output(capsys):
            print("hello")
            out, err = capsys.readouterr()
    """))
    assert "C31" in {a.code for a in analyze_file(str(f))}


def test_c31_captured_asserted_clean(tmp_path):
    f = _write(tmp_path / "test_c31d.py", textwrap.dedent("""
        def test_output(capsys):
            print("hello")
            captured = capsys.readouterr()
            assert captured.out == "hello\\n"
    """))
    assert "C31" not in {a.code for a in analyze_file(str(f))}


def test_c31_tuple_asserted_clean(tmp_path):
    f = _write(tmp_path / "test_c31e.py", textwrap.dedent("""
        def test_output(capsys):
            print("hello")
            out, err = capsys.readouterr()
            assert out == "hello\\n"
    """))
    assert "C31" not in {a.code for a in analyze_file(str(f))}


def test_c31_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c31f.py", textwrap.dedent("""
        def test_output(capsys):
            print("hello")
            capsys.readouterr()
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C31"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# C32: @pytest.mark.skip without reason=
# ---------------------------------------------------------------------------

def test_c32_skip_no_reason_fires(tmp_path):
    f = _write(tmp_path / "test_c32a.py", textwrap.dedent("""
        import pytest

        @pytest.mark.skip
        def test_broken():
            assert False
    """))
    assert "C32" in {a.code for a in analyze_file(str(f))}


def test_c32_skip_call_no_reason_fires(tmp_path):
    f = _write(tmp_path / "test_c32b.py", textwrap.dedent("""
        import pytest

        @pytest.mark.skip()
        def test_broken():
            assert False
    """))
    assert "C32" in {a.code for a in analyze_file(str(f))}


def test_c32_skip_with_reason_clean(tmp_path):
    f = _write(tmp_path / "test_c32c.py", textwrap.dedent("""
        import pytest

        @pytest.mark.skip(reason="needs refactor before re-enabling")
        def test_broken():
            assert False
    """))
    assert "C32" not in {a.code for a in analyze_file(str(f))}


def test_c32_skipif_not_flagged(tmp_path):
    # skipif carries a condition — reason is optional by design there
    f = _write(tmp_path / "test_c32d.py", textwrap.dedent("""
        import pytest, sys

        @pytest.mark.skipif(sys.platform == "win32", reason="linux only")
        def test_posix():
            assert True
    """))
    assert "C32" not in {a.code for a in analyze_file(str(f))}


def test_c32_class_level_skip_no_reason_fires(tmp_path):
    f = _write(tmp_path / "test_c32e.py", textwrap.dedent("""
        import pytest

        @pytest.mark.skip
        class TestSuite:
            def test_one(self):
                assert 1 == 1
    """))
    assert "C32" in {a.code for a in analyze_file(str(f))}


def test_c32_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c32f.py", textwrap.dedent("""
        import pytest

        @pytest.mark.skip
        def test_broken():
            assert False
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C32"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# D4: @pytest.mark.parametrize without ids= (more than 2 cases)
# ---------------------------------------------------------------------------

def test_d4_parametrize_no_ids_fires(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD4 = "info"\n')
    f = _write(tmp_path / "test_d4a.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_positive(x):
            assert x > 0
    """))
    assert "D4" in {a.code for a in run([f], config_path=cfg)}


def test_d4_two_cases_clean(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD4 = "info"\n')
    f = _write(tmp_path / "test_d4b.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2])
        def test_positive(x):
            assert x > 0
    """))
    assert "D4" not in {a.code for a in run([f], config_path=cfg)}


def test_d4_with_ids_clean(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD4 = "info"\n')
    f = _write(tmp_path / "test_d4c.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 3], ids=["one", "two", "three"])
        def test_positive(x):
            assert x > 0
    """))
    assert "D4" not in {a.code for a in run([f], config_path=cfg)}


def test_d4_off_by_default(tmp_path):
    f = _write(tmp_path / "test_d4d.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_positive(x):
            assert x > 0
    """))
    assert "D4" not in {a.code for a in run([f])}


# ---------------------------------------------------------------------------
# C16 extension: train_test_split without random_state
# ---------------------------------------------------------------------------

def test_c16_train_test_split_no_random_state_fires(tmp_path):
    f = _write(tmp_path / "test_c16tts.py", textwrap.dedent("""
        from sklearn.model_selection import train_test_split

        def test_split():
            X_train, X_test, y_train, y_test = train_test_split(X, y)
            assert len(X_train) > 0
    """))
    codes = {a.code for a in analyze_file(str(f))}
    assert "C16" in codes


def test_c16_train_test_split_with_random_state_clean(tmp_path):
    f = _write(tmp_path / "test_c16tts2.py", textwrap.dedent("""
        from sklearn.model_selection import train_test_split

        def test_split():
            X_train, X_test = train_test_split(X, random_state=42)
            assert len(X_train) > 0
    """))
    assert "C16" not in {a.code for a in analyze_file(str(f))}


# ---------------------------------------------------------------------------
# C33: sklearn metric result never asserted
# ---------------------------------------------------------------------------

def test_c33_score_discarded_fires(tmp_path):
    f = _write(tmp_path / "test_c33a.py", textwrap.dedent("""
        def test_model_quality(model, X_test, y_test):
            model.score(X_test, y_test)
    """))
    assert "C33" in {a.code for a in analyze_file(str(f))}


def test_c33_accuracy_score_discarded_fires(tmp_path):
    f = _write(tmp_path / "test_c33b.py", textwrap.dedent("""
        from sklearn.metrics import accuracy_score

        def test_accuracy(model, X_test, y_test):
            y_pred = model.predict(X_test)
            accuracy_score(y_test, y_pred)
    """))
    assert "C33" in {a.code for a in analyze_file(str(f))}


def test_c33_f1_assigned_not_asserted_fires(tmp_path):
    f = _write(tmp_path / "test_c33c.py", textwrap.dedent("""
        from sklearn.metrics import f1_score

        def test_f1(y_true, y_pred):
            score = f1_score(y_true, y_pred)
    """))
    assert "C33" in {a.code for a in analyze_file(str(f))}


def test_c33_score_asserted_clean(tmp_path):
    f = _write(tmp_path / "test_c33d.py", textwrap.dedent("""
        def test_model_quality(model, X_test, y_test):
            acc = model.score(X_test, y_test)
            assert acc >= 0.8
    """))
    assert "C33" not in {a.code for a in analyze_file(str(f))}


def test_c33_accuracy_asserted_clean(tmp_path):
    f = _write(tmp_path / "test_c33e.py", textwrap.dedent("""
        from sklearn.metrics import accuracy_score

        def test_accuracy(y_true, y_pred):
            score = accuracy_score(y_true, y_pred)
            assert score > 0.9
    """))
    assert "C33" not in {a.code for a in analyze_file(str(f))}


def test_c33_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c33f.py", textwrap.dedent("""
        from sklearn.metrics import f1_score

        def test_f1(y_true, y_pred):
            f1_score(y_true, y_pred)
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C33"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# C34: suboptimal assert form
# ---------------------------------------------------------------------------

def test_c34_not_in_pattern_fires(tmp_path):
    f = _write(tmp_path / "test_c34a.py", textwrap.dedent("""
        def test_membership():
            assert not "x" in ["a", "b"]
    """))
    assert "C34" in {a.code for a in analyze_file(str(f))}


def test_c34_len_eq_zero_fires(tmp_path):
    f = _write(tmp_path / "test_c34b.py", textwrap.dedent("""
        def test_empty():
            result = compute()
            assert len(result) == 0
    """))
    assert "C34" in {a.code for a in analyze_file(str(f))}


def test_c34_eq_true_fires(tmp_path):
    f = _write(tmp_path / "test_c34c.py", textwrap.dedent("""
        def test_flag():
            assert is_valid() == True
    """))
    assert "C34" in {a.code for a in analyze_file(str(f))}


def test_c34_eq_false_fires(tmp_path):
    f = _write(tmp_path / "test_c34d.py", textwrap.dedent("""
        def test_flag():
            assert is_valid() == False
    """))
    assert "C34" in {a.code for a in analyze_file(str(f))}


def test_c34_eq_none_fires(tmp_path):
    f = _write(tmp_path / "test_c34e.py", textwrap.dedent("""
        def test_no_result():
            assert get_result() == None
    """))
    assert "C34" in {a.code for a in analyze_file(str(f))}


def test_c34_neq_none_fires(tmp_path):
    f = _write(tmp_path / "test_c34f.py", textwrap.dedent("""
        def test_has_result():
            assert get_result() != None
    """))
    assert "C34" in {a.code for a in analyze_file(str(f))}


def test_c34_is_none_clean(tmp_path):
    # `is None` is the correct identity check — must not fire C34.
    f = _write(tmp_path / "test_c34g.py", textwrap.dedent("""
        def test_no_result():
            assert get_result() is None
    """))
    assert "C34" not in {a.code for a in analyze_file(str(f))}


def test_c34_x_not_in_y_clean(tmp_path):
    # `not in` is already idiomatic — must not fire C34.
    f = _write(tmp_path / "test_c34h.py", textwrap.dedent("""
        def test_membership():
            assert "x" not in ["a", "b"]
    """))
    assert "C34" not in {a.code for a in analyze_file(str(f))}


def test_c34_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c34i.py", textwrap.dedent("""
        def test_flag():
            assert compute() == True
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C34"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# D5: too many inline setup statements (off by default)
# ---------------------------------------------------------------------------

def test_d5_fires_at_threshold(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD5 = "info"\n')
    f = _write(tmp_path / "test_d5a.py", textwrap.dedent("""
        def test_pipeline():
            raw = load_data()
            cleaned = clean(raw)
            normalised = normalise(cleaned)
            grouped = group_by(normalised)
            result = compute(grouped)
            assert result > 0
    """))
    assert "D5" in {a.code for a in run([f], config_path=cfg)}


def test_d5_below_threshold_clean(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD5 = "info"\n')
    f = _write(tmp_path / "test_d5b.py", textwrap.dedent("""
        def test_pipeline():
            raw = load_data()
            cleaned = clean(raw)
            normalised = normalise(cleaned)
            result = compute(normalised)
            assert result > 0
    """))
    assert "D5" not in {a.code for a in run([f], config_path=cfg)}


def test_d5_off_by_default(tmp_path):
    f = _write(tmp_path / "test_d5c.py", textwrap.dedent("""
        def test_pipeline():
            raw = load_data()
            cleaned = clean(raw)
            normalised = normalise(cleaned)
            grouped = group_by(normalised)
            result = compute(grouped)
            assert result > 0
    """))
    assert "D5" not in {a.code for a in run([f])}


# ---------------------------------------------------------------------------
# D6: print() in test body (off by default)
# ---------------------------------------------------------------------------

def test_d6_print_fires(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD6 = "info"\n')
    f = _write(tmp_path / "test_d6a.py", textwrap.dedent("""
        def test_value():
            result = compute()
            print(result)
            assert result == 42
    """))
    assert "D6" in {a.code for a in run([f], config_path=cfg)}


def test_d6_no_print_clean(tmp_path):
    cfg = _write(tmp_path / ".falsegreen.toml", '[severity]\nD6 = "info"\n')
    f = _write(tmp_path / "test_d6b.py", textwrap.dedent("""
        def test_value():
            result = compute()
            assert result == 42
    """))
    assert "D6" not in {a.code for a in run([f], config_path=cfg)}


def test_d6_off_by_default(tmp_path):
    f = _write(tmp_path / "test_d6c.py", textwrap.dedent("""
        def test_value():
            result = compute()
            print(result)
            assert result == 42
    """))
    assert "D6" not in {a.code for a in run([f])}


# ---------------------------------------------------------------------------
# C35: retry/flaky decorator masks flakiness
# ---------------------------------------------------------------------------

def test_c35_flaky_decorator_fires(tmp_path):
    f = _write(tmp_path / "test_c35a.py", textwrap.dedent("""
        import pytest

        @pytest.mark.flaky(reruns=3)
        def test_network():
            result = fetch()
            assert result is not None
    """))
    assert "C35" in {a.code for a in analyze_file(str(f))}


def test_c35_repeat_decorator_fires(tmp_path):
    f = _write(tmp_path / "test_c35b.py", textwrap.dedent("""
        import pytest

        @pytest.mark.repeat(5)
        def test_timing():
            result = measure()
            assert result < 1.0
    """))
    assert "C35" in {a.code for a in analyze_file(str(f))}


def test_c35_no_retry_clean(tmp_path):
    f = _write(tmp_path / "test_c35c.py", textwrap.dedent("""
        def test_stable():
            assert compute() == 42
    """))
    assert "C35" not in {a.code for a in analyze_file(str(f))}


def test_c35_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c35d.py", textwrap.dedent("""
        import pytest

        @pytest.mark.flaky
        def test_thing():
            assert do_thing() is True
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C35"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# C16 extension: PyTorch and TensorFlow randomness without seed
# ---------------------------------------------------------------------------

def test_c16_torch_rand_no_seed_fires(tmp_path):
    f = _write(tmp_path / "test_c16torch.py", textwrap.dedent("""
        import torch

        def test_model_output():
            x = torch.rand(10)
            result = model(x)
            assert result.shape == (10,)
    """))
    assert "C16" in {a.code for a in analyze_file(str(f))}


def test_c16_torch_rand_with_manual_seed_clean(tmp_path):
    f = _write(tmp_path / "test_c16torch2.py", textwrap.dedent("""
        import torch

        def test_model_output():
            torch.manual_seed(42)
            x = torch.rand(10)
            result = model(x)
            assert result.shape == (10,)
    """))
    assert "C16" not in {a.code for a in analyze_file(str(f))}


def test_c16_tf_random_no_seed_fires(tmp_path):
    f = _write(tmp_path / "test_c16tf.py", textwrap.dedent("""
        import tensorflow as tf

        def test_layer_output():
            x = tf.random.normal([10, 5])
            result = layer(x)
            assert result.shape == (10, 3)
    """))
    assert "C16" in {a.code for a in analyze_file(str(f))}


def test_c16_tf_random_with_set_seed_clean(tmp_path):
    f = _write(tmp_path / "test_c16tf2.py", textwrap.dedent("""
        import tensorflow as tf

        def test_layer_output():
            tf.random.set_seed(0)
            x = tf.random.normal([10, 5])
            result = layer(x)
            assert result.shape == (10, 3)
    """))
    assert "C16" not in {a.code for a in analyze_file(str(f))}


# C16 parity with JS crypto.randomUUID/getRandomValues: uuid.* and secrets.*

def test_c16_fires_for_uuid4(tmp_path):
    assert "C16" in scan_source(tmp_path, """
        import uuid
        def test_id():
            token = uuid.uuid4()
            assert token in registry
    """)


def test_c16_fires_for_secrets_token_hex(tmp_path):
    assert "C16" in scan_source(tmp_path, """
        import secrets
        def test_token():
            t = secrets.token_hex(16)
            assert len(t) == 32
    """)


def test_c16_fires_for_secrets_choice(tmp_path):
    assert "C16" in scan_source(tmp_path, """
        import secrets
        def test_pick():
            c = secrets.choice(options)
            assert c in options
    """)


def test_no_c16_for_bare_uuid4_import(tmp_path):
    # `from uuid import uuid4; uuid4()` is module-unqualified — deferred for precision,
    # matching the JS FP-averse stance (aliases not chased in v1).
    assert "C16" not in scan_source(tmp_path, """
        from uuid import uuid4
        def test_id():
            token = uuid4()
            assert token in registry
    """)


def test_no_c16_for_user_method_named_uuid4(tmp_path):
    # A user object's .uuid4() is not the stdlib module function — no leaf-match.
    assert "C16" not in scan_source(tmp_path, """
        def test_gen():
            token = generator.uuid4()
            assert token == expected
    """)


def test_no_c16_for_deterministic_uuid5(tmp_path):
    # uuid5 is namespace-deterministic, so it is reproducible — not C16.
    assert "C16" not in scan_source(tmp_path, """
        import uuid
        def test_ns():
            token = uuid.uuid5(uuid.NAMESPACE_DNS, "example.com")
            assert token == expected
    """)


# ---------------------------------------------------------------------------
# C36: pytest.fail() without a reason
# ---------------------------------------------------------------------------

def test_c36_fail_no_reason_fires(tmp_path):
    f = _write(tmp_path / "test_c36a.py", textwrap.dedent("""
        import pytest

        def test_branch():
            if compute() < 0:
                pytest.fail()
    """))
    assert "C36" in {a.code for a in analyze_file(str(f))}


def test_c36_fail_with_positional_reason_clean(tmp_path):
    f = _write(tmp_path / "test_c36b.py", textwrap.dedent("""
        import pytest

        def test_branch():
            if compute() < 0:
                pytest.fail("expected non-negative result")
    """))
    assert "C36" not in {a.code for a in analyze_file(str(f))}


def test_c36_fail_with_reason_kwarg_clean(tmp_path):
    f = _write(tmp_path / "test_c36c.py", textwrap.dedent("""
        import pytest

        def test_branch():
            if compute() < 0:
                pytest.fail(reason="expected non-negative result")
    """))
    assert "C36" not in {a.code for a in analyze_file(str(f))}


def test_c36_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c36d.py", textwrap.dedent("""
        import pytest

        def test_branch():
            pytest.fail()
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C36"]
    assert findings and findings[0].conf == "low"


# ---------------------------------------------------------------------------
# C37: duplicate parametrize case
# ---------------------------------------------------------------------------

def test_c37_duplicate_int_fires(tmp_path):
    f = _write(tmp_path / "test_c37a.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 1])
        def test_positive(x):
            assert x > 0
    """))
    assert "C37" in {a.code for a in analyze_file(str(f))}


def test_c37_duplicate_tuple_fires(tmp_path):
    f = _write(tmp_path / "test_c37b.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x,y", [(1, "a"), (2, "b"), (1, "a")])
        def test_pair(x, y):
            assert x > 0
    """))
    assert "C37" in {a.code for a in analyze_file(str(f))}


def test_c37_no_duplicates_clean(tmp_path):
    f = _write(tmp_path / "test_c37c.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_positive(x):
            assert x > 0
    """))
    assert "C37" not in {a.code for a in analyze_file(str(f))}


def test_c37_is_low_confidence(tmp_path):
    f = _write(tmp_path / "test_c37d.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [5, 5])
        def test_val(x):
            assert x > 0
    """))
    findings = [a for a in analyze_file(str(f)) if a.code == "C37"]
    assert findings and findings[0].conf == "low"


def test_c37_non_adjacent_literal_dup_fires(tmp_path):
    # the repeat is separated by a distinct case; comparison must be pairwise,
    # not adjacent. [1, 2, 1] runs the x==1 scenario twice.
    f = _write(tmp_path / "test_c37e.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 1])
        def test_positive(x):
            assert x > 0
    """))
    assert "C37" in {a.code for a in analyze_file(str(f))}


def test_c37_distinct_literals_stay_clean(tmp_path):
    f = _write(tmp_path / "test_c37f.py", textwrap.dedent("""
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_positive(x):
            assert x > 0
    """))
    assert "C37" not in {a.code for a in analyze_file(str(f))}


def test_c37_non_literal_repeat_stays_quiet(tmp_path):
    # two identical dynamic cases (a call) may resolve to different runtime
    # values, so they are not a provable duplicate: C37 must skip them.
    f = _write(tmp_path / "test_c37g.py", textwrap.dedent("""
        import pytest
        from mod import make

        @pytest.mark.parametrize("x", [make(), make()])
        def test_dyn(x):
            assert x
    """))
    assert "C37" not in {a.code for a in analyze_file(str(f))}


# ---------------------------------------------------------------------------
# Issue #31 — xUnit / unittest.TestCase subclass support
# ---------------------------------------------------------------------------

def test_xunit_testcase_subclass_c2_fires(tmp_path):
    # A unittest.TestCase subclass not starting with "Test" is still collected.
    assert "C2" in scan_source(tmp_path, """
        import unittest
        class SuiteA(unittest.TestCase):
            def test_empty(self):
                pass
    """)


def test_xunit_self_assertEqual_counts_as_assertion(tmp_path):
    # self.assertEqual does not trigger C2b (has_assertion returns True).
    assert "C2" not in scan_source(tmp_path, """
        import unittest
        class MyTests(unittest.TestCase):
            def test_ok(self):
                self.assertEqual(1 + 1, 2)
    """)
    assert "C2b" not in scan_source(tmp_path, """
        import unittest
        class MyTests(unittest.TestCase):
            def test_ok(self):
                self.assertEqual(1 + 1, 2)
    """)


def test_xunit_assertraises_context_manager_counts(tmp_path):
    # with self.assertRaises(...) counts as an assertion.
    assert "C2" not in scan_source(tmp_path, """
        import unittest
        class MyTests(unittest.TestCase):
            def test_raises(self):
                with self.assertRaises(ValueError):
                    int("bad")
    """)


# ---------------------------------------------------------------------------
# Issue #6 — C6b: positional argument layout coupling
# ---------------------------------------------------------------------------

def test_c6b_fires_for_index_subscript_on_call_args(tmp_path):
    assert "C6b" in scan_source(tmp_path, """
        def test_positional(mock_fn):
            idx = mock_fn.call_args_list[0].args.index(42)
            assert mock_fn.call_args.args[idx] == 42
    """)


def test_c6b_clean_for_named_arg_check(tmp_path):
    # Checking call_args.kwargs by name has no positional coupling.
    assert "C6b" not in scan_source(tmp_path, """
        def test_named(mock_fn):
            assert mock_fn.call_args.kwargs["key"] == "value"
    """)


# ---------------------------------------------------------------------------
# Issue #5 — C11a: self-confirming literal
# ---------------------------------------------------------------------------

def test_c11a_fires_when_assert_mirrors_constructor_kwarg(tmp_path):
    assert "C11a" in scan_source(tmp_path, """
        def test_self_confirming():
            obj = MyClass(name="alice")
            assert obj.name == "alice"
    """)


def test_c11a_clean_when_value_comes_from_sut(tmp_path):
    # Value not set by the test itself — no C11a.
    assert "C11a" not in scan_source(tmp_path, """
        def test_from_sut():
            obj = service.get_user(1)
            assert obj.name == "alice"
    """)


# ---------------------------------------------------------------------------
# Issue #7 — C16: concurrency timeout detection
# ---------------------------------------------------------------------------

def test_c16_fires_for_future_result_with_timeout(tmp_path):
    assert "C16" in scan_source(tmp_path, """
        def test_timeout():
            val = future.result(timeout=5)
            assert val == "ok"
    """)


def test_c16_clean_for_future_result_no_timeout(tmp_path):
    assert "C16" not in scan_source(tmp_path, """
        def test_no_timeout():
            val = future.result()
            assert val == "ok"
    """)


def test_c16_fires_for_thread_join_with_timeout(tmp_path):
    assert "C16" in scan_source(tmp_path, """
        import threading
        def test_join_timeout():
            t = threading.Thread(target=lambda: None)
            t.start()
            t.join(timeout=10)
            assert not t.is_alive()
    """)


def test_no_c16_for_requests_get_with_timeout(tmp_path):
    # requests.get(timeout=) is the recommended HTTP pattern, not a concurrency wait (#105).
    assert "C16" not in scan_source(tmp_path, """
        import requests
        def test_http():
            r = requests.get("http://api.example.com", timeout=5)
            assert r.status_code == 200
    """)


def test_no_c16_for_cache_get_with_timeout(tmp_path):
    # cache.get(timeout=) is a TTL, not a concurrency wait (#105).
    assert "C16" not in scan_source(tmp_path, """
        def test_cache():
            v = cache.get("k", timeout=5)
            assert v == 1
    """)


def test_no_c20_for_non_pytest_fail_attribute(tmp_path):
    # logger.fail()/result.fail() are not terminators; the assertion after them runs (#103).
    assert "C20" not in scan_source(tmp_path, """
        def test_logs_then_asserts():
            logger.fail("oops")
            assert run() == 1
    """)


def test_c20_still_fires_for_pytest_fail(tmp_path):
    # A genuine pytest.fail() is a terminator: the assertion below it is dead code.
    assert "C20" in scan_source(tmp_path, """
        import pytest
        def test_dead():
            pytest.fail("stop")
            assert run() == 1
    """)


def test_c20_still_fires_for_unittest_self_fail(tmp_path):
    # unittest TestCase.fail() raises, so it is a terminator too.
    assert "C20" in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_dead(self):
                self.fail("stop")
                assert run() == 1
    """)


# ---------------------------------------------------------------------------
# Issue #21 — C24: module-level mutable state mutated by a test
# ---------------------------------------------------------------------------

def test_c24_fires_for_list_mutated_in_test(tmp_path):
    assert "C24" in scan_source(tmp_path, """
        STORE = []
        def test_add():
            STORE.append(1)
            assert len(STORE) == 1
    """)


def test_c24_fires_for_dict_update_in_test(tmp_path):
    assert "C24" in scan_source(tmp_path, """
        CACHE = {}
        def test_put():
            CACHE["x"] = 1
            assert CACHE["x"] == 1
    """)


def test_c24_clean_when_autouse_fixture_resets(tmp_path):
    # If an autouse fixture clears the global, it is not a leak.
    assert "C24" not in scan_source(tmp_path, """
        import pytest
        STORE = []

        @pytest.fixture(autouse=True)
        def reset():
            STORE.clear()

        def test_add():
            STORE.append(1)
            assert len(STORE) == 1
    """)


def test_c24_clean_for_immutable_global(tmp_path):
    # A module-level constant (int/str) cannot be mutated.
    assert "C24" not in scan_source(tmp_path, """
        LIMIT = 10
        def test_limit():
            assert LIMIT == 10
    """)


# --- C38-C45: codes added from the consolidated catalog --------------------

def test_c38_duplicate_test_name_module(tmp_path):
    # Two top-level tests with the same name: the second overrides the first.
    assert "C38" in scan_source(tmp_path, """
        def test_login():
            assert 1 == 1
        def test_login():
            assert 2 == 2
    """)


def test_c38_duplicate_test_name_in_class(tmp_path):
    assert "C38" in scan_source(tmp_path, """
        class TestAuth:
            def test_login(self):
                assert do() == 1
            def test_login(self):
                assert do() == 2
    """)


def test_no_c38_for_distinct_names(tmp_path):
    assert "C38" not in scan_source(tmp_path, """
        def test_a():
            assert f() == 1
        def test_b():
            assert g() == 2
    """)


def test_c39_return_comparison_instead_of_assert(tmp_path):
    assert "C39" in scan_source(tmp_path, """
        def test_sum():
            return add(2, 2) == 4
    """)


def test_no_c39_for_plain_assert(tmp_path):
    assert "C39" not in scan_source(tmp_path, """
        def test_sum():
            assert add(2, 2) == 4
    """)


def test_c42_assert_generator_expression(tmp_path):
    assert "C42" in scan_source(tmp_path, """
        def test_items():
            assert (x for x in get_items())
    """)


def test_c42_assert_lambda(tmp_path):
    assert "C42" in scan_source(tmp_path, """
        def test_cb():
            assert lambda: do()
    """)


def test_no_c42_for_list_comprehension(tmp_path):
    # A list comprehension can be empty, so the assertion is a real (weak) check.
    assert "C42" not in scan_source(tmp_path, """
        def test_items():
            assert [x for x in get_items()]
    """)


def test_c43_skip_after_logic_strands_check(tmp_path):
    assert "C43" in scan_source(tmp_path, """
        import pytest
        def test_flow():
            result = build()
            pytest.skip("not ready")
            assert result == 42
    """)


def test_no_c43_for_skip_at_top(tmp_path):
    assert "C43" not in scan_source(tmp_path, """
        import pytest
        def test_flow():
            pytest.skip("blocked")
            assert build() == 42
    """)


def test_c44_len_ge_zero_is_tautology(tmp_path):
    assert "C44" in scan_source(tmp_path, """
        def test_nonempty():
            assert len(get_items()) >= 0
    """)


def test_c44_zero_le_len_is_tautology(tmp_path):
    assert "C44" in scan_source(tmp_path, """
        def test_nonempty():
            assert 0 <= len(get_items())
    """)


def test_no_c44_for_real_len_check(tmp_path):
    assert "C44" not in scan_source(tmp_path, """
        def test_nonempty():
            assert len(get_items()) >= 3
    """)


# --- #91: weak/tautological assertions on a mock's call_count ---------------

def test_c44_mock_call_count_ge_zero_is_tautology(tmp_path):
    # call_count on a Mock is never negative, so >= 0 is always true (#91).
    assert "C44" in scan_source(tmp_path, """
        def test_called(mock_svc):
            run(mock_svc)
            assert mock_svc.call_count >= 0
    """)


def test_c44_mock_call_count_gt_minus_one_is_tautology(tmp_path):
    assert "C44" in scan_source(tmp_path, """
        def test_called(mock_svc):
            assert mock_svc.call_count > -1
    """)


def test_c6c_bare_mock_call_count_is_weak(tmp_path):
    # Bare truthiness passes on any count >= 1: it checks "was it called", not how often (#91).
    assert "C6c" in scan_source(tmp_path, """
        def test_called(mock_svc):
            run(mock_svc)
            assert mock_svc.call_count
    """)


def test_no_c6c_c44_for_a_real_call_count_check(tmp_path):
    # An exact or lower-bounded count is a genuine check — stays quiet.
    codes = scan_source(tmp_path, """
        def test_called(mock_svc):
            assert mock_svc.call_count == 2
    """)
    assert "C6c" not in codes
    assert "C44" not in codes


def test_no_c6c_for_call_count_on_a_non_mock(tmp_path):
    # The receiver must be a known mock; a plain object's .call_count is not C6c.
    assert "C6c" not in scan_source(tmp_path, """
        def test_x(counter):
            assert counter.call_count
    """)


def test_c45_empty_parametrize_runs_zero_times(tmp_path):
    assert "C45" in scan_source(tmp_path, """
        import pytest
        @pytest.mark.parametrize("n", [])
        def test_n(n):
            assert process(n) > 0
    """)


def test_no_c45_for_populated_parametrize(tmp_path):
    assert "C45" not in scan_source(tmp_path, """
        import pytest
        @pytest.mark.parametrize("n", [1, 2, 3])
        def test_n(n):
            assert process(n) > 0
    """)


def test_c41_assert_not_on_none_mutator(tmp_path):
    # `lst.sort()` returns None; `not None` is True, so the assert is always green
    # and the sort is never verified.
    assert "C41" in scan_source(tmp_path, """
        def test_sort():
            lst = [3, 1, 2]
            assert not lst.sort()
    """)


def test_c41_bare_none_mutator_is_red_not_c41(tmp_path):
    # `lst.append(1)` returns None, so `assert lst.append(1)` is `assert None`,
    # which FAILS (red). A failing assert is not a false-green — not C41.
    assert "C41" not in scan_source(tmp_path, """
        def test_append():
            lst = []
            assert lst.append(1)
    """)


def test_c41_assert_not_on_dict_update(tmp_path):
    # d.update({}) returns None; `assert d.update(...) is None` is always green.
    assert "C41" in scan_source(tmp_path, """
        def test_update():
            d = {}
            assert d.update({}) is None
    """)


def test_no_c41_for_custom_object_receiver(tmp_path):
    # `registry` is a function arg with no local container evidence; its add()
    # may return a value, so flagging would be a false positive — not C41.
    assert "C41" not in scan_source(tmp_path, """
        def test_register(registry):
            assert registry.add("x") is None
    """)


def test_c41_assert_mutator_is_none(tmp_path):
    assert "C41" in scan_source(tmp_path, """
        def test_append():
            lst = []
            assert lst.append(1) is None
    """)


def test_c41_assert_is_none_unittest_form(tmp_path):
    assert "C41" in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_sort(self):
                lst = [3, 1, 2]
                self.assertIsNone(lst.sort())
    """)


def test_c20_dead_assertion_owns_the_line_not_c41(tmp_path):
    # A None-mutator assertion that sits after a return never runs. C20 (dead code,
    # HIGH) owns the line; its triviality (C41, LOW) is moot and suppressed (#108).
    codes = scan_source(tmp_path, """
        def test_dead():
            data = [3, 1]
            return
            assert data.sort() is None
    """)
    assert "C20" in codes
    assert "C41" not in codes


def test_no_c41_for_value_returning_method(tmp_path):
    # pop() returns the removed element — a real value-checking assertion, not a
    # None-mutator false-green.
    assert "C41" not in scan_source(tmp_path, """
        def test_pop():
            lst = [1, 2, 3]
            assert lst.pop() == 3
    """)


def test_no_c41_for_pure_builtin_sorted(tmp_path):
    # sorted() returns a new list; the equality is a genuine check.
    assert "C41" not in scan_source(tmp_path, """
        def test_sorted():
            lst = [3, 1, 2]
            assert sorted(lst) == [1, 2, 3]
    """)


def test_no_c41_when_container_binding_is_after_the_assert(tmp_path):
    # Codex round-2 on #80: container evidence must precede the assertion. Here the
    # receiver is only bound to a list literal AFTER the assert, so at the point the
    # assertion runs `obj` is unknown (its method could return a value) — not C41.
    assert "C41" not in scan_source(tmp_path, """
        def test_later_binding():
            obj = make_thing()
            assert not obj.sort()
            obj = []
    """)


def test_c41_when_container_binding_precedes_the_assert(tmp_path):
    # Guard for the same fix: a binding BEFORE the assert still proves the container,
    # so the None-mutator assertion is still flagged.
    assert "C41" in scan_source(tmp_path, """
        def test_earlier_binding():
            obj = []
            assert not obj.sort()
            obj = []
    """)


def test_no_c41_unittest_form_when_binding_is_after_assert(tmp_path):
    # Same precedence rule for the assertIsNone(...) xunit form.
    assert "C41" not in scan_source(tmp_path, """
        import unittest
        class T(unittest.TestCase):
            def test_sort(self):
                obj = make_thing()
                self.assertIsNone(obj.sort())
                obj = []
    """)


def test_no_c41_when_container_binding_is_in_a_nested_scope(tmp_path):
    # Codex round-2 (second half) on #80: container evidence must not be collected
    # from nested scopes. Here the asserted receiver is a custom-object parameter,
    # and a NESTED helper binds the same name to a list literal. That binding is the
    # helper's local, not the test's — it does not prove the receiver is a container.
    assert "C41" not in scan_source(tmp_path, """
        def test_register(registry):
            def helper():
                registry = []
                return registry
            assert registry.add("x") is None
    """)


def test_c41_when_container_binding_is_at_test_top_level(tmp_path):
    # Guard for the same fix: a literal binding in the test body itself (even inside
    # a non-def block) is still local evidence, so the None-mutator assert still fires.
    assert "C41" in scan_source(tmp_path, """
        def test_top_level():
            if True:
                lst = [3, 1, 2]
            assert lst.sort() is None
    """)


# --- C48: dark-patch (test flips a test-mode flag then asserts) --------------

def test_c48_flags_env_test_mode_then_assert(tmp_path):
    # The test forces TESTING=1 in os.environ, then asserts — it exercises the
    # product's `if TESTING:` test-only branch, not the path a real user hits.
    assert "C48" in scan_source(tmp_path, """
        import os
        def test_dark():
            os.environ["TESTING"] = "1"
            assert feature() == "ok"
    """)


def test_c48_flags_module_flag_then_assert(tmp_path):
    # A settings/module flag named TESTING set to True, then asserted, is the same smell.
    assert "C48" in scan_source(tmp_path, """
        def test_dark():
            settings.TESTING = True
            assert run() == 1
    """)


def test_c48_flags_global_name_then_assert(tmp_path):
    # A bare TESTING = True only mutates shared state when declared global; then it is C48.
    assert "C48" in scan_source(tmp_path, """
        def test_dark():
            global TESTING
            TESTING = True
            assert run() == 1
    """)


def test_no_c48_for_config_value(tmp_path):
    # DATABASE_URL is configuration, not a test-mode toggle — not C48 (still C29 leak).
    codes = scan_source(tmp_path, """
        import os
        def test_cfg():
            os.environ["DATABASE_URL"] = "sqlite://"
            assert run() == 1
    """)
    assert "C48" not in codes


def test_no_c48_for_feature_flag(tmp_path):
    # A product feature flag is real behaviour under test, not a test-mode dark patch.
    assert "C48" not in scan_source(tmp_path, """
        def test_feature():
            settings.FEATURE_X = True
            assert run() == 1
    """)


def test_no_c48_for_non_test_mode_value(tmp_path):
    # TEST_MODE set to "production" does not enable a test branch — not C48.
    assert "C48" not in scan_source(tmp_path, """
        import os
        def test_prod():
            os.environ["TEST_MODE"] = "production"
            assert run() == 1
    """)


def test_no_c48_without_downstream_assertion(tmp_path):
    # A flag write with no assertion after it is fixture/setup, not a dark-patch test.
    assert "C48" not in scan_source(tmp_path, """
        import os
        def test_setup_only():
            os.environ["TESTING"] = "1"
            do_setup()
    """)


def test_no_c48_for_local_name_without_global(tmp_path):
    # A bare TESTING = True with no `global` is a local variable — it changes no shared
    # state, so the product never sees it; not a dark patch.
    assert "C48" not in scan_source(tmp_path, """
        def test_local():
            TESTING = True
            assert TESTING
    """)


def test_no_c48_when_genuine_assertion_runs_before_the_flip(tmp_path):
    # Real behaviour is asserted before the test-mode write, so the post-flip
    # asserts are incidental, not a dark-patch (#107).
    assert "C48" not in scan_source(tmp_path, """
        import os
        def test_real_then_flips():
            assert pre() == 1
            os.environ["TESTING"] = "1"
            assert post() == 2
    """)


def test_c48_still_fires_when_post_flip_assert_checks_the_toggle(tmp_path):
    # Even with a genuine assertion before the flip, a post-flip assertion that
    # inspects the toggled flag itself IS the dark-patch (#107).
    assert "C48" in scan_source(tmp_path, """
        import os
        def test_checks_flag():
            assert pre() == 1
            os.environ["TESTING"] = "1"
            assert os.environ["TESTING"] == "1"
    """)


def test_no_c48_when_post_assert_only_mentions_key_as_unrelated_literal(tmp_path):
    # A genuine pre-flip assert, and the post-flip assert merely contains the string
    # "TESTING" as an unrelated literal — it does NOT read the flag, so not a dark
    # patch. Guards against a leaf-match that fires on any matching token (#107).
    assert "C48" not in scan_source(tmp_path, """
        import os
        def test_unrelated_literal():
            assert pre() == 1
            os.environ["TESTING"] = "1"
            assert label() == "TESTING"
    """)


def test_no_c48_when_post_assert_reads_same_named_attr_on_other_object(tmp_path):
    # settings.TESTING is flipped, but the post-flip assert reads other.TESTING — a
    # different object. Receiver-aware: must NOT fire (#107).
    assert "C48" not in scan_source(tmp_path, """
        def test_other_object():
            assert pre() == 1
            settings.TESTING = True
            assert other.TESTING == 5
    """)


def test_no_c48_for_self_attribute(tmp_path):
    # self.TESTING mutates instance state of the test, not a module/product flag.
    assert "C48" not in scan_source(tmp_path, """
        class T:
            def test_x(self):
                self.TESTING = True
                assert run() == 1
    """)


def test_c48_suppresses_c29_on_the_same_write(tmp_path):
    # When C48 fires on an os.environ test-mode write, the more specific C48 owns the
    # line: C29 (env-leak) is not also reported on it.
    codes = scan_source(tmp_path, """
        import os
        def test_dark():
            os.environ["TESTING"] = "1"
            assert feature() == "ok"
    """)
    assert "C48" in codes and "C29" not in codes


def test_c29_still_fires_on_non_test_mode_env_write(tmp_path):
    # The C48 suppression is scoped to the C48 line: a plain env write (DATABASE_URL)
    # that C48 does not flag still reports the C29 leak.
    assert "C29" in scan_source(tmp_path, """
        import os
        def test_cfg():
            os.environ["DATABASE_URL"] = "sqlite://"
            assert run() == 1
    """)


# --- Codex review fixes (each fix gets a test) -------------------------------

def test_no_c43_for_non_pytest_skip_method(tmp_path):
    # reader.skip() is a SUT/helper method, not pytest.skip — must not be C43.
    assert "C43" not in scan_source(tmp_path, """
        def test_reads():
            reader = open_reader()
            reader.skip(1)
            assert reader.read() == 42
    """)


def test_c43_still_flags_real_mid_test_pytest_skip(tmp_path):
    assert "C43" in scan_source(tmp_path, """
        import pytest
        def test_flow():
            result = build()
            pytest.skip("not ready")
            assert result == 42
    """)


def test_no_c38_for_non_test_class(tmp_path):
    # A plain helper class is not collected by pytest, so duplicate test_* helper
    # methods are not a vanished test (no C38).
    assert "C38" not in scan_source(tmp_path, """
        class Helper:
            def test_build(self):
                return 1
            def test_build(self):
                return 2
    """)


def test_c38_still_flags_in_test_class(tmp_path):
    assert "C38" in scan_source(tmp_path, """
        class TestAuth:
            def test_login(self):
                assert do() == 1
            def test_login(self):
                assert do() == 2
    """)


def test_no_c17_for_non_pytest_skip_in_except(tmp_path):
    # reader.skip() inside a broad except is not a pytest skip — not C17.
    assert "C17" not in scan_source(tmp_path, """
        def test_x():
            try:
                assert compute() == 42
            except Exception:
                reader.skip()
    """)


# --- status report: pyramid level, fix-hint, output dir ----------------------

def _findings_of(tmp_path, code):
    return analyze_file(_write(tmp_path / "test_lvl.py", code))


def test_detect_pyramid_level_unit_by_default(tmp_path):
    fs = _findings_of(tmp_path, """
        def test_x():
            assert True
    """)
    assert fs and all(a.level == "unit" for a in fs)


def test_detect_pyramid_level_integration_on_web_import(tmp_path):
    fs = _findings_of(tmp_path, """
        import requests
        def test_x():
            assert True
    """)
    assert fs and all(a.level == "integration" for a in fs)


def test_http_mock_library_does_not_count_as_integration(tmp_path):
    # importing only an HTTP stub/record library (responses/respx/requests_mock)
    # means the boundary is mocked: the test stays unit, not integration.
    for lib in ("responses", "respx", "requests_mock", "httpretty", "vcr"):
        fs = _findings_of(tmp_path, """
            import %s
            def test_x():
                assert True
        """ % lib)
        assert fs and all(a.level == "unit" for a in fs), lib


def test_real_web_client_still_integration_even_with_mock(tmp_path):
    # a real client present (requests) keeps it integration even if a stub is too
    fs = _findings_of(tmp_path, """
        import requests
        import responses
        def test_x():
            assert True
    """)
    assert fs and all(a.level == "integration" for a in fs)


def test_detect_pyramid_level_integration_on_db_import(tmp_path):
    fs = _findings_of(tmp_path, """
        import sqlalchemy
        def test_x():
            assert True
    """)
    assert fs and all(a.level == "integration" for a in fs)


def test_detect_pyramid_level_e2e_on_browser_import(tmp_path):
    fs = _findings_of(tmp_path, """
        from playwright.sync_api import sync_playwright
        def test_x():
            assert True
    """)
    assert fs and all(a.level == "e2e" for a in fs)


def test_json_finding_carries_level_and_fix(tmp_path):
    findings = run([_write(tmp_path / "test_j.py", "def test_x():\n    assert True\n")])
    d = json.loads(render_json(findings))[0]
    assert d["level"] == "unit"
    assert "constant" in d["fix"]  # C5's fix hint, present and meaningful


def test_render_text_shows_level_and_fix(tmp_path):
    from falsegreen.scanner import render_text
    findings = run([_write(tmp_path / "test_r.py", "def test_x():\n    assert True\n")])
    out = render_text(findings)
    assert "level: unit" in out
    assert "fix:" in out


def test_render_text_summary_by_level_and_top_fixes(tmp_path):
    from falsegreen.scanner import render_text
    findings = run([_write(tmp_path / "test_r2.py", "def test_x():\n    assert True\n")])
    out = render_text(findings)
    assert "By level:" in out
    assert "unit:1" in out
    assert "Top fixes:" in out
    assert "C5 (1)" in out


def test_output_directory_writes_report_file(tmp_path):
    f = _write(tmp_path / "test_h.py", "def test_x():\n    assert True\n")
    outdir = tmp_path / ".falsegreen"
    main([f, "--json", "--output", str(outdir)])
    report = outdir / "report.json"
    assert report.exists()
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc[0]["code"] == "C5"


def test_output_file_path_still_writes_file(tmp_path):
    # a path with an extension is still treated as a file, not a directory
    f = _write(tmp_path / "test_h2.py", "def test_x():\n    assert True\n")
    out = tmp_path / "sub" / "report.sarif"
    main([f, "--format", "sarif", "--output", str(out)])
    assert out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == "2.1.0"


def test_fix_hints_cover_every_case(tmp_path):
    # every catalog code must have a remediation; the report promises one
    from falsegreen.scanner import CASES, FIX_HINTS
    missing = [c for c in CASES if c not in FIX_HINTS]
    assert missing == [], "codes without a fix-hint: %s" % missing


# --- PL config-audit: project-layer checks -----------------------------------

from falsegreen.scanner import audit_config  # noqa: E402


def test_config_audit_flags_all_when_pyproject_bare(tmp_path):
    _write(tmp_path / "pyproject.toml", """
        [tool.pytest.ini_options]
        addopts = "-x"
    """)
    codes = {f.code for f in audit_config(str(tmp_path))}
    assert codes == {"PL2", "PL7", "PL8"}


def test_config_audit_clean_pyproject(tmp_path):
    _write(tmp_path / "pyproject.toml", """
        [tool.pytest.ini_options]
        addopts = "--cov-fail-under=80"
        filterwarnings = ["error"]
    """)
    assert audit_config(str(tmp_path)) == []


def test_config_audit_cov_gate_via_coverage_table(tmp_path):
    _write(tmp_path / "pyproject.toml", """
        [tool.pytest.ini_options]
        filterwarnings = ["error"]
        [tool.coverage.report]
        fail_under = 90
    """)
    codes = {f.code for f in audit_config(str(tmp_path))}
    assert "PL7" not in codes  # coverage gate satisfied via [tool.coverage.report]


def test_config_audit_pl8_maxfail(tmp_path):
    _write(tmp_path / "pyproject.toml", """
        [tool.pytest.ini_options]
        addopts = "--maxfail=1"
        filterwarnings = ["error"]
        [tool.coverage.report]
        fail_under = 1
    """)
    assert {f.code for f in audit_config(str(tmp_path))} == {"PL8"}


def test_config_audit_reads_pytest_ini(tmp_path):
    _write(tmp_path / "pytest.ini", "[pytest]\naddopts = -x\n")
    codes = {f.code for f in audit_config(str(tmp_path))}
    assert "PL8" in codes and "PL2" in codes


def test_config_audit_no_pytest_config_is_empty(tmp_path):
    assert audit_config(str(tmp_path)) == []


def test_config_audit_finding_carries_level_and_fix(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\naddopts = \"-x\"\n")
    f = next(x for x in audit_config(str(tmp_path)) if x.code == "PL8")
    d = f.dict()
    assert d["level"] == "project"
    assert "addopts" in d["fix"]


def test_config_audit_cli_exit_and_output(tmp_path):
    _write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\naddopts = \"-x\"\n")
    out = tmp_path / "rep.json"
    rc = main(["--config-audit", "--json", "--output", str(out), str(tmp_path)])
    assert rc == 10
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert {d["code"] for d in doc} == {"PL2", "PL7", "PL8"}


def test_pl_codes_have_fix_hints():
    from falsegreen.scanner import FIX_HINTS
    assert {"PL1", "PL2", "PL7", "PL8"} <= set(FIX_HINTS)


def test_config_audit_pl1_tox_dash_o(tmp_path):
    # tox running python -O -m pytest strips every assert: the suite goes green
    # with no checks. PL1 fires; it is a project WARNING that never blocks.
    _write(tmp_path / "tox.ini", """
        [testenv]
        commands = python -O -m pytest
        [pytest]
        filterwarnings = error
        addopts = --cov-fail-under=80
    """)
    from falsegreen.scanner import CASES
    findings = audit_config(str(tmp_path))
    codes = {f.code for f in findings}
    assert "PL1" in codes
    assert CASES["PL1"][1] == "low"  # never blocks


def test_config_audit_no_pl1_without_optimize(tmp_path):
    _write(tmp_path / "tox.ini", """
        [testenv]
        commands = python -m pytest
        [pytest]
        filterwarnings = error
        addopts = --cov-fail-under=80
    """)
    assert "PL1" not in {f.code for f in audit_config(str(tmp_path))}


def test_config_audit_no_pl1_for_pythonoptimize_zero(tmp_path):
    # PYTHONOPTIMIZE=0 leaves asserts ON: the safe config, one token off the BAD
    # one. It must NOT fire PL1.
    _write(tmp_path / "tox.ini", """
        [testenv]
        setenv = PYTHONOPTIMIZE=0
        commands = python -m pytest
        [pytest]
        filterwarnings = error
        addopts = --cov-fail-under=80
    """)
    assert "PL1" not in {f.code for f in audit_config(str(tmp_path))}


def test_version_lockstep():
    # __version__ must equal pyproject.toml and CITATION.cff. Single equality-chain assert,
    # no conditional and no truthiness check, so the scanner's own self-scan stays clean.
    import re
    import pathlib
    from falsegreen.scanner import __version__
    from falsegreen import __version__ as pkg_version
    root = pathlib.Path(__file__).resolve().parent.parent

    def _ver(path, pat):
        m = re.search(pat, path.read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None

    pyproject_v = _ver(root / "pyproject.toml", r'^version\s*=\s*"([^"]+)"')
    cff_v = _ver(root / "CITATION.cff", r'^version:\s*(\S+)')
    assert __version__ == pkg_version == pyproject_v == cff_v, (
        "version lockstep broken: scanner=%s package=%s pyproject=%s CITATION=%s"
        % (__version__, pkg_version, pyproject_v, cff_v))


# ---------------------------------------------------------------------------
# C49: pytest.warns / assertWarns wrapping more than one call (sibling of C19)
# ---------------------------------------------------------------------------

def test_c49_warns_multi_call_fires(tmp_path):
    assert "C49" in scan_source(tmp_path, """
        import pytest
        def test_dep():
            with pytest.warns(DeprecationWarning):
                build_config()
                call_legacy()
    """)


def test_c49_warns_single_call_clean(tmp_path):
    # One call inside the warns block is exactly the right shape, one token away.
    assert "C49" not in scan_source(tmp_path, """
        import pytest
        def test_dep():
            with pytest.warns(DeprecationWarning):
                call_legacy()
    """)


def test_c49_xunit_assert_warns_multi_call_fires(tmp_path):
    assert "C49" in scan_source(tmp_path, """
        class T(TestCase):
            def test_dep(self):
                with self.assertWarns(DeprecationWarning):
                    build_config()
                    call_legacy()
    """)


def test_c49_deprecated_call_multi_fires(tmp_path):
    assert "C49" in scan_source(tmp_path, """
        import pytest
        def test_dep():
            with pytest.deprecated_call():
                build_config()
                call_legacy()
    """)


# ---------------------------------------------------------------------------
# C50: caplog / assertLogs output captured but never asserted (sibling of C31)
# ---------------------------------------------------------------------------

def test_c50_assertlogs_not_asserted_fires(tmp_path):
    assert "C50" in scan_source(tmp_path, """
        class T(TestCase):
            def test_log(self):
                with self.assertLogs('app') as cm:
                    run_job()
    """)


def test_c50_assertlogs_asserted_clean(tmp_path):
    # cm.output reaches an assertIn one line down, so the capture has an oracle.
    assert "C50" not in scan_source(tmp_path, """
        class T(TestCase):
            def test_log(self):
                with self.assertLogs('app') as cm:
                    run_job()
                self.assertIn('done', cm.output[0])
    """)


def test_c50_caplog_not_asserted_fires(tmp_path):
    assert "C50" in scan_source(tmp_path, """
        def test_log(caplog):
            with caplog.at_level('INFO'):
                run_job()
    """)


def test_c50_caplog_asserted_clean(tmp_path):
    assert "C50" not in scan_source(tmp_path, """
        def test_log(caplog):
            with caplog.at_level('INFO'):
                run_job()
            assert 'done' in caplog.text
    """)


# ---------------------------------------------------------------------------
# C51: empty-bodied pytest.raises / warns context (call that should raise omitted)
# ---------------------------------------------------------------------------

def test_c51_empty_raises_body_fires(tmp_path):
    assert "C51" in scan_source(tmp_path, """
        import pytest
        def test_raises():
            with pytest.raises(ValueError):
                pass
    """)


def test_c51_raises_with_one_call_clean(tmp_path):
    # A single call inside the block is legitimate, one token (pass -> a call) away.
    assert "C51" not in scan_source(tmp_path, """
        import pytest
        def test_raises():
            with pytest.raises(ValueError):
                parse("bad")
    """)


def test_c51_empty_warns_body_fires(tmp_path):
    assert "C51" in scan_source(tmp_path, """
        import pytest
        def test_warns():
            with pytest.warns(UserWarning):
                pass
    """)


def test_c51_as_binding_left_to_c28_clean(tmp_path):
    # An `as` binding is C28 territory, not C51, even with an empty body.
    codes = scan_source(tmp_path, """
        import pytest
        def test_raises():
            with pytest.raises(ValueError) as e:
                pass
    """)
    assert "C51" not in codes


# ---------------------------------------------------------------------------
# C52: membership self-confirmation (assert x in collection built from x)
# ---------------------------------------------------------------------------

def test_c52_membership_self_confirm_fires(tmp_path):
    assert "C52" in scan_source(tmp_path, """
        def test_member():
            assert obj.id in {obj.id}
    """)


def test_c52_membership_distinct_element_clean(tmp_path):
    # One token away: the literal holds a DIFFERENT element, so it is a real check.
    assert "C52" not in scan_source(tmp_path, """
        def test_member():
            assert obj.id in {other.id}
    """)


def test_c52_membership_in_registry_clean(tmp_path):
    # A Name container is a lookup against a registry, not a self-built literal.
    assert "C52" not in scan_source(tmp_path, """
        def test_member():
            assert obj.id in registry
    """)


def test_c52_eq_semantics_membership_exempt_clean(tmp_path):
    # Beside `ws == ws` the membership is the __eq__/__hash__ half, not a smell.
    assert "C52" not in scan_source(tmp_path, """
        def test_eq():
            assert ws == ws
            assert ws in {ws}
    """)


# ---------------------------------------------------------------------------
# C55: assertion comparing two mock-rooted values (both sides stand-ins)
# ---------------------------------------------------------------------------

def test_c55_both_sides_mock_fires(tmp_path):
    assert "C55" in scan_source(tmp_path, """
        def test_cmp(mock_a, mock_b):
            assert mock_a.return_value == mock_b.return_value
    """)


def test_c55_one_real_side_clean(tmp_path):
    # One token away: the right side is a concrete literal, a legitimate check.
    assert "C55" not in scan_source(tmp_path, """
        def test_cmp(mock_a):
            assert mock_a.return_value == 42
    """)


def test_c55_both_children_of_one_mock_fires(tmp_path):
    assert "C55" in scan_source(tmp_path, """
        def test_cmp(mock_client):
            assert mock_client.foo == mock_client.bar
    """)
