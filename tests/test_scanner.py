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
    assert conf == {"disable": set(), "exclude": [], "severity": {}}


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
    assert all(e[1] in ("high", "low", "off") for e in entries)
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
