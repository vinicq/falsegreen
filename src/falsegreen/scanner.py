#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
falsegreen: deterministic false-positive scanner for Python/pytest tests.

Parses test files with the AST (no code execution) and flags the mechanical
patterns that make a test pass without protecting anything. Each finding maps
to a case from the guide (docs/guide.md).

Cases 12 (re-implementing the production logic inside the test) and 18 (the
expected value contradicts the intended behavior) are NOT statically detectable.
They belong to the semantic pass of the falsegreen skill, which compares the
test's expected value against the INTENDED behavior (spec, contract, then code),
not against whatever the code returns today. The scanner owns what the structure
can prove.

Output:
  - readable text (default) or JSON (--json)
  - exit code: 0 clean, 10 low-confidence only, 20 high-confidence present

Suppress a finding inline with a comment on its line:
  assert total == 0.3   # falsegreen: ignore        (silences every code)
  assert total == 0.3   # falsegreen: ignore[C8]    (silences only C8)

Usage:
  falsegreen [paths...]         # files/dirs; no args = scan cwd
  falsegreen --staged           # only test files staged in git
  falsegreen --json             # JSON output
  falsegreen --disable C6,C2b   # turn off specific codes
"""

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

__version__ = "0.9.1"  # keep in lockstep with pyproject.toml (test_version_lockstep enforces it)
TOOL_URI = "https://github.com/vinicq/falsegreen"
DOCS_CATALOG_URI = "https://vinicq.github.io/falsegreen-docs/catalog/python/"

try:
    import tomllib as _toml  # Python 3.11+
except ImportError:  # pragma: no cover - depends on interpreter version
    try:
        import tomli as _toml  # backport for 3.8-3.10
    except ImportError:
        _toml = None  # no TOML reader: config is a silent no-op (3.8 without tomli)

# ---------------------------------------------------------------------------
# Case catalog (mirrors the guide). code -> (title, confidence, judgment)
# confidence: "high" => blocks the commit; "low" => warns only.
# judgment: which of the semantic pass's questions the code belongs to (J1-J6,
# see falsegreen-skill). Lets output/SARIF/docs group findings by category without
# splitting the flat module.
# ---------------------------------------------------------------------------
JUDGMENTS = {
    "J1": "does the assertion actually run?",
    "J2": "is the oracle independent of the code?",
    "J3": "does it exercise the real unit, or a stand-in?",
    "J4": "does it check enough, and the right thing?",
    "J5": "is it coupled to internals it should not see?",
    "J6": "does it pass in isolation, or only via shared state?",
}

CASES = {
    "C1":  ("assert inside if/for that may never run", "low", "J1"),
    "C2":  ("test with no check at all (empty body)", "high", "J1"),
    "C2b": ("test calls things but checks nothing", "low", "J1"),
    "C2c": ("self.subTest(...) block wraps work but asserts nothing (the subTest analogue of an empty test)", "low", "J1"),
    "C3":  ("assert inside try whose except swallows the error", "high", "J1"),
    "C4":  ("test is not collected by pytest (silently never runs)", "high", "J1"),
    "C4b": ("test class has __init__ (not collected unless subclassed)", "low", "J1"),
    "C5":  ("always-true check (assert True / tuple / or True)", "high", "J2"),
    "C6":  ("weak check (only verifies that something came back)", "low", "J4"),
    "C6b": ("assertion coupled to positional argument layout", "low", "J5"),
    "C6c": ("asserts a mock's call_count truthiness — only that it was called, not how many times", "low", "J4"),
    "C7":  ("compares a thing to itself (always matches)", "high", "J2"),
    "C8":  ("exact equality on a float (fails on rounding, not bugs)", "low", "J4"),
    "C8b": ("approximate-equality with no explicit tolerance (default places/rel hides a wrong value)", "low", "J4"),
    "C9":  ("pytest.raises too broad (accepts any error)", "low", "J4"),
    "C11a":("self-confirming literal assigned by the test itself", "low", "J2"),
    "C13": ("mock assertion misspelled / not called (always passes)", "high", "J3"),
    "C13b":("patch without autospec (lets mock typos pass)", "low", "J3"),
    "C14": ("golden/snapshot generated from the output itself", "low", "J2"),
    "C16": ("result depends on time, randomness or a fixed sleep", "low", "J1"),
    "C17": ("skip inside a broad except hides a real failure", "high", "J1"),
    "C18": ("compares str()/repr() of a value to a literal (checks formatting)", "low", "J2"),
    "C19": ("pytest.raises wraps more than one call (target may never be reached)", "low", "J1"),
    "C20": ("assertion in dead code after return/raise/fail (never runs)", "high", "J1"),
    "C21": ("every assertion is conditional, none runs unconditionally", "low", "J1"),
    "C22": ("async test asserts but never awaits the unit (vacuous pass)", "off", "J1"),
    "C23": ("opens a real file at a literal path (mystery guest)", "low", "J6"),
    "C24": ("module-global mutable state shared across tests", "low", "J6"),
    "C25": ("xfail without strict=True silently accepts a fixed bug (XPASS treated as pass)", "low", "J4"),
    "C27": ("try/except/pass — test passes whether the expected exception is raised or not", "high", "J1"),
    "C28": ("pytest.raises binding declared but exception content never inspected", "low", "J4"),
    "C29": ("os.environ assigned directly in a test — env state leaks between tests", "low", "J6"),
    "C30": ("responses.add() / httpretty.register_uri() without activating the interceptor", "low", "J3"),
    "C31": ("capsys/capfd.readouterr() result never asserted — stdout/stderr captured but not checked", "low", "J4"),
    "C32": ("@pytest.mark.skip without reason= — test silently excluded with no documented motive", "low", "J1"),
    "C33": ("sklearn metric result never asserted — model performance computed but no threshold checked", "low", "J4"),
    "C34": ("suboptimal assert form — a simpler, more idiomatic alternative exists", "low", "J4"),
    "C35": ("retry/repeat/flaky decorator masks flaky behaviour instead of fixing it", "low", "J1"),
    "C36": ("pytest.fail() called without a reason — the failure message is empty", "low", "J4"),
    "C37": ("duplicate test case in @pytest.mark.parametrize — same argument set runs the same scenario twice", "low", "J4"),
    "C38": ("two test functions share a name — the second silently overrides the first, the first never runs", "high", "J1"),
    "C39": ("test returns a comparison instead of asserting it — pytest ignores the value, nothing is checked", "high", "J1"),
    "C41": ("assertion on an in-place method that returns None (sort/append/...) — trivially satisfied", "low", "J4"),
    "C42": ("assertion on a generator expression or lambda — the object is always truthy", "high", "J2"),
    "C43": ("pytest.skip() called after test logic — the verification below it may never run", "low", "J1"),
    "C44": ("numeric tautology — len()/abs() compared so the result is always true", "high", "J2"),
    "C45": ("empty @pytest.mark.parametrize list — the test is generated with zero cases and never runs", "high", "J1"),
    "C48": ("test flips a test-mode flag (env/module) then asserts — exercises the product's test-only branch, not real behaviour", "low", "J1"),
    "C49": ("pytest.warns/assertWarns wraps more than one call (an unrelated earlier line may warn while the target never does)", "low", "J1"),
    "C50": ("caplog/assertLogs output captured but never asserted — the capture has no effect on pass/fail", "low", "J4"),
    "C51": ("empty-bodied pytest.raises/warns context — the call that should raise is never made", "high", "J1"),
    "C52": ("membership self-confirmation (assert x in a collection built from x) — true by construction", "low", "J2"),
    "C55": ("assertion compares two mock-rooted values — both sides are the test's own doubles, not the SUT", "low", "J3"),
    # --- deep-sweep codes (issue #51): NEW, not field-validated yet (L15). LOW/HIGH
    #     per the structural certainty of each; precision-first guards. ----------
    "C56": ("sync assert of a never-awaited coroutine — the operand is a call to a local async def, so the check runs on a coroutine object, not its value", "low", "J1"),
    "C57": ("assertion compares against an unconfigured Mock attribute — the expected side is m.attr on a bare Mock()/MagicMock() with no spec, which auto-creates a fresh truthy Mock", "low", "J3"),
    "C59": ("bare top-level comparison expression — result == expected written as a statement, the value is computed and discarded so nothing is asserted (loose-statement sibling of C39)", "high", "J1"),
    "CC":  ("commented-out assert (check switched off)", "low", "J1"),
    # --- diagnostic group (readability / observability; default off) ----------
    "D1":  ("multiple assertions without messages (assertion roulette)", "off", "J4"),
    "D3":  ("identical assertion repeated in the same test (duplicate assert)", "off", "J4"),
    "D4":  ("@pytest.mark.parametrize without ids= — failing case identified only by index", "off", "J4"),
    "D5":  ("too many inline setup statements before first assert — consider extracting a fixture", "off", "J5"),
    "D6":  ("print() in test body — debug artifact that bypasses the test oracle", "off", "J4"),
    # --- coupling group (fragility / maintainability; default off) ------------
    "M2":  ("test method body exceeds the configured line-count threshold", "off", "J5"),
    # --- project layer (config-audit only; emitted by --config-audit, never by
    #     the per-file scan). The suite goes green by configuration, not by a
    #     smell inside any one test file. ---------------------------------------
    "PL1": ("python -O / PYTHONOPTIMIZE strips every assert at runtime - the whole suite passes with no checks", "low", "J1"),
    "PL2": ("filterwarnings does not promote warnings to errors - deprecations and runtime warnings pass silently", "low", "J1"),
    "PL7": ("no coverage gate (--cov-fail-under / fail_under) - coverage can fall to zero and the suite still passes", "low", "J5"),
    "PL8": ("addopts stops the run early (-x / --maxfail / --exitfirst) - the reported test count is incomplete", "low", "J5"),
}

def group_of(code):
    """Smell category inferred from code prefix: 'false-positive' | 'diagnostic' | 'coupling' | 'project'."""
    if code.startswith("PL"):
        return "project"
    if code.startswith("D"):
        return "diagnostic"
    if code.startswith("M"):
        return "coupling"
    return "false-positive"


# One-line remediation per case: what to change so the test protects something.
# Short, imperative, no trailing period. Surfaced in the status report (text +
# JSON `fix` field). A code missing here renders no fix line, never crashes.
FIX_HINTS = {
    "C1":  "move the assertion out of the conditional so it always runs",
    "C2":  "add an assertion that checks the behaviour under test",
    "C2b": "assert the result of the call, not just that it ran",
    "C2c": "put an assertion inside the subTest block so each sub-case verifies something",
    "C3":  "narrow or remove the except so the assertion error propagates",
    "C4":  "rename to test_* (or register it) so pytest collects it",
    "C4b": "drop __init__ from the test class; use fixtures for setup",
    "C5":  "assert the real behaviour, not a constant or tautology",
    "C6":  "assert the value, not just that something came back",
    "C6b": "assert by keyword/field, not by positional argument order",
    "C6c": "assert the expected call_count (== N), not just its truthiness",
    "C7":  "compare against an independent expected value, not the subject itself",
    "C8":  "use pytest.approx() or a tolerance instead of exact float equality",
    "C8b": "pass an explicit tolerance (places=/delta=, or rel=/abs= on approx) sized to the values",
    "C9":  "match the specific exception type, not a broad base class",
    "C11a":"compare against an oracle the test does not compute itself",
    "C13": "use a real mock assertion (assert_called_once_with, ...)",
    "C13b":"patch with autospec=True so wrong attribute/call names fail",
    "C14": "write the expected value by hand; don't snapshot the code's own output",
    "C16": "freeze time / seed randomness so the result is deterministic",
    "C17": "remove the skip from the except, or narrow it, so failures surface",
    "C18": "assert the value, not its str()/repr() formatting",
    "C19": "wrap only the single call expected to raise in pytest.raises",
    "C20": "move the assertion before the return/raise so it executes",
    "C21": "add at least one assertion that runs unconditionally",
    "C22": "await the unit before asserting in the async test",
    "C23": "use a fixture or tmp_path instead of a real file at a literal path",
    "C24": "reset the global in a fixture, or drop the module-level mutable state",
    "C25": "add strict=True to xfail so an unexpected pass fails the suite",
    "C27": "assert the exception is raised with pytest.raises; don't pass on it",
    "C28": "assert on the bound exception (excinfo.value) content",
    "C29": "set env via monkeypatch.setenv so it is restored after the test",
    "C30": "activate the interceptor (@responses.activate or its context manager)",
    "C31": "assert on the captured readouterr() output",
    "C32": "add reason= to the skip marker to document why",
    "C33": "assert the metric against a threshold",
    "C34": "use the simpler, more idiomatic assert form",
    "C35": "fix the flaky cause instead of retrying the test",
    "C36": "give pytest.fail() a message explaining the failure",
    "C37": "remove the duplicate parametrize case",
    "C38": "rename one of the duplicate tests so both run",
    "C39": "replace return with assert so pytest checks the comparison",
    "C41": "assert the resulting state, not the in-place method's None return",
    "C42": "evaluate the generator/lambda, or assert a concrete value",
    "C43": "remove the mid-test skip, or move it to a decorator at the top",
    "C44": "compare against a meaningful bound, not an always-true one",
    "C45": "add at least one case to the parametrize list",
    "C48": "assert the behaviour a real user hits; do not force the product's test-mode branch from the test",
    "C49": "wrap only the single call expected to warn in pytest.warns",
    "C50": "assert on the captured caplog records / log output",
    "C51": "make the call that should raise inside the pytest.raises block",
    "C52": "compare against an independent collection, not one built from the subject",
    "C55": "compare a mock-rooted value against a concrete expected value",
    "C56": "await the async call before asserting (or run it via asyncio.run)",
    "C57": "construct the mock with spec=, or compare against a concrete expected value",
    "C59": "replace the bare comparison with assert so pytest checks it",
    "CC":  "restore the commented-out assertion, or delete it",
    "D1":  "add an assertion message to each assert",
    "D3":  "remove the duplicate assertion",
    "D4":  "add ids= to parametrize for readable case names",
    "D5":  "extract the repeated setup into a fixture",
    "D6":  "replace print() with an assertion, or remove it",
    "M2":  "split the long test into focused cases",
    "PL1": "run pytest without -O/-OO and unset PYTHONOPTIMIZE so asserts execute",
    "PL2": "set filterwarnings = error in the pytest config so warnings fail the suite",
    "PL7": "add --cov-fail-under=<N> (or [tool.coverage.report] fail_under) to gate coverage",
    "PL8": "drop -x/--maxfail from addopts so the full suite runs and the count is complete",
}


# Real mock API assertion methods.
MOCK_ASSERTS_VALID = {
    "assert_called", "assert_called_once", "assert_called_with",
    "assert_called_once_with", "assert_any_call", "assert_has_calls",
    "assert_not_called",
}
XUNIT_ASSERT_TRUE = {"assertTrue"}
XUNIT_ASSERT_EQUAL = {"assertEqual"}
XUNIT_ASSERT_RAISES = {"assertRaises", "assertRaisesRegex", "assertRaisesRegexp"}
XUNIT_ASSERTS_VALID = {
    "assertTrue", "assertFalse", "assertEqual", "assertNotEqual",
    "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
    "assertIn", "assertNotIn", "assertIsInstance", "assertNotIsInstance",
    "assertAlmostEqual", "assertNotAlmostEqual", "assertGreater",
    "assertGreaterEqual", "assertLess", "assertLessEqual",
    "assertRegex", "assertNotRegex", "assertRaises", "assertRaisesRegex",
    "assertRaisesRegexp",
}
# pytest_check soft-assert API: check.equal(...), check.is_true(...), etc. The dotted
# form check.<member> is required (mirrors MOCK_ASSERTS_VALID / XUNIT_ASSERTS_VALID).
# A local object named check calling an arbitrary method (check.run/frobnicate) or a
# bare check(thing) is NOT an oracle and must not be recognized (no L1 over-match).
PYTEST_CHECK_ASSERTS = frozenset({
    "equal", "not_equal", "is_true", "is_false", "is_none", "is_not_none",
    "is_in", "is_not_in", "is_instance", "is_not_instance", "greater",
    "greater_equal", "less", "less_equal", "almost_equal", "not_almost_equal",
    "between", "raises", "fail",
})
# Names that look like a mock assertion but do not exist (always pass).
MOCK_FALSE_NAMES = {
    "called_once", "called_once_with", "called_with",
    "assert_not_called_once", "assert_called_twice",
}
# Callables that produce a mock. A name assigned from one of these is a mock.
MOCK_FACTORIES = {
    "Mock", "MagicMock", "AsyncMock", "NonCallableMock", "NonCallableMagicMock",
    "create_autospec", "patch",
}
# Prefixes that mark a helper, not a forgotten test. Kept narrow so common test
# names (run_/do_/get_) are not silently exempted.
HELPER_PREFIXES = (
    "assert", "check", "verify", "ensure", "make", "build",
    "setup", "teardown", "helper", "fixture", "_",
)

# Fluent assertion library entry points that count as an assertion in test bodies.
# assertpy assert_that(x)... is already caught by the startswith("assert") rule.
# expects / ward: expect(x).to(equal(y)) or expect(x).to.equal(y)
# sure: x.should.equal(y) — the .should attribute access is detected separately.
FLUENT_ASSERT_CALLS = {"expect"}

# Libraries that control the system clock in tests. When a file imports one of
# these, datetime.now() / time.time() calls are not non-deterministic — the
# test has frozen time, so C16 clock-read findings are suppressed.
TIME_CONTROL_IMPORTS = {"freezegun", "time_machine"}

# Methods that register mock HTTP responses. Calling any of these without first
# activating the library's interceptor (decorator or context manager) lets real
# HTTP calls reach the network — the mock is declared but never applied.
RESPONSES_SETUP_CALLS = {
    "responses.add", "responses.add_callback", "responses.add_passthrough",
    "httpretty.register_uri",
}

# sklearn / ML metric functions whose return value is the performance score.
# When the result is discarded or assigned but never asserted, there is no
# threshold check — the test passes regardless of the model's actual quality.
ML_METRIC_FUNCTIONS = {
    "accuracy_score", "balanced_accuracy_score",
    "f1_score", "precision_score", "recall_score",
    "roc_auc_score", "average_precision_score",
    "log_loss", "matthews_corrcoef",
    "mean_squared_error", "mean_absolute_error",
    "mean_absolute_percentage_error", "r2_score",
    "confusion_matrix",
}
# Estimator methods that return a scalar performance score.
ML_SCORE_METHODS = {"score"}

# PyTorch functions that produce random tensors. Without torch.manual_seed()
# (or torch.use_deterministic_algorithms(True)) somewhere in the test, the
# output changes between runs, making the test non-deterministic (C16).
TORCH_RANDOM_CALLS = {
    "rand", "randn", "randint", "randperm", "bernoulli",
    "multinomial", "normal", "poisson", "exponential",
}
# TensorFlow random ops. Without tf.random.set_seed() the graph-level seed is
# unset and results differ across runs.
TF_RANDOM_CALLS = {
    "normal", "uniform", "shuffle", "categorical",
    "truncated_normal", "stateless_normal", "stateless_uniform",
}

MUTABLE_GLOBAL_FACTORIES = {
    "list", "dict", "set", "Counter", "collections.Counter",
    "defaultdict", "collections.defaultdict",
}
MUTATING_METHODS = {
    "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse",
    "update", "add", "discard", "setdefault",
}
# In-place mutator methods that return None on the built-in containers (C41).
# An assertion that the call's None return is None is trivially green: the call
# mutates in place and yields None, so `assert not lst.sort()` /
# `assertIsNone(lst.sort())` verify the None, not the resulting state. Kept STRICT
# to these names so value-returning methods (pop, get, union, ...) never trip the
# detector, and the receiver must be a provably built-in container (see
# builtin_container_names) so a custom object's same-named method is not flagged.
NONE_RETURNING_MUTATORS = {
    "sort", "append", "extend", "reverse", "update", "add", "remove",
    "insert", "clear",
}
TIMEOUT_KEYWORDS = {"timeout", "segment_timeout"}
# Blocking concurrency waits that take a timeout= kwarg. `get` is deliberately excluded:
# it collides with the very common requests.get(timeout=)/cache.get(timeout=) HTTP/cache
# form, which is the recommended pattern, not a concurrency wait (false positive).
CONCURRENCY_TIMEOUT_CALLS = {
    "join", "wait", "wait_for", "sleep", "result",
}

# Decorator leaf names that indicate a retry/repeat loop. These make a test
# pass on the Nth attempt and report green, masking a flaky SUT instead of
# fixing the root cause.
RETRY_MARKER_NAMES = {"flaky", "repeat", "retry", "rerun", "flake"}

# C48 (dark-patch): a test that flips a known test-mode toggle to a test-mode value
# and then asserts is exercising the product's test-only branch (`if TESTING: ...`),
# not the behaviour a user actually hits. v1 detects RAW writes only.
# Env-var keys (os.environ["<KEY>"] = <truthy>) whose name unambiguously means
# "we are under test". Config/feature-flag names (DATABASE_URL, FEATURE_X) are out.
ENV_TEST_MODE_KEYS = {
    "TESTING", "TEST", "TEST_MODE", "TESTMODE", "UNDER_TEST", "PYTEST_RUNNING",
    "IS_TEST", "RUNNING_TESTS", "DJANGO_TEST", "_TEST", "PYTEST_CURRENT_TEST",
}
# Module/settings flag names (TESTING = True with `global`, or settings.TESTING = True).
MODULE_TEST_MODE_RE = re.compile(r"^(TESTING|TEST_MODE|IS_TEST|UNDER_TEST|_TESTING)$")
# Values that put the flag into test mode. bool is checked before int (bool subclasses int).
TEST_MODE_TRUE_STRINGS = {"1", "true", "test", "yes", "on"}

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "build",
    "dist", "site-packages", ".eggs",
}

IGNORE_RE = re.compile(r"#\s*falsegreen:\s*ignore(?:\[([A-Za-z0-9, ]+)\])?")


# ---------------------------------------------------------------------------
# Config file ([tool.falsegreen] in pyproject.toml, or .falsegreen.toml)
# ---------------------------------------------------------------------------
SEVERITY_VALUES = {"high", "low", "info", "off"}
EMPTY_CONFIG = {"disable": set(), "exclude": [], "severity": {}, "long_test_threshold": 50,
                "inline_setup_threshold": 5}


def _read_toml(path):
    if _toml is None:
        return None
    try:
        with open(path, "rb") as fh:
            return _toml.load(fh)
    except Exception:
        return None


def _normalize_config(data):
    """Validate a raw [tool.falsegreen] mapping into the internal config shape."""
    if not data:
        return {"disable": set(), "exclude": [], "severity": {}, "long_test_threshold": 50}
    disable = {str(c) for c in (data.get("disable") or [])}
    exclude = [str(g) for g in (data.get("exclude") or [])]
    severity = {}
    for code, level in (data.get("severity") or {}).items():
        if isinstance(level, str) and level.lower() in SEVERITY_VALUES:
            severity[code] = level.lower()
        else:
            sys.stderr.write(
                "falsegreen: ignoring invalid severity %r for %s (use high|low|info|off)\n"
                % (level, code))
    long_test_threshold = 50
    raw_thresh = data.get("long_test_threshold")
    if raw_thresh is not None:
        try:
            long_test_threshold = int(raw_thresh)
        except (ValueError, TypeError):
            sys.stderr.write(
                "falsegreen: ignoring invalid long_test_threshold %r (must be an integer)\n"
                % raw_thresh)
    inline_setup_threshold = 5
    raw_setup = data.get("inline_setup_threshold")
    if raw_setup is not None:
        try:
            inline_setup_threshold = int(raw_setup)
        except (ValueError, TypeError):
            sys.stderr.write(
                "falsegreen: ignoring invalid inline_setup_threshold %r (must be an integer)\n"
                % raw_setup)
    return {"disable": disable, "exclude": exclude, "severity": severity,
            "long_test_threshold": long_test_threshold,
            "inline_setup_threshold": inline_setup_threshold}


def load_config(start=None, explicit=None):
    """Resolve config to {'disable': set, 'exclude': [globs], 'severity': {code: level}}.

    With `explicit`, read that file (a pyproject.toml is read under [tool.falsegreen];
    any other name is read as a flat table). Otherwise auto-discover in `start`
    (default cwd): prefer .falsegreen.toml (flat table) over pyproject.toml
    [tool.falsegreen]. A no-op (empty config) when no file is found or no TOML
    reader is available (Python 3.8 without tomli).
    """
    data = None
    if explicit:
        raw = _read_toml(explicit)
        if raw is not None:
            data = raw.get("tool", {}).get("falsegreen", {}) \
                if os.path.basename(explicit) == "pyproject.toml" else raw
    else:
        base = start or os.getcwd()
        fg = os.path.join(base, ".falsegreen.toml")
        pp = os.path.join(base, "pyproject.toml")
        if os.path.isfile(fg):
            data = _read_toml(fg)
        elif os.path.isfile(pp):
            raw = _read_toml(pp)
            if raw is not None:
                data = raw.get("tool", {}).get("falsegreen", {})
    return _normalize_config(data)


def effective_conf(code, config=None, cli_disable=None):
    """A code's effective confidence: 'high' | 'low' | 'off'.

    Precedence: CLI --disable > config (disable/severity) > catalog default.
    Inline `# falsegreen: ignore` is applied per line earlier, in analyze_file.
    """
    if cli_disable and code in cli_disable:
        return "off"
    if config:
        if code in config.get("disable", ()):
            return "off"
        sev = config.get("severity", {})
        if code in sev:
            return sev[code]
    return CASES[code][1]


def _apply_exclude(files, globs):
    """Drop files matching any exclude glob (matched against the relative path,
    the forward-slash full path, and the basename)."""
    if not globs:
        return files
    kept = []
    for f in files:
        full = f.replace("\\", "/")
        try:
            rel = os.path.relpath(f).replace("\\", "/")
        except ValueError:  # different drive on Windows
            rel = full
        base = os.path.basename(full)
        if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(full, g)
               or fnmatch.fnmatch(base, g) for g in globs):
            continue
        kept.append(f)
    return kept


# ---------------------------------------------------------------------------
# Layer detection: which layer does a test target? Surfaced as metadata on every
# finding (triage by layer), and used to SOFTEN two codes that misfire on web/UI
# tests (C6, C14 - see issue #20). It only ever removes a finding; it never adds
# one or raises confidence, so it trades false positives away without buying new
# ones (precision over recall).
# ---------------------------------------------------------------------------
WEB_IMPORT_ROOTS = {
    "django", "flask", "fastapi", "starlette", "rest_framework",
    "httpx", "requests", "webtest", "werkzeug", "aiohttp",
    # HTTP mock libraries: a test that intercepts HTTP calls targets the web layer
    "responses", "httpretty", "respx", "aioresponses", "vcr",
    "requests_mock", "pook", "pytest_httpserver",
}
BROWSER_IMPORT_ROOTS = {
    "selenium", "playwright", "splinter", "pytest_playwright",
    "helium", "pyppeteer", "seleniumbase",
}
# Real datastore drivers/ORMs. A test importing one of these crosses the I/O
# boundary to a database, which is an integration test (the row IS the oracle).
INTEGRATION_DB_ROOTS = {
    "sqlalchemy", "sqlmodel", "psycopg", "psycopg2", "asyncpg", "aiomysql",
    "pymysql", "mysql", "cx_Oracle", "oracledb", "pymongo", "mongoengine",
    "motor", "redis", "alembic", "databases", "tortoise", "peewee", "pony",
    "testcontainers", "pytest_postgresql", "pytest_mysql", "fakeredis",
}


# Real HTTP clients and web frameworks: a test importing one talks to an actual
# (or test-client) API boundary, which is an integration test. This is a strict
# subset of WEB_IMPORT_ROOTS - the HTTP mock/record/replay libraries there
# (responses, httpretty, respx, aioresponses, vcr, requests_mock, pook,
# pytest_httpserver) are deliberately excluded: stubbing the boundary keeps the
# test at unit level, so they must not raise the pyramid level to integration.
WEB_CLIENT_LEVEL_ROOTS = {
    "django", "flask", "fastapi", "starlette", "rest_framework",
    "httpx", "requests", "webtest", "werkzeug", "aiohttp",
}


def detect_pyramid_level(tree):
    """Map the test file to a pyramid level from its import roots: 'e2e' (browser
    driver), 'integration' (real web client or database driver: API and DB tests),
    or 'unit' (neither). Broadest wins. A real API/DB import in a test the author
    treats as a unit test is itself the smell, surfaced by the level mismatch.
    HTTP mock libraries do not count as integration (they stub the boundary)."""
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    if roots & BROWSER_IMPORT_ROOTS:
        return "e2e"
    if roots & (WEB_CLIENT_LEVEL_ROOTS | INTEGRATION_DB_ROOTS):
        return "integration"
    return "unit"


def detect_file_layer(tree):
    """'browser' | 'web' | 'logic' from the file's import roots (broadest wins)."""
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    if roots & BROWSER_IMPORT_ROOTS:
        return "browser"
    if roots & WEB_IMPORT_ROOTS:
        return "web"
    return "logic"


# Fixture/parameter and object names that mark a test as targeting a web client
# or a browser. Used to refine the file layer per function: a single test that
# takes a `client` or `page` fixture targets that layer even in a logic file.
WEB_CTX_NAMES = {"client", "test_client", "async_client", "api_client",
                 "live_server", "flask_client", "testapp", "webapp"}
BROWSER_CTX_NAMES = {"page", "browser", "driver", "selenium", "live_browser"}
# Element/locator visibility predicates (Playwright/Selenium) - genuine booleans.
BROWSER_PRESENCE_METHODS = {"is_visible", "is_enabled", "is_checked",
    "is_displayed", "is_selected", "is_clickable", "is_editable", "is_hidden"}
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
# Object roots whose truthiness IS a presence assertion at the web/UI layer.
WEB_OPERAND_ROOTS = {"response", "resp", "page", "locator", "element", "client",
                     "request", "soup", "widget"}


def detect_test_context(func, file_layer):
    """The layer a single test targets: start from the file layer, then add
    `web`/`browser` from this function's fixture/parameter names and call shapes
    (`client.get(...)`, `page.locator(...)`, `.is_visible()`). A set, because a
    test can touch more than one. Used only to soften C6/C14 (issue #20)."""
    ctx = {file_layer}
    params = {a.arg for a in func.args.args}
    params |= {a.arg for a in getattr(func.args, "kwonlyargs", []) or []}
    params |= {a.arg for a in getattr(func.args, "posonlyargs", []) or []}
    if params & BROWSER_CTX_NAMES:
        ctx.add("browser")
    if params & WEB_CTX_NAMES:
        ctx.add("web")
    for n in ast.walk(func):
        if isinstance(n, ast.Call):
            name = dotted_name(n.func)
            root, last = name.split(".")[0], name.split(".")[-1]
            if (root in BROWSER_CTX_NAMES or last in BROWSER_PRESENCE_METHODS
                    or last == "locator"):
                ctx.add("browser")
            elif root in WEB_CTX_NAMES and last in HTTP_METHODS:
                ctx.add("web")
    return ctx


def file_controls_time(tree):
    """True if the file imports a time-control library (freezegun or time-machine).
    When time is externally frozen, datetime.now()/time.time() calls inside tests
    are not non-deterministic, so C16 clock-read findings are suppressed."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in TIME_CONTROL_IMPORTS:
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in TIME_CONTROL_IMPORTS:
                return True
    return False


def file_imports_sklearn(tree):
    """True if the file imports sklearn. C33 flags a bare `.score()` only here:
    `.score()` is a generic method name (a game, a TfidfVectorizer, a domain
    object all expose one). Without a sklearn import in scope, calling it a
    'sklearn metric' is a false positive. The named free functions
    (accuracy_score, f1_score, ...) are unambiguous and stay ungated."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "sklearn":
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "sklearn":
                return True
    return False


def is_web_presence_operand(test):
    """A truthy operand that is the real assertion at the web/UI layer: an
    element visibility predicate (`locator.is_visible()`), an HTTP request
    (`client.get(...)`), or an object rooted in a response/page/locator/element.
    In web/browser context these are checks, not weak `something came back`."""
    if isinstance(test, ast.Call):
        name = dotted_name(test.func)
        root, last = name.split(".")[0], name.split(".")[-1]
        if last in BROWSER_PRESENCE_METHODS or last == "locator":
            return True
        if last in HTTP_METHODS and root in WEB_CTX_NAMES:
            return True
        return root in WEB_OPERAND_ROOTS
    if isinstance(test, (ast.Name, ast.Attribute, ast.Subscript)):
        parts = dotted_name(test).split(".")
        return bool(parts) and parts[0] in WEB_OPERAND_ROOTS
    return False


# ---------------------------------------------------------------------------
# Test file discovery
# ---------------------------------------------------------------------------
def is_test_file(path):
    name = os.path.basename(path)
    if not name.endswith(".py"):
        return False
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    parts = {p.lower() for p in path.replace("\\", "/").split("/")}
    return "tests" in parts or "test" in parts


def discover(paths):
    files = []
    for root in paths:
        if os.path.isfile(root):
            if root.endswith(".py"):
                files.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for f in filenames:
                full = os.path.join(dirpath, f)
                if is_test_file(full):
                    files.append(full)
    return sorted(set(files))


def staged_files():
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace")
    except Exception:
        return []
    res = []
    for line in out.splitlines():
        line = line.strip()
        if line and is_test_file(line) and os.path.isfile(line):
            res.append(line)
    return res


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------
def children_no_nesting(node):
    """Walk descendants without entering nested def/class/lambda bodies."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from children_no_nesting(child)


def dotted_name(node):
    """foo.bar.baz -> 'foo.bar.baz' for Attribute/Name; otherwise ''."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return (base + "." + node.attr) if base else node.attr
    return ""


def root_name(node):
    """Leftmost Name of an attribute/call chain. 'a.b.c()' -> 'a'."""
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break
    return node.id if isinstance(node, ast.Name) else ""


def is_call_to(node, *names):
    if not isinstance(node, ast.Call):
        return False
    target = dotted_name(node.func)
    return any(target == n or target.endswith("." + n) for n in names)


def is_pytest_skip_call(node):
    """A genuine pytest skip: `pytest.skip(...)`, a bare imported `skip(...)`, or
    `self.skipTest(...)`/`cls.skipTest(...)`. Excludes an arbitrary `obj.skip(...)`
    method (e.g. a reader/cursor) that `is_call_to(..., 'skip')` would over-match."""
    if not isinstance(node, ast.Call):
        return False
    if dotted_name(node.func) == "pytest.skip":
        return True
    if isinstance(node.func, ast.Name) and node.func.id == "skip":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "skipTest" \
            and isinstance(node.func.value, ast.Name) and node.func.value.id in ("self", "cls"):
        return True
    return False


def is_xunit_assert_call(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in XUNIT_ASSERTS_VALID:
        return False
    return isinstance(node.func.value, ast.Name) and node.func.value.id in ("self", "cls")


def is_xunit_raises_call(node):
    return is_xunit_assert_call(node) and node.func.attr in XUNIT_ASSERT_RAISES


def _chain_root_call(node):
    """The innermost Call at the head of a fluent chain. For
    `assert_that(x).is_equal_to(y)` the outer Call is `.is_equal_to(...)`; this
    returns the `assert_that(x)` Call (the recognized entry point). Walks down the
    receiver spine through Attribute/Call until the first non-chained Call."""
    if not isinstance(node, ast.Call):
        return None
    cur = node.func
    while isinstance(cur, ast.Attribute):
        cur = cur.value
        if isinstance(cur, ast.Call):
            inner = _chain_root_call(cur)
            return inner if inner is not None else cur
    return node


def call_is_recognized_assertion(call):
    """True if `call` is a recognized verification entry point, whether it is the
    outermost call or the ROOT of a fluent chain. Single source of truth for the
    assertion-recognition predicate so the per-statement and whole-function helpers
    cannot drift (the FP-1 asymmetry). Recognizes: bare assert-prefixed names,
    mock/xunit asserts, pytest.raises/fail, FLUENT_ASSERT_CALLS (expect), assert_that,
    and pytest_check soft asserts: the dotted check.<member> form only (check.equal,
    check.is_true, ...), never a bare check(...) or check.<other>()."""
    if not isinstance(call, ast.Call):
        return False
    for c in (call, _chain_root_call(call)):
        if c is None:
            continue
        target = dotted_name(c.func)
        last = target.split(".")[-1]
        if (
            last in MOCK_ASSERTS_VALID
            or last.startswith("assert")
            or target.endswith("raises")
            or last == "fail"
            or last in FLUENT_ASSERT_CALLS
            or (root_name(c) == "check" and last in PYTEST_CHECK_ASSERTS)
        ):
            return True
        if is_xunit_assert_call(c):
            return True
    return False


def almost_equal_without_tolerance(call):
    """xunit `self.assertAlmostEqual`/`assertNotAlmostEqual` with no explicit tolerance
    (C8b). places= defaults to 7 and hides a meaningfully wrong value. places can be the
    3rd positional arg, so a call with >= 3 args already sized it; otherwise require a
    places=/delta= keyword."""
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
        return False
    if call.func.attr not in ("assertAlmostEqual", "assertNotAlmostEqual"):
        return False
    if not (isinstance(call.func.value, ast.Name) and call.func.value.id in ("self", "cls")):
        return False
    if len(call.args) >= 3:
        return False  # places supplied positionally
    return not any(kw.arg in ("places", "delta") for kw in call.keywords)


def approx_without_tolerance(node):
    """A `pytest.approx(...)` / `approx(...)` call with no rel=/abs= keyword (C8b):
    the 1e-6 relative default lets a meaningfully wrong value pass."""
    if not isinstance(node, ast.Call):
        return False
    if dotted_name(node.func) not in ("pytest.approx", "approx"):
        return False
    return not any(kw.arg in ("rel", "abs") for kw in node.keywords)


def compare_has_untolerated_approx(test):
    """An `== pytest.approx(x)` / `!= approx(x)` comparison with no tolerance (C8b)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], (ast.Eq, ast.NotEq)):
        return False
    return approx_without_tolerance(test.left) or approx_without_tolerance(test.comparators[0])


def constant_truthy(node):
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if node.__class__.__name__ in ("Num", "Str", "Bytes", "NameConstant"):
        return bool(getattr(node, "n", getattr(node, "s", getattr(node, "value", False))))
    return False


def assert_always_true(test):
    if isinstance(test, ast.Tuple) and len(test.elts) > 0:
        return True
    if constant_truthy(test):
        return True
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(constant_truthy(v) for v in test.values)
    return False


def assert_always_truthy_object(test):
    """A generator expression or lambda is an object that is always truthy, so
    `assert (x for x in y)` / `assert lambda: ...` can never fail (C42). A list,
    set, or dict comprehension is NOT included: those can be empty, so the
    assertion is a real (if weak) check."""
    return isinstance(test, (ast.GeneratorExp, ast.Lambda))


def _is_builtin_container_literal(node):
    """True if node is a list/dict/set literal or a list/dict/set comprehension.
    These establish the value as a built-in container by direct local evidence."""
    return isinstance(node, (ast.List, ast.Dict, ast.Set,
                             ast.ListComp, ast.DictComp, ast.SetComp))


def builtin_container_names(func, before_lineno=None):
    """Names bound, in this function body, to a list/dict/set literal or
    comprehension. Used by C41 to prove a mutator receiver is a built-in container
    by LOCAL evidence; a name with no such binding is treated as unknown (and not
    flagged), since a custom object's add/update/clear may return a value.

    When `before_lineno` is given, only bindings that appear strictly before that
    line count: a container literal assigned AFTER the assertion does not prove the
    receiver was a container at the point the assertion runs, so it must not flag.

    Bindings inside nested scopes (a helper def/lambda/class in the test body) are
    not the test's local `name`, so they are not evidence about the asserted
    receiver: walk children only, skipping nested scopes."""
    names = set()
    for n in children_no_nesting(func):
        if before_lineno is not None and getattr(n, "lineno", 0) >= before_lineno:
            continue
        if isinstance(n, ast.Assign) and _is_builtin_container_literal(n.value):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(n, ast.AnnAssign) and n.value is not None \
                and _is_builtin_container_literal(n.value) \
                and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


def _receiver_is_builtin_container(receiver, container_names):
    """The mutator receiver is provably a built-in container by local evidence:
    either a literal container directly, or a plain name bound to one earlier in
    the same function body. Anything else (an arg, an attribute, a subscript, a
    name with no local binding) is unknown and must not be flagged."""
    if _is_builtin_container_literal(receiver):
        return True
    if isinstance(receiver, ast.Name):
        return receiver.id in container_names
    return False


def _is_none_returning_mutator_call(node, container_names):
    """True if node is `receiver.<m>(...)` where <m> is a known in-place mutator
    that returns None (sort/append/extend/reverse/update/add/remove/insert/clear)
    AND the receiver is a provably built-in container by local evidence. A custom
    object that happens to define one of these names (e.g. a registry whose add()
    returns a value) is NOT flagged."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in NONE_RETURNING_MUTATORS:
        return False
    return _receiver_is_builtin_container(node.func.value, container_names)


def assert_none_mutator(test, container_names):
    """The asserted expression is (trivially) the None return of an in-place
    mutator on a built-in container, so the assertion is always green (C41). The
    bare `assert lst.sort()` form is excluded: `assert None` FAILS (red), so it is
    not a false-green. Covers only the actually-green forms:
      assert not lst.sort()          -> not None == True, always green
      assert lst.append(x) is None   -> compares the None return to None, green
      assert lst.clear() == None     -> same via ==
    Returns True when the mutator call is the thing under (non-)check."""
    # `assert not lst.sort()`
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) \
            and _is_none_returning_mutator_call(test.operand, container_names):
        return True
    # `assert lst.append(x) is None` / `== None` (either side)
    if isinstance(test, ast.Compare) and len(test.ops) == 1 \
            and isinstance(test.ops[0], (ast.Is, ast.Eq)):
        left, right = test.left, test.comparators[0]
        if _is_none_returning_mutator_call(left, container_names) and _is_none_literal(right):
            return True
        if _is_none_returning_mutator_call(right, container_names) and _is_none_literal(left):
            return True
    return False


def _is_none_literal(node):
    return isinstance(node, ast.Constant) and node.value is None


def _int_const(node):
    """The integer value of a literal, including a unary minus; else None.
    `True`/`False` are excluded (they are ints in Python but not numeric here)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _int_const(node.operand)
        return None if v is None else -v
    return None


def _is_len_or_abs_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("len", "abs") and len(node.args) == 1)


def assert_numeric_tautology(test):
    """len()/abs() compared so the result is always true: `len(x) >= 0`,
    `0 <= len(x)`, `abs(x) >= 0`, `len(x) > -1`. len() and abs() are never
    negative, so the comparison holds for every input and verifies nothing (C44)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    op, left, right = test.ops[0], test.left, test.comparators[0]
    if _is_len_or_abs_call(left):
        if isinstance(op, ast.GtE) and _int_const(right) == 0:
            return True
        if isinstance(op, ast.Gt) and _int_const(right) == -1:
            return True
    if _is_len_or_abs_call(right):
        if isinstance(op, ast.LtE) and _int_const(left) == 0:
            return True
        if isinstance(op, ast.Lt) and _int_const(left) == -1:
            return True
    return False


def _is_mock_call_count(node, mock_names):
    """True if `node` is `<mock>.call_count` for a known mock receiver (#91)."""
    return isinstance(node, ast.Attribute) and node.attr == "call_count" \
        and root_name(node) in mock_names


def mock_call_count_smell(test, mock_names):
    """An assertion on a mock's call_count that verifies nothing (#91):
      - bare `assert m.call_count` is truthy on any count >= 1 (weak oracle, C6c);
      - `assert m.call_count >= 0` / `> -1` is a tautology, always true (C44).
    A real count check (`== N`, `>= 1`, `> 0`) stays quiet. Returns the code or None."""
    if _is_mock_call_count(test, mock_names):
        return "C6c"
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op, left, right = test.ops[0], test.left, test.comparators[0]
        if _is_mock_call_count(left, mock_names):
            if isinstance(op, ast.GtE) and _int_const(right) == 0:
                return "C44"
            if isinstance(op, ast.Gt) and _int_const(right) == -1:
                return "C44"
        if _is_mock_call_count(right, mock_names):
            if isinstance(op, ast.LtE) and _int_const(left) == 0:
                return "C44"
            if isinstance(op, ast.Lt) and _int_const(left) == -1:
                return "C44"
    return None


def assert_self_compare(test):
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        op = test.ops[0]
        if isinstance(op, (ast.Eq, ast.Is)):
            try:
                identical = ast.dump(test.left) == ast.dump(test.comparators[0])
            except Exception:
                return False
            if not identical:
                return False
            # `x is x` / `x == x` / `obj.attr == obj.attr` is always true. But a
            # call on each side is NOT a tautology: two separate calls may return
            # two distinct objects. `f() is f()` is the lru_cache / singleton
            # identity test; `cls(1) == cls(1)` / `Color("red") == Color("red")`
            # is the canonical __eq__ value-equality test (attrs, pydantic,
            # dataclasses). With default identity __eq__ those would FAIL, so a
            # green one is a real check. Only flag a self-compare with no call.
            if any(isinstance(n, ast.Call) for n in ast.walk(test.left)):
                return False
            return True
    return False


def in_equality_semantics_test(func, self_cmp):
    """True if the enclosing test exercises the same operand's equality/hashing.

    `assert x == x` / `assert x is x` is only a tautology in isolation. Beside a
    discriminating counterpart on the SAME operand it is the reflexive half of a
    deliberate __eq__/__hash__/identity test, not a bug. A counterpart is:
      - `x != peer` / `not (x == peer)` / `x is not peer`, where peer is a
        distinct object (another variable, or a non-trivial literal like `"foo"`
        - but NOT a sentinel like None/True/False/0/1, so `x != None` does not
        count);
      - membership in a literal holding x: `x in {x}` / `x in [x, ...]` (but not
        `x in some_registry`);
      - a companion `hash(x)` in the test, the __hash__ half of an eq/hash pair.
    Real examples: aiohttp `assert resp1 == resp1; assert not resp1 == resp2`;
    starlette `assert ws == ws; assert ws in {ws}`; scrapy `assert r.flags is
    r.flags; assert r.flags is not original`; attrs `assert i == i; assert
    hash(i) == hash(i)`; hypothesis `assert x == x; assert x != "foo"`. A lone
    `assert x == x`, or one merely next to `x != None`, stays C7."""
    try:
        operand = ast.dump(self_cmp.left)
    except Exception:
        return False

    # a distinct object is another variable-like operand (a sibling under test),
    # or a non-trivial literal. Sentinels (None/bool/0/1/"") do NOT count - a
    # `x != None` null-check is not an equality-semantics test.
    PEER = (ast.Name, ast.Attribute, ast.Subscript)

    def _trivial_const(node):
        if not isinstance(node, ast.Constant):
            return False
        v = node.value
        return v is None or isinstance(v, bool) or v == "" \
            or (isinstance(v, (int, float)) and v in (0, 1))

    def _has_distinct(parts):
        mentions = distinct = False
        for p in parts:
            try:
                same = ast.dump(p) == operand
            except Exception:
                continue
            if same:
                mentions = True
            elif isinstance(p, PEER) or (isinstance(p, ast.Constant)
                                         and not _trivial_const(p)):
                distinct = True
        return mentions and distinct

    def _literal_holds_operand(container):
        if isinstance(container, (ast.Set, ast.List, ast.Tuple)):
            for e in container.elts:
                try:
                    if ast.dump(e) == operand:
                        return True
                except Exception:
                    continue
        return False

    for n in ast.walk(func):
        if isinstance(n, ast.Compare) and n is not self_cmp:
            # x != peer / x is not peer: a discriminating check against a
            # distinct object (inequality or non-identity).
            if any(isinstance(o, (ast.NotEq, ast.IsNot)) for o in n.ops):
                if _has_distinct([n.left, *n.comparators]):
                    return True
            # x in {x} / x in [x, ...]: membership in a literal holding x, which
            # exercises __eq__/__hash__. `x in some_registry` does NOT qualify.
            try:
                left_is_operand = ast.dump(n.left) == operand
            except Exception:
                left_is_operand = False
            if left_is_operand:
                for op, comp in zip(n.ops, n.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) \
                            and _literal_holds_operand(comp):
                        return True
        # not (x == peer)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            inner = n.operand
            if isinstance(inner, ast.Compare) and any(
                isinstance(o, ast.Eq) for o in inner.ops
            ):
                if _has_distinct([inner.left, *inner.comparators]):
                    return True
        # hash(x) anywhere in the test: the __hash__ half of an eq/hash pair.
        if isinstance(n, ast.Call) and dotted_name(n.func).split(".")[-1] == "hash":
            for a in n.args:
                try:
                    if ast.dump(a) == operand:
                        return True
                except Exception:
                    continue
    return False


def _const_value(node):
    if isinstance(node, ast.Constant):
        return node.value
    if node.__class__.__name__ == "Num":
        return getattr(node, "n", None)
    return None


# Builtins / methods that return a genuine bool. Asserting one of these IS the
# expected-result check (a type/shape/predicate), not a weak "something came
# back". Only applies to a CALL (the predicate is actually invoked) - a bare
# attribute like `assert path.exists` (missing parens) stays weak on purpose.
BOOL_PREDICATE_CALLS = {
    "isinstance", "issubclass", "callable", "hasattr", "any", "all",
    "exists", "is_file", "is_dir", "isfile", "isdir", "is_absolute",
    "is_symlink", "is_mount", "samefile",
    "startswith", "endswith",
    "isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower",
    "istitle", "isidentifier", "isnumeric", "isdecimal", "isprintable",
    "is_integer", "issubset", "issuperset", "isdisjoint",
}
# Heuristic is name-based, not return-type based (the AST has no types): a method
# `is_recommended_value()` returning a non-bool would be wrongly exempted. The
# convention is strong enough that the recall give-back is worth killing the
# isinstance/exists/any false positives; the semantic pass is the type backstop.
BOOL_PREDICATE_PREFIX = re.compile(
    r"^(is|has|can|should|was|were|are|did|does|will)_"
)


def assert_weak(test, ctx=()):
    # In a web/browser test, the truthiness of a locator/element/response IS the
    # expected-result check (element is present, request succeeded), not a weak
    # "something came back". Soften only there, only for that operand shape.
    if ("web" in ctx or "browser" in ctx) and is_web_presence_operand(test):
        return None
    # Truthiness of something: assert result / assert obj.attr / assert f()
    if isinstance(test, ast.Call):
        fname = dotted_name(test.func).split(".")[-1]
        if fname in BOOL_PREDICATE_CALLS or BOOL_PREDICATE_PREFIX.match(fname):
            return None  # genuine boolean predicate, a real expected-result check
        return "truthiness of a value, not compared to an expected result"
    if isinstance(test, (ast.Name, ast.Attribute, ast.Subscript)):
        return "truthiness of a value, not compared to an expected result"
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        right = test.comparators[0]
        # "x" in str(...): substring search in stringified output
        if isinstance(op, ast.In):
            if isinstance(right, ast.Call) and dotted_name(right.func).split(".")[-1] in ("str", "repr", "format"):
                return "substring search in stringified output, not the exact content"
            return None
        # len(x) > 0 / >= 1 / != 0 (only-not-empty). NOT len(x) == N (exact count, good).
        if isinstance(test.left, ast.Call) and dotted_name(test.left.func).endswith("len"):
            rv = _const_value(right)
            if (isinstance(op, ast.Gt) and rv == 0) or (isinstance(op, ast.GtE) and rv == 1) \
                    or (isinstance(op, ast.NotEq) and rv == 0):
                return "only checks it is not empty"
    return None


def _exact_safe_float(v):
    """0.0 and 1.0 are exactly representable and are the usual all/none ratio
    sentinels (0/N, N/N), so exact == on them is not the rounding smell C8 warns
    about. Fractional literals like 0.1, 0.3, 2.5 still are."""
    return v in (0.0, 1.0)


def assert_exact_float(test):
    if isinstance(test, ast.Compare) and any(isinstance(o, ast.Eq) for o in test.ops):
        sides = [test.left] + list(test.comparators)
        for side in sides:
            if isinstance(side, ast.Constant) and isinstance(side.value, float):
                if not _exact_safe_float(side.value):
                    return True
            elif side.__class__.__name__ == "Num" and isinstance(getattr(side, "n", None), float):
                if not _exact_safe_float(side.n):
                    return True
    return False


def _is_string_literal(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return node.__class__.__name__ in ("Str",)


def _is_stringify(node):
    """A str()/repr()/format() call or an f-string: turning a value into text."""
    if isinstance(node, ast.JoinedStr):  # an f-string
        return True
    if isinstance(node, ast.Call):
        return dotted_name(node.func).split(".")[-1] in ("str", "repr", "format")
    return False


def assert_sensitive_equality(test):
    """assert str(x) == "..." / repr(x) == "..." / f"{x}" == "..." checks the
    formatting of x, not its value. A repr change breaks the test with no real
    defect, and a value bug can hide behind matching text. (Sensitive Equality)."""
    if isinstance(test, ast.Compare) and len(test.comparators) == 1 \
            and isinstance(test.ops[0], ast.Eq):
        left, right = test.left, test.comparators[0]
        if (_is_stringify(left) and _is_string_literal(right)) \
                or (_is_stringify(right) and _is_string_literal(left)):
            return True
    return False


def _compare_node(left, op, right):
    return ast.Compare(left=left, ops=[op], comparators=[right])


def _literal_value(node):
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, bytes, int, float, complex, bool, type(None))
    ):
        return node.value
    return None


def _same_literal(left, right):
    return _literal_value(left) == _literal_value(right) \
        and _literal_value(left) is not None


def _literal_collection_key(node):
    """A hashable, value-based key for a fully literal AST node, or None if any
    part is dynamic (a Name, a call, an f-string, ...). Recurses into list/tuple/
    set/dict literals so a nested literal argset still normalizes; type is part of
    the key (so int 1 and float 1.0 do not collide, and (1,) != [1])."""
    lit = _literal_value(node)
    if lit is not None or isinstance(node, ast.Constant):
        return ("lit", type(lit).__name__, lit)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        parts = []
        for e in node.elts:
            k = _literal_collection_key(e)
            if k is None:
                return None
            parts.append(k)
        return (type(node).__name__, tuple(parts))
    if isinstance(node, ast.Dict):
        items = []
        for kk, vv in zip(node.keys, node.values):
            if kk is None:  # dict unpacking {**x}
                return None
            kkey = _literal_collection_key(kk)
            vkey = _literal_collection_key(vv)
            if kkey is None or vkey is None:
                return None
            items.append((kkey, vkey))
        return ("Dict", frozenset(items))
    return None


def _parametrize_literal_key(case):
    """A value key for one @pytest.mark.parametrize case, or None if it is not
    provably a literal duplicate (so C37 must skip it). Handles a bare value, a
    tuple/list argset, and a pytest.param(...) wrapper. Returns None when any
    value is dynamic OR when an id= is present but not a literal string (a dynamic
    id makes pytest label the cases distinctly, so they are not a duplicate)."""
    if isinstance(case, ast.Call) and dotted_name(case.func).split(".")[-1] == "param":
        for kw in case.keywords:
            if kw.arg == "id" and _literal_value(kw.value) is None:
                return None  # dynamic id: pytest names the cases apart
        parts = []
        for a in case.args:
            k = _literal_collection_key(a)
            if k is None:
                return None
            parts.append(k)
        return ("param", tuple(parts))
    return _literal_collection_key(case)


def _slice_value(node):
    # Python 3.8 wraps subscript slices in ast.Index; unwrap only that node.
    if type(node).__name__ == "Index":
        return node.value
    return node


def _subscript_base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _subscript_base_name(node.value)
        return (base + "." + node.attr) if base else node.attr
    if isinstance(node, ast.Subscript):
        return _subscript_base_name(node.value)
    return ""


def _contains_name(node, names):
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _index_names_from_index_calls(func):
    names = set()
    for n in children_no_nesting(func):
        if not isinstance(n, ast.Assign):
            continue
        if not isinstance(n.value, ast.Call):
            continue
        if dotted_name(n.value.func).split(".")[-1] != "index":
            continue
        for target in n.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def assert_arg_order_coupled(test, index_names):
    """LOW C6b: assertion depends on computed/index-derived position in args."""
    for n in ast.walk(test):
        if not isinstance(n, ast.Subscript):
            continue
        base = _subscript_base_name(n.value)
        if not ("args" in base.split(".") or "call_args" in base.split(".")):
            continue
        sl = _slice_value(n.slice)
        computed = isinstance(sl, (ast.BinOp, ast.UnaryOp, ast.Call)) \
            or (isinstance(sl, ast.Name) and sl.id in index_names) \
            or _contains_name(sl, index_names)
        if computed:
            return True
    return False


def c16_call_detail(call, has_seed, controls_time, nonce_random_calls=()):
    target = dotted_name(call.func)
    last = target.split(".")[-1]
    # Anchored to real blocking/wall-clock sleep entry points: time.sleep, the async
    # sleeps (asyncio/anyio/trio, same flakiness class), and a bare imported sleep
    # (`from time import sleep`). The excluded case is an arbitrary-receiver obj.sleep():
    # a page-object op.sleep(3) or a coroutine yield gen.sleep(0) is not a fixed test
    # delay (L1: no bare leaf-match on "sleep").
    if target in ("time.sleep", "asyncio.sleep", "anyio.sleep", "trio.sleep", "sleep"):
        return "fixed sleep"
    if not controls_time and (
        target.endswith("datetime.now") or target.endswith("datetime.today")
        or target.endswith("date.today") or target.endswith("time.time")
    ):
        return "reads the system clock"
    if (target.startswith("random.") or target.endswith("randint")
            or target.endswith("choice") or target.endswith("shuffle")) and not has_seed:
        # A random.* result stringified into a name that is membership-asserted
        # (assert nonce in log / .contains(nonce)) reads as a fresh unique id, not a
        # value to reproduce. Suppress that call regardless of its other uses.
        if call not in nonce_random_calls:
            return "randomness without a fixed seed"
    # uuid.uuid4()/uuid1()/getnode() and secrets.* produce a fresh value each run
    # with no seed concept (sibling of the JS crypto.randomUUID/getRandomValues).
    # Module-qualified only (precision-first): a user method named uuid4() or a bare
    # `from uuid import uuid4` call is not flagged, mirroring the JS FP-averse stance.
    if target in ("uuid.uuid4", "uuid.uuid1", "uuid.getnode"):
        return "uuid is non-deterministic between runs (no seed)"
    if target.startswith("secrets.") and (
            last.startswith("token_") or last in ("randbits", "choice")):
        return "secrets randomness is non-deterministic between runs (no seed)"
    if target.endswith("train_test_split") \
            and not any(kw.arg == "random_state" for kw in call.keywords):
        return "train_test_split without random_state - non-deterministic split"
    if target.startswith("torch.") and last in TORCH_RANDOM_CALLS and not has_seed:
        return "PyTorch randomness without torch.manual_seed"
    if target.startswith("tf.random.") and last in TF_RANDOM_CALLS and not has_seed:
        return "TensorFlow randomness without tf.random.set_seed"
    if last in CONCURRENCY_TIMEOUT_CALLS:
        for kw in call.keywords:
            if kw.arg in TIMEOUT_KEYWORDS and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, (int, float)):
                return "fixed timeout in concurrent wait"
    for kw in call.keywords:
        if kw.arg == "segment_timeout" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, (int, float)):
            return "fixed production timeout"
    return None


def _random_nonce_calls(func):
    """random.* Call nodes whose result is stringified (str(...) or an f-string), bound
    to a name, and that name is later asserted via membership (`name in x` or
    `x.contains(name)` / `.is_in(...)`). Such a value reads as a fresh unique nonce,
    so the call is suppressed for C16 regardless of its other uses (FP-3)."""
    # names bound from str(random.*) / f-string wrapping a random.* call -> the call node
    nonce_names = {}
    for n in children_no_nesting(func):
        if not isinstance(n, ast.Assign) or len(n.targets) != 1:
            continue
        tgt = n.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        rand_call = None
        val = n.value
        wrapped = (isinstance(val, ast.Call) and dotted_name(val.func) == "str")             or isinstance(val, ast.JoinedStr)
        if not wrapped:
            continue
        for sub in ast.walk(val):
            if isinstance(sub, ast.Call) and dotted_name(sub.func).startswith("random."):
                rand_call = sub
                break
        if rand_call is not None:
            nonce_names[tgt.id] = rand_call
    if not nonce_names:
        return set()
    # which of those names are read under a membership/contains assertion
    asserted_membership = set()
    for n in children_no_nesting(func):
        if isinstance(n, ast.Compare) and any(
                isinstance(op, (ast.In, ast.NotIn)) for op in n.ops):
            if isinstance(n.left, ast.Name) and n.left.id in nonce_names:
                asserted_membership.add(n.left.id)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)                 and n.func.attr in ("contains", "is_in", "assertIn"):
            for a in list(n.args) + [a.value for a in n.keywords]:
                if isinstance(a, ast.Name) and a.id in nonce_names:
                    asserted_membership.add(a.id)
    return {nonce_names[name] for name in asserted_membership}


def helper_c16_detail(func, controls_time):
    has_seed = any(
        is_call_to(c, "random.seed", "seed", "np.random.seed",
                   "torch.manual_seed", "manual_seed",
                   "tf.random.set_seed", "set_seed")
        for c in ast.walk(func) if isinstance(c, ast.Call)
    )
    _nonce = _random_nonce_calls(func)
    for n in children_no_nesting(func):
        if isinstance(n, ast.Call):
            detail = c16_call_detail(n, has_seed, controls_time, _nonce)
            if detail:
                return detail
    return None


def c11a_findings(func):
    """Return (line, detail) for self-confirming literals in top-level asserts."""
    local_names = set()
    assigned_literals = {}
    findings = []

    def _record_attr(target, value):
        if not isinstance(target, ast.Attribute):
            return
        root = root_name(target)
        if root in local_names:
            literal = _literal_value(value)
            if literal is not None:
                assigned_literals[ast.dump(target)] = literal

    for stmt in func.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)
                    if isinstance(stmt.value, ast.Call):
                        for kw in stmt.value.keywords:
                            if kw.arg and _literal_value(kw.value) is not None:
                                attr = ast.Attribute(
                                    value=ast.Name(id=target.id, ctx=ast.Load()),
                                    attr=kw.arg,
                                    ctx=ast.Load(),
                                )
                                assigned_literals[ast.dump(attr)] = _literal_value(kw.value)
                _record_attr(target, stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name):
                local_names.add(stmt.target.id)
            if stmt.value is not None:
                _record_attr(stmt.target, stmt.value)
        elif isinstance(stmt, ast.Assert):
            test = stmt.test
            if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                    and isinstance(test.ops[0], ast.Eq)):
                continue
            pairs = ((test.left, test.comparators[0]), (test.comparators[0], test.left))
            for attr_node, literal_node in pairs:
                key = ast.dump(attr_node)
                if key in assigned_literals and assigned_literals[key] == _literal_value(literal_node):
                    findings.append((stmt.lineno, "literal assigned earlier in this test"))
                    break
    return findings


def _module_mutable_bindings(tree):
    names = set()
    for stmt in tree.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        value = stmt.value
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        mutable = isinstance(value, (ast.List, ast.Dict, ast.Set))
        if isinstance(value, ast.Call):
            mutable = dotted_name(value.func) in MUTABLE_GLOBAL_FACTORIES \
                or dotted_name(value.func).split(".")[-1] in MUTABLE_GLOBAL_FACTORIES
        if not mutable:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _mutated_module_globals(func, globals_):
    mutated = set()
    for n in children_no_nesting(func):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and root_name(target.value) in globals_:
                    mutated.add(root_name(target.value))
                elif isinstance(target, ast.Attribute) and root_name(target) in globals_:
                    mutated.add(root_name(target))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in MUTATING_METHODS and root_name(n.func.value) in globals_:
                mutated.add(root_name(n.func.value))
    return mutated


def _read_module_globals_in_asserts(func, globals_):
    reads = {}
    for n in children_no_nesting(func):
        if not isinstance(n, ast.Assert):
            continue
        for sub in ast.walk(n.test):
            name = root_name(sub)
            if name in globals_ and isinstance(sub, (ast.Name, ast.Attribute, ast.Subscript)):
                reads.setdefault(name, n.lineno)
    return reads


def _autouse_fixture_resets(tree, globals_):
    resets = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        autouse = False
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            if not is_fixture_decorator(d):
                continue
            for kw in d.keywords:
                if kw.arg == "autouse" and isinstance(kw.value, ast.Constant) \
                        and kw.value.value is True:
                    autouse = True
        if autouse:
            resets |= _mutated_module_globals(node, globals_)
    return resets


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
class Finding:
    __slots__ = ("file", "line", "code", "detail", "conf", "snippet", "layer", "level")

    def __init__(self, file, line, code, detail=""):
        self.file = file
        self.line = line
        self.code = code
        self.detail = detail
        self.conf = CASES[code][1]  # effective confidence; run() may override it
        self.snippet = ""           # normalized source at the finding; set in analyze_file
        self.layer = "logic"        # logic | web | browser; set per file in analyze_file
        self.level = "unit"         # unit | integration | e2e; set per file in analyze_file

    def dict(self):
        title = CASES[self.code][0]
        return {
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "confidence": self.conf,
            "title": title,
            "detail": self.detail,
            "layer": self.layer,
            "level": self.level,
            "fix": FIX_HINTS.get(self.code, ""),
        }


def _iter_assertion_nodes(func):
    """Yield nodes in func's body (no nested scopes) that act as assertions:
    bare assert, fluent .should, xunit/mock/pytest assert calls, pytest.raises/fail,
    and pytest.raises/xunit-raises used as a `with` context."""
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert):
            yield n
        # sure: result.should.equal(y) — .should is the fluent assertion entry point
        elif isinstance(n, ast.Attribute) and n.attr == "should":
            yield n
        elif isinstance(n, ast.Call):
            if call_is_recognized_assertion(n):  # incl. fluent chains + pytest_check
                yield n
        elif isinstance(n, ast.With):
            for item in n.items:
                if is_call_to(item.context_expr, "pytest.raises", "raises") \
                        or is_xunit_raises_call(item.context_expr):
                    yield n
                    break


def has_assertion(func):
    return any(True for _ in _iter_assertion_nodes(func))


def _is_test_mode_true(node):
    """A constant value that puts a test-mode flag into test mode: True, 1, or one of
    the closed string forms ('1'/'true'/'test'/'yes'/'on'). 'production', 2, 'staging',
    a non-constant expression — none of these match, so a config write is not flagged."""
    if not isinstance(node, ast.Constant):
        return False
    val = node.value
    if isinstance(val, bool):          # bool first: bool is a subclass of int
        return val is True
    if isinstance(val, int):
        return val == 1
    if isinstance(val, str):
        return val.strip().lower() in TEST_MODE_TRUE_STRINGS
    return False


def _subscript_str_key(sub):
    """The literal string key of a subscript target, or None. Handles py3.8 ast.Index."""
    s = sub.slice
    if s.__class__.__name__ == "Index":  # py3.8 wraps the key in ast.Index
        s = s.value
    if isinstance(s, ast.Constant) and isinstance(s.value, str):
        return s.value
    return None


def _c48_toggle_writes(func):
    """Raw writes, in this test body, that flip a known test-mode toggle to test mode:
    os.environ["TESTING"] = <truthy>, settings.TESTING = <truthy> (not self/cls), or a
    bare TESTING = <truthy> that is declared `global` in the function (otherwise the
    name is a local and changes no shared state). v1 covers raw writes only; the
    monkeypatch.setenv form stays with C29's 'use monkeypatch' guidance."""
    global_names = set()
    for n in children_no_nesting(func):
        if isinstance(n, ast.Global):
            global_names.update(n.names)
    writes = []
    for n in children_no_nesting(func):
        if not (isinstance(n, ast.Assign) and _is_test_mode_true(n.value)):
            continue
        for tgt in n.targets:
            if isinstance(tgt, ast.Subscript) \
                    and dotted_name(tgt.value) == "os.environ" \
                    and _subscript_str_key(tgt) in ENV_TEST_MODE_KEYS:
                writes.append(n)
                break
            if isinstance(tgt, ast.Attribute) and MODULE_TEST_MODE_RE.match(tgt.attr) \
                    and root_name(tgt.value) not in ("self", "cls"):
                writes.append(n)
                break
            if isinstance(tgt, ast.Name) and tgt.id in global_names \
                    and MODULE_TEST_MODE_RE.match(tgt.id):
                writes.append(n)
                break
    return writes


def _c48_assert_reads_toggle(node, write):
    """True if `node` (a post-flip assertion) actually *reads the toggled flag
    itself*, not merely mentions a matching token. Receiver-aware so a same-named
    attribute on a different object, or the key string used as an unrelated
    literal, does not count (avoids the leaf-match false positive)."""
    for tgt in write.targets:
        if isinstance(tgt, ast.Subscript) and dotted_name(tgt.value) == "os.environ":
            key = _subscript_str_key(tgt)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Subscript) and dotted_name(sub.value) == "os.environ" \
                        and _subscript_str_key(sub) == key:
                    return True
                if isinstance(sub, ast.Call) and dotted_name(sub.func) in ("os.environ.get", "os.getenv") \
                        and sub.args and isinstance(sub.args[0], ast.Constant) \
                        and sub.args[0].value == key:
                    return True
            return False
        if isinstance(tgt, ast.Attribute):
            target = dotted_name(tgt)
            return any(isinstance(sub, ast.Attribute) and dotted_name(sub) == target
                       for sub in ast.walk(node))
        if isinstance(tgt, ast.Name):
            return any(isinstance(sub, ast.Name) and sub.id == tgt.id
                       and isinstance(sub.ctx, ast.Load)
                       for sub in ast.walk(node))
    return False


def empty_body(func):
    for stmt in func.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if isinstance(stmt, ast.Expr) and stmt.value.__class__.__name__ in ("Str", "Ellipsis"):
            continue
        return False
    return True


def makes_any_call(func):
    for n in children_no_nesting(func):
        if isinstance(n, ast.Call):
            return True
    return False


def handler_swallows(handler):
    for stmt in handler.body:
        if isinstance(stmt, (ast.Pass, ast.Continue)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return True


def handler_broad(handler):
    if handler.type is None:
        return True
    name = dotted_name(handler.type)
    return name.endswith("Exception") or name.endswith("BaseException")


def handler_catches_assertion(handler):
    """True if this except would actually swallow an AssertionError. Only a bare
    `except:`, `except Exception`, `except BaseException`, or `except
    AssertionError` (or a tuple including one) catches it. A specific custom
    handler whose name merely ends in "Exception" (e.g. `except TestingException`)
    does NOT catch AssertionError, so an assert in its try is not silenced -
    AssertionError propagates and still fails the test."""
    t = handler.type
    if t is None:
        return True

    def _catches(node):
        last = dotted_name(node).split(".")[-1]
        return last in ("Exception", "BaseException", "AssertionError")

    if isinstance(t, ast.Tuple):
        return any(_catches(e) for e in t.elts)
    return _catches(t)


def block_has_assertion(stmts):
    """True if the block contains a real check, not just any call."""
    for s in stmts:
        for sub in ast.walk(s):
            if isinstance(sub, ast.Assert):
                return True
            if isinstance(sub, ast.Attribute) and sub.attr == "should":
                return True
            if isinstance(sub, ast.Call):
                t = dotted_name(sub.func)
                last = t.split(".")[-1]
                if t.endswith("raises") or last.startswith("assert") \
                        or last in FLUENT_ASSERT_CALLS:
                    return True
                if is_xunit_assert_call(sub):
                    return True
    return False


def gather_mock_names(func):
    """Names within this function that hold a mock (params, @patch, assignments, with-as)."""
    names = set()
    args = func.args
    for a in list(args.args) + list(getattr(args, "kwonlyargs", []) or []):
        if "mock" in a.arg.lower():
            names.add(a.arg)
    if args.vararg and "mock" in args.vararg.arg.lower():
        names.add(args.vararg.arg)

    # @patch / @patch.object decorators inject a mock as a positional arg (unless
    # new= is given). Decorators apply bottom-up, so the bottom-most maps to the
    # first injected param after self/cls.
    patch_decos = []
    for d in func.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        target = d.func if isinstance(d, ast.Call) else d
        dn = dotted_name(target)
        last = dn.split(".")[-1]
        if last == "patch" or (last == "object" and "patch" in dn):
            if call and any(kw.arg == "new" for kw in call.keywords):
                continue  # new= replaces with a real object, no mock injected
            patch_decos.append(d)
    if patch_decos:
        pos = list(args.args)
        if pos and pos[0].arg in ("self", "cls"):
            pos = pos[1:]
        for i in range(len(patch_decos)):
            if i < len(pos):
                names.add(pos[i].arg)

    for n in children_no_nesting(func):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            dn = dotted_name(n.value.func)
            last = dn.split(".")[-1]
            if last in MOCK_FACTORIES or "patch" in dn:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
        if isinstance(n, ast.With):
            for item in n.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call):
                    dn = dotted_name(ce.func)
                    if "patch" in dn or dn.split(".")[-1] in MOCK_FACTORIES:
                        if isinstance(item.optional_vars, ast.Name):
                            names.add(item.optional_vars.id)
    return names


def is_warns_context(node):
    """A pytest warning context: pytest.warns(...) / pytest.deprecated_call(...),
    or the xunit self.assertWarns/assertWarnsRegex (anchored to self/cls)."""
    if is_call_to(node, "pytest.warns", "warns",
                  "pytest.deprecated_call", "deprecated_call"):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("assertWarns", "assertWarnsRegex") \
            and isinstance(node.func.value, ast.Name) \
            and node.func.value.id in ("self", "cls"):
        return True
    return False


def is_raises_or_warns_context(node):
    """Any exception/warning capturing context: pytest.raises, xunit assertRaises,
    or a warning context (C49/C51 share this with C19/C28)."""
    return is_call_to(node, "pytest.raises", "raises") \
        or is_xunit_raises_call(node) or is_warns_context(node)


def membership_self_confirm(test):
    """C52: `x in <literal collection holding x>` is true by construction. The
    left operand is AST-identical to an element of a literal Set/List/Tuple on the
    right, and has no Call (a call on each side may yield distinct objects, mirror
    C7's no-call guard). Returns True only for a single `in` comparison."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], ast.In):
        return False
    container = test.comparators[0]
    if not isinstance(container, (ast.Set, ast.List, ast.Tuple)):
        return False  # a Name/registry container is a real lookup, not self-confirm
    if any(isinstance(c, ast.Call) for c in ast.walk(test.left)):
        return False
    try:
        left = ast.dump(test.left)
    except Exception:
        return False
    for e in container.elts:
        try:
            if ast.dump(e) == left:
                return True
        except Exception:
            continue
    return False


def membership_is_eq_semantics(func, test):
    """Exempt C52 when the membership `x in {x}` is the __eq__/__hash__ half of a
    deliberate equality exercise: a sibling `assert x == x` / `assert x is x` on
    the SAME operand (aiohttp/starlette `assert ws == ws; assert ws in {ws}`). The
    sibling self-compare is what marks intent; a lone `x in {x}` stays C52."""
    try:
        left = ast.dump(test.left)
    except Exception:
        return False
    for n in ast.walk(func):
        if isinstance(n, ast.Compare) and n is not test \
                and len(n.ops) == 1 and isinstance(n.ops[0], (ast.Eq, ast.Is)):
            try:
                if ast.dump(n.left) == left == ast.dump(n.comparators[0]):
                    return True
            except Exception:
                continue
    return False


def both_sides_mock(test, mock_names):
    """C55: an == / is comparison whose BOTH operands are mock-rooted attribute
    chains (e.g. `m.foo == m.bar`, `a.return_value == b.return_value`). Each side
    must be an Attribute whose root Name is a proven local mock; one real side is
    a legitimate check and stays quiet."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], (ast.Eq, ast.Is)):
        return False
    left, right = test.left, test.comparators[0]
    for side in (left, right):
        if not isinstance(side, ast.Attribute):
            return False
        if root_name(side) not in mock_names:
            return False
    return True


# --- deep-sweep helpers (issue #51, C56/C57/C59) ---------------------------
# These are NEW and not field-validated yet (L15); guards are precision-first.

def file_async_def_names(tree):
    """Names bound to an `async def` anywhere in the file: module-level functions
    and methods inside any class body. C56 only fires when the asserted call
    resolves to one of these LOCAL async defs (intra-file). A cross-module callee
    is undecidable from the AST and must not fire, so it is deliberately absent."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            names.add(node.name)
    return names


def _locally_shadowed_sync_names(func):
    """Names the test body rebinds to a NON-async callable: a sync `def`, an
    `Assign`/`AnnAssign` target, a function arg, or an `import`/`from ... import`.
    C56 resolves a callee by bare name against the file-wide async-def set; if the
    test shadows that name with a sync binding, the call is no longer a coroutine
    and must not fire (the L1 scope trap). A local `async def` of the same name is
    NOT a shadow (still async), so a name bound by a local `async def` anywhere in
    the body is removed from the shadow set even if a sync binding also exists.

    Walks `ast.walk(func)` (not children_no_nesting) because a shadowing `def` is
    itself a def node, which children_no_nesting skips; the inner def's OWN body is
    irrelevant here, only the name it binds in the test's scope."""
    sync_bound = set()
    async_bound = set()
    for a in list(func.args.args) + list(getattr(func.args, "posonlyargs", []) or []) \
            + list(getattr(func.args, "kwonlyargs", []) or []):
        sync_bound.add(a.arg)
    for n in ast.walk(func):
        if n is func:
            continue
        if isinstance(n, ast.AsyncFunctionDef):
            async_bound.add(n.name)
        elif isinstance(n, ast.FunctionDef):
            sync_bound.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    sync_bound.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            sync_bound.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for alias in n.names:
                sync_bound.add(alias.asname or alias.name.split(".")[0])
    return sync_bound - async_bound


def assert_unawaited_coroutine(test, async_names, func):
    """C56: the asserted expression contains a `Call` to a local `async def` and
    no `await` wraps it, so the operand is a coroutine object, not the awaited
    value. Quiet when the call is awaited (an `Await` node anywhere in the test)
    or when the coroutine is handed to a loop runner (asyncio.run / anyio.run /
    run_until_complete / trio.run) before the comparison — those consume it.
    Resolves the callee only to a bare Name in `async_names` (intra-file), never a
    dotted/cross-module call, and never a name the test locally rebinds to a sync
    callable (scope guard, L1)."""
    if not async_names:
        return False
    if any(isinstance(n, ast.Await) for n in ast.walk(test)):
        return False
    for call in ast.walk(test):
        if not isinstance(call, ast.Call):
            continue
        # a loop runner consuming a coroutine arg means it IS driven, not vacuous
        if is_call_to(call, "asyncio.run", "anyio.run", "trio.run",
                      "run_until_complete", "loop.run_until_complete"):
            return False
    shadowed = _locally_shadowed_sync_names(func)
    for call in ast.walk(test):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                and call.func.id in async_names \
                and call.func.id not in shadowed:
            return True
    return False


def unspecced_mock_names(func):
    """Names bound, in this function body, to a BARE Mock()/MagicMock()/AsyncMock()
    with no spec=/spec_set= keyword. C57 only treats an attribute read on one of
    these as auto-created (always a fresh Mock). A mock built with spec= constrains
    its attributes, so reading m.attr can legitimately equal a real value — those
    names are excluded. NonCallableMock/create_autospec/patch are out: autospec and
    patch targets are spec-constrained or proxy a real object."""
    bare_factories = {"Mock", "MagicMock", "AsyncMock"}
    names = set()
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            last = dotted_name(n.value.func).split(".")[-1]
            if last not in bare_factories:
                continue
            if any(kw.arg in ("spec", "spec_set") for kw in n.value.keywords):
                continue
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


_MOCK_ATTR_FACTORIES = {"Mock", "MagicMock", "AsyncMock", "NonCallableMock"}


def names_reaching_assert_or_call(func):
    """Names that reach a verification point: read inside an `assert` test, OR
    passed as an argument to any Call. The Call arm is the delegate-pattern
    exemption `shared/PROTOCOL.md` adopts for C2b — handing a captured value to a
    verification helper (`check_output(out)`, `verify_logs(caplog)`) is a check,
    the scanner just cannot see inside the helper. Shared by C31 (readouterr) and
    C50 (caplog): a value that reaches neither assert nor call was truly discarded."""
    used = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Name):
                    used.add(sub.id)
        elif isinstance(node, ast.Call):
            for arg in node.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name):
                        used.add(sub.id)
            for kw in node.keywords:
                for sub in ast.walk(kw.value):
                    if isinstance(sub, ast.Name):
                        used.add(sub.id)
    return used


def _mock_attrs_explicitly_set(func):
    """(name, attr) pairs explicitly configured in the function body. C57 must stay
    quiet on these: once an attribute is set, reading it is a real value, not an
    auto-created Mock. Only one level deep (m.attr), the shape C57 inspects.

    Three idioms, all first-class in unittest.mock:
      m.role = 3                    -> attribute assignment
      m = MagicMock(role="admin")   -> constructor kwargs (bar reserved spec=/wraps=)
      m.configure_mock(role="admin")-> configure_mock kwargs
    """
    # unittest.mock ctor/configure_mock kwargs that configure the mock itself, not
    # a child attribute of the same name. side_effect is special (C6c territory).
    reserved = {"spec", "spec_set", "wraps", "name", "side_effect", "return_value"}
    pairs = set()
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                    pairs.add((t.value.id, t.attr))
            # m = MagicMock(role="admin"): each attribute kwarg is an explicit set.
            if isinstance(n.value, ast.Call) \
                    and dotted_name(n.value.func).split(".")[-1] in _MOCK_ATTR_FACTORIES:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        for kw in n.value.keywords:
                            if kw.arg and kw.arg not in reserved:
                                pairs.add((t.id, kw.arg))
        # m.configure_mock(role="admin"): each attribute kwarg is an explicit set.
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Attribute) \
                and n.value.func.attr == "configure_mock" \
                and isinstance(n.value.func.value, ast.Name):
            name = n.value.func.value.id
            for kw in n.value.keywords:
                if kw.arg and kw.arg not in reserved:
                    pairs.add((name, kw.arg))
    return pairs


def compare_unconfigured_mock_attr(test, unspecced, set_pairs):
    """C57: a single == / != / is comparison where one side is `m.attr` and `m` is
    a bare unspecced Mock whose `attr` was never explicitly set. That side is a
    freshly auto-created Mock, so the comparison is vacuous. Requires exactly one
    side to be such a mock attribute (both sides mock is C55's territory)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)):
        return False
    def is_unconfigured_attr(side):
        if not (isinstance(side, ast.Attribute) and isinstance(side.value, ast.Name)):
            return False
        if side.value.id not in unspecced:
            return False
        # a known mock-assert attribute (m.assert_called...) or call_count is not a
        # value comparison; those are C13/C6c, leave them alone.
        if side.attr in MOCK_ASSERTS_VALID or side.attr == "call_count":
            return False
        return (side.value.id, side.attr) not in set_pairs
    left, right = test.left, test.comparators[0]
    return is_unconfigured_attr(left) ^ is_unconfigured_attr(right)


def has_bare_compare_statement(func):
    """True if the function body has a bare `Expr(Compare)` statement (the C59
    shape). Used to let C59 OWN that line so C2b does not also fire — a bare
    comparison is the author's attempt at a check, so it is not 'checks nothing'
    in the generic C2b sense (mirrors the C2c exemption of C2b)."""
    for n in children_no_nesting(func):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Compare):
            return True
    return False


def _stmt_is_terminator(stmt):
    """An unconditional control-flow exit: nothing after it in this block runs."""
    if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        t = dotted_name(stmt.value.func)
        # A genuine fail() terminator: pytest.fail, a bare imported fail(), or unittest's
        # self.fail()/cls.fail(). An arbitrary obj.fail() (logger.fail, result.fail) is NOT a
        # terminator and must not strand the assertion below it (mirrors is_pytest_skip_call).
        if t in ("pytest.fail", "fail", "self.fail", "cls.fail"):
            return True
    if isinstance(stmt, ast.Assert):  # assert False / assert 0 always raises
        v = _const_value(stmt.test)
        if v is not None and not v:
            return True
    return False


def _stmt_is_check(stmt):
    """A statement that performs a verification (assert, mock-assert, raises)."""
    if isinstance(stmt, ast.Assert):
        return True
    if isinstance(stmt, ast.Expr):
        # sure: result.should.equal(y) — top-level expression accesses .should
        if isinstance(stmt.value, ast.Attribute) and stmt.value.attr == "should":
            return True
        if isinstance(stmt.value, ast.Call):
            # Recognize fluent chains by their root entry point (assert_that(...).is_*)
            # and pytest_check soft asserts, matching func_has_any_check (FP-1/FP-5).
            if call_is_recognized_assertion(stmt.value):
                return True
    if isinstance(stmt, ast.With):
        for item in stmt.items:
            if is_call_to(item.context_expr, "pytest.raises", "raises") \
                    or is_xunit_raises_call(item.context_expr):
                return True
    return False


def block_bodies(func):
    """Yield every block statement-list in func (its body and the bodies of nested
    if/for/while/with/try), without entering nested def/class/lambda."""
    yield func.body
    for node in children_no_nesting(func):
        for field in ("body", "orelse", "finalbody"):
            b = getattr(node, field, None)
            if isinstance(b, list) and b:
                yield b
        for h in getattr(node, "handlers", []) or []:
            yield h.body


def dead_checks_after_terminator(stmts):
    """Checks that appear after an unconditional terminator in the same block."""
    dead = []
    terminated = False
    for stmt in stmts:
        if terminated and _stmt_is_check(stmt):
            dead.append(stmt)
        if _stmt_is_terminator(stmt):
            terminated = True
    return dead


def _for_body_always_runs(stmt):
    """A for over a non-empty literal collection always runs its body."""
    return isinstance(stmt, ast.For) and isinstance(
        stmt.iter, (ast.List, ast.Tuple, ast.Set)) and len(stmt.iter.elts) > 0


def is_async_liar(func):
    """An `async def test_*` that makes calls and asserts but never awaits anything
    (no await, async with, or async for) and does not drive a loop itself
    (asyncio.run / run_until_complete / anyio.run). The SUT call returns an
    un-awaited coroutine, so the assertion checks the wrong object or the coroutine
    never runs: a vacuous pass. C22 (off by default; async suites opt in)."""
    if not isinstance(func, ast.AsyncFunctionDef):
        return False
    has_await = any(isinstance(n, (ast.Await, ast.AsyncWith, ast.AsyncFor))
                    for n in ast.walk(func))
    if has_await:
        return False
    drives_loop = any(
        is_call_to(c, "asyncio.run", "run_until_complete", "anyio.run", "trio.run")
        for c in ast.walk(func) if isinstance(c, ast.Call))
    if drives_loop:
        return False
    return makes_any_call(func) and func_has_any_check(func)


def runs_a_check_unconditionally(stmts):
    """True if some verification in this block runs on every path through it.

    A check directly in the block runs. A `with`/`try` body runs. A `for` over a
    non-empty literal runs. An `if` whose every branch (including a closing else)
    runs a check is exhaustive, so a check always runs.
    """
    for stmt in stmts:
        if _stmt_is_check(stmt):  # includes a top-level `with pytest.raises`
            return True
        if isinstance(stmt, ast.With):
            if runs_a_check_unconditionally(stmt.body):
                return True
        elif isinstance(stmt, ast.Try):
            if runs_a_check_unconditionally(stmt.body) \
                    or runs_a_check_unconditionally(stmt.finalbody):
                return True
        elif _for_body_always_runs(stmt):
            if runs_a_check_unconditionally(stmt.body):
                return True
        elif isinstance(stmt, ast.If):
            # exhaustive only if there is an else and BOTH sides run a check
            if stmt.orelse and runs_a_check_unconditionally(stmt.body) \
                    and runs_a_check_unconditionally(stmt.orelse):
                return True
    return False


def func_has_any_check(func):
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Attribute) and n.attr == "should":
            return True
        if isinstance(n, ast.Call):
            if call_is_recognized_assertion(n):
                return True
        if isinstance(n, ast.With):
            for item in n.items:
                if is_call_to(item.context_expr, "pytest.raises", "raises") \
                        or is_xunit_raises_call(item.context_expr):
                    return True
    return False


# Decorator leaf names that mark a function as a web route handler / WSGI app,
# not a test: @app.get/@app.post/... (fastapi, sanic, flask), @app.route,
# @Request.application (werkzeug), @app.websocket/@app.signal/@app.middleware.
ROUTE_DECORATOR_NAMES = {
    "get", "post", "put", "delete", "patch", "head", "options", "trace",
    "route", "api_route", "websocket", "application", "middleware",
    "signal", "listener", "on_request", "on_response", "endpoint",
}


def is_web_route_handler(func):
    for d in func.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if dotted_name(target).split(".")[-1] in ROUTE_DECORATOR_NAMES:
            return True
    return False


def takes_callback_args(func):
    """A nested def that accepts a parameter (other than self/cls) is being used
    as a callback/handler (it receives `request`, a query value, ...), not a
    forgotten pytest test. A real nested test would take no fixtures (pytest
    cannot inject them into a nested def anyway)."""
    args = func.args
    n_pos = len(args.args)
    if n_pos and args.args[0].arg in ("self", "cls"):
        n_pos -= 1
    return bool(n_pos or args.vararg or args.kwarg or args.kwonlyargs)


def _subtest_verifies(node):
    """True if a `with self.subTest(...)` body verifies something, raises, or delegates
    to a verification helper — the C2c exemptions. Walks the whole block (a check may be
    nested in an inner if/for). HELPER_PREFIXES delegate-exemption mirrors C2b."""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assert, ast.Raise)):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "should":
            return True
        if isinstance(sub, ast.Call):
            t = dotted_name(sub.func)
            last = t.split(".")[-1]
            if last in MOCK_ASSERTS_VALID or last.startswith("assert") \
                    or t.endswith("raises") or last == "fail" \
                    or last in FLUENT_ASSERT_CALLS or last.startswith(HELPER_PREFIXES):
                return True
            if is_xunit_assert_call(sub):
                return True
    return False


def _is_subtest_ctx(expr):
    """A `self.subTest(...)` / `cls.subTest(...)` context expression. Receiver-anchored
    to self/cls (like is_xunit_assert_call) so an unrelated `obj.subTest()` is not a
    false positive."""
    return isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) \
        and expr.func.attr == "subTest" \
        and isinstance(expr.func.value, ast.Name) and expr.func.value.id in ("self", "cls")


def has_empty_subtest(func):
    """A `with self.subTest(...)` block whose body asserts nothing (C2c). The subTest
    is the unittest analogue of an empty test: each generated sub-case runs but verifies
    nothing. Only meaningful when the method has no assertion (caller guarantees that),
    so a subTest with a real check elsewhere is not the target."""
    for n in children_no_nesting(func):
        if isinstance(n, ast.With) and any(_is_subtest_ctx(item.context_expr) for item in n.items):
            if not _subtest_verifies(n):
                return True
    return False


def has_direct_check(func):
    """An assertion (or pytest.raises) directly in this function's own body,
    not buried in a deeper nested def. A genuine forgotten test asserts in its
    own body; a local orchestration coroutine whose asserts live in further
    nested helpers does not."""
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Call) and is_call_to(n, "pytest.raises", "raises"):
            return True
    return False


def name_is_used(scope, name):
    """The name appears as a value (Load) somewhere in `scope` - the function is
    called, awaited, scheduled (asyncio.create_task), or passed as a callback,
    so it actually runs. A genuinely forgotten test is defined and never
    referenced (the author relied on pytest collecting it, and it never does).
    Used for NESTED defs, where `scope` is the single enclosing function: a
    callback registered by bare name (`cleanup_ctx.append(run_test)`) counts."""
    for n in ast.walk(scope):
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load):
            return True
    return False


def name_used_at_module_level(tree, name):
    """Like name_is_used but scoped for a TOP-LEVEL function, so an unrelated
    same-name local in another function (a rebinding, a comprehension target)
    does not count. The real forgotten-test signal is "never called and never
    referenced at module level". Counts: a call target `name(...)` anywhere
    (covers `asyncio.run(main())`), or a Load of `name` in module-level code."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == name:
            return True
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and n.id == name \
                    and isinstance(n.ctx, ast.Load):
                return True
    return False


def looks_like_forgotten_nested_test(func, scope):
    """A nested `def test*` that is genuinely an uncollected, never-run test -
    not a wired local construct. Real projects nest a `test*`-named function
    only as a route handler (@app.get), a CLI command (@click.command), a
    callback passed to the framework, or a local helper coroutine that the test
    awaits - never as a test they expect pytest to collect. So flag only the
    bare shape: undecorated, no parameters, with a real check in its own body,
    and never referenced (so it truly never runs)."""
    return (
        not func.decorator_list
        and not takes_callback_args(func)
        and has_direct_check(func)
        and not name_is_used(scope, func.name)
    )


def analyze_function(func, file, findings, in_class=False, skip_exempt=False,
                     file_layer="logic", controls_time=False, long_test_threshold=50,
                     inline_setup_threshold=5, async_names=frozenset(),
                     imports_sklearn=False):
    line = func.lineno
    mock_names = gather_mock_names(func)
    _unspecced_mocks = unspecced_mock_names(func)
    _mock_set_pairs = _mock_attrs_explicitly_set(func)
    ctx = detect_test_context(func, file_layer)

    # C25: xfail without strict=True — XPASS silently treated as pass, masking a fixed bug.
    for d in func.decorator_list:
        if _is_xfail_without_strict(d):
            findings.append(Finding(file, line, "C25",
                                    "add strict=True or convert to skip if permanently broken"))
            break

    # C32: @pytest.mark.skip (or bare @skip) without reason= — the test is
    # excluded indefinitely with no documented motive. Without a reason, there
    # is no signal for when it should be re-enabled, so broken suites can
    # accumulate silently. Add reason="<why>" or remove the skip entirely.
    for d in func.decorator_list:
        if _is_skip_without_reason(d):
            findings.append(Finding(file, line, "C32",
                                    "add reason= to document why the test is skipped"))
            break

    # C35: retry/repeat/flaky decorator — the test is re-run on failure until it
    # passes, which can make a genuinely flaky SUT appear green. Retries should be
    # a temporary workaround at most; the root cause (non-determinism, race
    # condition, test-order dependency) should be fixed instead.
    for d in func.decorator_list:
        if _is_retry_marker(d):
            findings.append(Finding(file, line, "C35",
                                    "fix the flakiness instead of retrying"))
            break

    body_intentionally_empty = (has_property_test_decorator(func)
                                or has_skip_decorator(func) or skip_exempt)
    if not has_assertion(func) and not body_intentionally_empty:
        if empty_body(func):
            findings.append(Finding(file, line, "C2"))
        elif has_empty_subtest(func):
            # More specific than C2b: the calls are wrapped in a self.subTest(...) that
            # asserts nothing. C2c owns this shape so it does not double-report as C2b.
            findings.append(Finding(file, line, "C2c",
                                    "a self.subTest(...) block wraps work but asserts nothing"))
        elif makes_any_call(func) and not has_bare_compare_statement(func):
            findings.append(Finding(file, line, "C2b",
                                    "if the check lives in a helper called here, ignore"))

    for n in ast.walk(func):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not func:
            if n.name.startswith("test") and looks_like_forgotten_nested_test(n, func):
                findings.append(Finding(file, n.lineno, "C4",
                                        "nested test function is not collected"))

    # C20 dead-code lines own their statement: an assertion that never runs cannot
    # be trivial, weak, or self-comparing in any way that matters, so the per-assert
    # detectors below are suppressed on these lines and C20 alone reports them
    # (#108, mirroring how C21 owns its conditional asserts and suppresses C1).
    dead_lines = {
        stmt.lineno
        for body in block_bodies(func)
        for stmt in dead_checks_after_terminator(body)
    }

    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert):
            if n.lineno in dead_lines:
                continue
            test = n.test
            _cc = mock_call_count_smell(test, mock_names)
            if _cc == "C44":
                findings.append(Finding(file, n.lineno, "C44",
                                        "a mock's call_count is never negative — this comparison is always true"))
            elif _cc == "C6c":
                findings.append(Finding(file, n.lineno, "C6c",
                                        "asserts only that the mock was called, not how many times"))
            elif assert_always_true(test):
                findings.append(Finding(file, n.lineno, "C5"))
            elif assert_always_truthy_object(test):
                findings.append(Finding(file, n.lineno, "C42",
                                        "a generator expression / lambda object is always truthy"))
            elif assert_numeric_tautology(test):
                findings.append(Finding(file, n.lineno, "C44",
                                        "len()/abs() is never negative — this comparison is always true"))
            elif assert_none_mutator(test, builtin_container_names(func, n.lineno)):
                findings.append(Finding(file, n.lineno, "C41",
                                        "an in-place mutator (sort/append/...) returns None — "
                                        "assert the resulting state instead"))
            elif assert_self_compare(test) and not in_equality_semantics_test(func, test):
                findings.append(Finding(file, n.lineno, "C7"))
            elif both_sides_mock(test, mock_names):
                findings.append(Finding(file, n.lineno, "C55",
                                        "both sides are mock-rooted — the comparison exercises the doubles, not the SUT"))
            elif compare_unconfigured_mock_attr(test, _unspecced_mocks, _mock_set_pairs):
                findings.append(Finding(file, n.lineno, "C57",
                                        "the expected side is an attribute of a bare Mock() with no spec — it auto-creates a fresh Mock, so the comparison is vacuous"))
            elif assert_unawaited_coroutine(test, async_names, func):
                findings.append(Finding(file, n.lineno, "C56",
                                        "the operand calls a local async def but is never awaited — the assertion checks a coroutine object, not its value"))
            elif membership_self_confirm(test) and not membership_is_eq_semantics(func, test):
                findings.append(Finding(file, n.lineno, "C52",
                                        "the collection is built from the subject — membership is true by construction"))
            else:
                if assert_exact_float(test):
                    findings.append(Finding(file, n.lineno, "C8"))
                if compare_has_untolerated_approx(test):
                    findings.append(Finding(file, n.lineno, "C8b",
                                            "pytest.approx() with no rel=/abs= uses the 1e-6 default"))
                if assert_sensitive_equality(test):
                    findings.append(Finding(file, n.lineno, "C18"))
                weak = assert_weak(test, ctx)
                if weak:
                    findings.append(Finding(file, n.lineno, "C6", weak))
                hint = _suboptimal_assert_hint(test)
                if hint:
                    findings.append(Finding(file, n.lineno, "C34", hint))

    # C41 (unittest form): assertIsNone(lst.sort()) — the argument is the None
    # return of an in-place mutator, so the assertion is trivially green. The
    # `assert ...` forms are handled in the dispatch loop above; this catches the
    # xunit-style call. assertIsNotNone(lst.sort()) would FAIL, so it is not a
    # false-green and is left alone.
    for n in children_no_nesting(func):
        if isinstance(n, ast.Expr) and is_xunit_assert_call(n.value) \
                and n.value.func.attr == "assertIsNone" and n.value.args \
                and n.lineno not in dead_lines \
                and _is_none_returning_mutator_call(n.value.args[0],
                                                    builtin_container_names(func, n.lineno)):
            findings.append(Finding(file, n.lineno, "C41",
                                    "an in-place mutator (sort/append/...) returns None — "
                                    "assert the resulting state instead"))

    # C8b (xunit form): self.assertAlmostEqual / assertNotAlmostEqual with no explicit
    # tolerance. The default places=7 lets a meaningfully wrong value pass.
    for n in children_no_nesting(func):
        if isinstance(n, ast.Expr) and almost_equal_without_tolerance(n.value) \
                and n.lineno not in dead_lines:
            findings.append(Finding(file, n.lineno, "C8b",
                                    "assertAlmostEqual with no places=/delta= uses the default 7 places"))

    # C6b: assertion subscripts a mock call-args list by a computed/index-derived
    # position rather than by a stable name — the check breaks if the argument
    # order of the called function changes.
    _index_names = _index_names_from_index_calls(func)
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert) and assert_arg_order_coupled(n.test, _index_names):
            findings.append(Finding(file, n.lineno, "C6b",
                                    "assertion uses positional index into call_args — "
                                    "breaks on argument-order changes"))

    # C11a: assertEqual(obj.attr, VALUE) where VALUE was set by the test itself
    # in the same function — the assertion confirms what the test just wrote, not
    # what the SUT produced.
    for line_no, detail in c11a_findings(func):
        findings.append(Finding(file, line_no, "C11a", detail))

    # C21: every assertion in the test is conditional and none runs
    # unconditionally, so a false condition at runtime makes the whole test pass
    # vacuously. A function-scoped, higher-signal cousin of C1. When it fires it
    # OWNS the function's conditional asserts, so the per-assert C1 below is
    # suppressed for this function to avoid double-reporting one smell.
    c21_fired = func_has_any_check(func) and not runs_a_check_unconditionally(func.body)
    if c21_fired:
        findings.append(Finding(file, line, "C21"))

    if not c21_fired:
        for n in children_no_nesting(func):
            if isinstance(n, (ast.If, ast.For, ast.While)):
                # A for over a non-empty literal always runs its body, so the
                # assert is never skipped: not C1. (`for q in (a, b, c): assert`).
                if _for_body_always_runs(n):
                    continue
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Assert):
                        findings.append(Finding(file, sub.lineno, "C1"))
                        break

    # C22: an async test that asserts but never awaits (off by default, J1).
    if is_async_liar(func):
        findings.append(Finding(file, func.lineno, "C22"))

    # C39: the test returns a comparison instead of asserting it. `return x == y`
    # computes the boolean and hands it to pytest, which ignores a test's return
    # value (and warns: PytestReturnNotNoneWarning). The comparison runs but
    # nothing checks it, so the test is green no matter the result.
    for n in children_no_nesting(func):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Compare):
            findings.append(Finding(file, n.lineno, "C39",
                                    "use assert: pytest ignores the value a test returns"))

    # C59: a bare comparison written as a statement (`result == expected`) instead
    # of `assert result == expected`. pytest computes the boolean and discards it,
    # so nothing is checked (the loose-statement sibling of C39's `return x == y`).
    # The bare `ast.Expr(value=ast.Compare)` shape is the whole signal: an Expr
    # statement is never consumed by assert/return/call-arg, so a Compare there is
    # always evaluated-and-dropped. Dead lines are owned by C20 and skipped (L2).
    for n in children_no_nesting(func):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Compare) \
                and n.lineno not in dead_lines:
            findings.append(Finding(file, n.lineno, "C59",
                                    "bare comparison as a statement — use assert, the value is discarded"))

    # C43: pytest.skip() / self.skipTest() in the middle of the body, after some
    # logic has run, with a verification still below it. A skip at the top is a
    # legitimate guard; a skip after the arrange/act strands the asserts under it
    # so they never execute, and the test reports skipped rather than run. Move
    # the skip (with its condition) above the logic.
    _body = func.body
    for _i, _stmt in enumerate(_body):
        if isinstance(_stmt, ast.Expr) and isinstance(_stmt.value, ast.Call) \
                and is_pytest_skip_call(_stmt.value):
            _has_prior_logic = any(
                not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                     and isinstance(s.value.value, str))
                and not isinstance(s, ast.Pass)
                for s in _body[:_i]
            )
            _has_later_check = any(
                isinstance(x, ast.Assert)
                for s in _body[_i + 1:] for x in ast.walk(s)
            )
            if _has_prior_logic and _has_later_check:
                findings.append(Finding(file, _stmt.lineno, "C43",
                                        "skip() after test logic skips the checks below it — "
                                        "move it to the top with its condition"))
            break

    # C20: a check that sits AFTER an unconditional terminator in the same block
    # (return / raise / break / continue / pytest.fail() / assert False) is dead
    # code, it never runs. Scanned per block body so a terminator in one branch
    # does not orphan a sibling at the parent level.
    for line_no in sorted(dead_lines):
        findings.append(Finding(file, line_no, "C20"))

    for n in children_no_nesting(func):
        if isinstance(n, ast.Try):
            body_has_check = block_has_assertion(n.body)
            for h in n.handlers:
                skips = any(
                    is_pytest_skip_call(c)
                    for c in ast.walk(h) if isinstance(c, ast.Call)
                )
                if skips and handler_broad(h):
                    findings.append(Finding(file, h.lineno, "C17"))
                elif handler_swallows(h) and handler_catches_assertion(h) and body_has_check:
                    findings.append(Finding(file, h.lineno, "C3"))

    for n in children_no_nesting(func):
        # bare assert_* / called* attribute on a mock, missing parentheses
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Attribute):
            attr = n.value.attr
            if (attr.startswith("assert_") or attr.startswith("called")) \
                    and root_name(n.value) in mock_names:
                findings.append(Finding(file, n.lineno, "C13",
                                        "'%s' used without (): checks nothing" % attr))
        if isinstance(n, ast.Call):
            target = dotted_name(n.func)
            last = target.split(".")[-1]
            if last in MOCK_FALSE_NAMES and root_name(n.func) in mock_names:
                findings.append(Finding(file, n.lineno, "C13",
                                        "'%s' is not part of the mock API" % last))
            is_patch = last == "patch" or (last == "object" and "patch" in target)
            if is_patch:
                kwargs = {kw.arg for kw in n.keywords if kw.arg}
                if not ({"autospec", "spec", "spec_set"} & kwargs):
                    findings.append(Finding(file, n.lineno, "C13b"))

    for n in children_no_nesting(func):
        call = None
        if isinstance(n, ast.With):
            for item in n.items:
                if is_call_to(item.context_expr, "pytest.raises", "raises"):
                    call = item.context_expr
            # C19: a raises block wrapping more than one statement. An earlier
            # statement can raise the expected error, so the call you meant to
            # test is never reached and the test passes for the wrong reason.
            if call is not None and len(n.body) > 1:
                findings.append(Finding(file, n.lineno, "C19",
                                        "narrow the block to the one call that should raise"))
        elif isinstance(n, ast.Call) and is_call_to(n, "pytest.raises", "raises"):
            call = n
        if call is not None:
            kwargs = {kw.arg for kw in call.keywords if kw.arg}
            args = call.args
            broad = args and dotted_name(args[0]) in ("Exception", "BaseException")
            if not args:
                findings.append(Finding(file, n.lineno, "C9", "raises with no error type"))
            elif broad and "match" not in kwargs:
                findings.append(Finding(file, n.lineno, "C9", "raises(Exception) without match"))

    # C16: result depends on time, randomness, a fixed sleep, or a hardcoded
    # timeout — uses helper to keep detection in one place (also covers
    # concurrency timeouts added in #7).
    _c16_has_seed = any(
        is_call_to(c, "random.seed", "seed", "np.random.seed",
                   "torch.manual_seed", "manual_seed",
                   "tf.random.set_seed", "set_seed")
        for c in ast.walk(func) if isinstance(c, ast.Call)
    )
    _c16_nonce = _random_nonce_calls(func)
    for n in children_no_nesting(func):
        if isinstance(n, ast.Call):
            detail = c16_call_detail(n, _c16_has_seed, controls_time, _c16_nonce)
            if detail:
                findings.append(Finding(file, n.lineno, "C16", detail))

    for n in children_no_nesting(func):
        if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp) \
                and isinstance(n.test.op, ast.Not):
            target = n.test.operand
            checks_exists = is_call_to(target, "exists", "isfile", "is_file") \
                or (isinstance(target, ast.Attribute) and target.attr in ("exists",))
            writes = any(
                is_call_to(c, "write_text", "write_bytes", "write", "dump", "open")
                for c in ast.walk(n) if isinstance(c, ast.Call)
            )
            # snapshot / visual-regression tests at the web/UI layer write a
            # golden on first run by design, so the "if not exists: write" shape
            # is the norm there, not a smell. Suppress C14 in web/browser ctx.
            if checks_exists and writes and not ({"web", "browser"} & ctx):
                findings.append(Finding(file, n.lineno, "C14"))

    # C23: the test reads a file using a hard-coded string path. The outcome
    # depends on the filesystem state at runtime: the file may not exist in CI
    # (false negative) or may hold stale content from a prior run (false
    # positive). Covers bare open("path") and Path("path").read_text/read_bytes().
    for n in children_no_nesting(func):
        if not isinstance(n, ast.Call):
            continue
        target = dotted_name(n.func)
        if target in ("open", "io.open", "codecs.open") and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            findings.append(Finding(file, n.lineno, "C23"))
        elif isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("read_text", "read_bytes") \
                and isinstance(n.func.value, ast.Call):
            val_call = n.func.value
            fname = dotted_name(val_call.func)
            if fname in ("Path", "pathlib.Path") and val_call.args \
                    and isinstance(val_call.args[0], ast.Constant) \
                    and isinstance(val_call.args[0].value, str):
                findings.append(Finding(file, n.lineno, "C23"))

    # C27: try/except/pass — a try block that silently swallows the expected exception
    # makes the test pass whether the exception was raised or not. Unlike C3 (which fires
    # when an assert lives inside the try body), C27 fires when the try body has NO
    # assertion and no sibling statement in the function performs a check either.
    for n in func.body:
        if not isinstance(n, ast.Try):
            continue
        if block_has_assertion(n.body):
            continue  # assertion inside try body: C3's territory
        if not any(isinstance(sub, ast.Call)
                   for stmt in n.body for sub in ast.walk(stmt)):
            continue  # nothing exercised in the try body
        if not any(handler_swallows(h) and h.type is not None for h in n.handlers):
            continue  # no pass-only handler with a specific exception type
        if any(_stmt_is_check(s) for s in func.body if s is not n):
            continue  # a sibling statement performs a real check
        findings.append(Finding(file, n.lineno, "C27"))

    # C28: pytest.raises with `as NAME` binding that is never read afterwards.
    # The programmer intended to inspect the exception content but did not —
    # the exception type is verified but message, args, and attributes are not.
    for n in children_no_nesting(func):
        if not isinstance(n, ast.With):
            continue
        excinfo_name = None
        for item in n.items:
            if is_call_to(item.context_expr, "pytest.raises", "raises") \
                    and item.optional_vars is not None \
                    and isinstance(item.optional_vars, ast.Name):
                excinfo_name = item.optional_vars.id
                break
        if excinfo_name is None:
            continue
        if any(isinstance(sub, ast.Name) and sub.id == excinfo_name
               and isinstance(sub.ctx, ast.Load)
               for sub in ast.walk(func)):
            continue  # excinfo name is actually read somewhere in the function
        findings.append(Finding(file, n.lineno, "C28",
                                "'%s' declared but never read" % excinfo_name))

    # C49: a pytest.warns / deprecated_call / assertWarns context wrapping more
    # than one statement (sibling of C19 for raises). An unrelated earlier line
    # may emit the expected warning while the call under test never warns, so the
    # context is satisfied for the wrong reason. Mirror C19: only len(body) > 1.
    for n in children_no_nesting(func):
        if not isinstance(n, ast.With):
            continue
        if any(is_warns_context(item.context_expr) for item in n.items) \
                and len(n.body) > 1:
            findings.append(Finding(file, n.lineno, "C49",
                                    "narrow the block to the one call that should warn"))

    # C51: a pytest.raises / warns context whose body contains NO call at all
    # (only pass / docstring / ...). The author left out the call under test, so
    # nothing can ever be captured. One call is legitimate; an `as NAME` binding
    # is C28 territory and is skipped here.
    for n in children_no_nesting(func):
        if not isinstance(n, ast.With):
            continue
        ctx_items = [item for item in n.items
                     if is_raises_or_warns_context(item.context_expr)]
        if not ctx_items:
            continue
        if any(item.optional_vars is not None for item in ctx_items):
            continue  # `as excinfo` inspection is C28's concern
        if any(isinstance(sub, ast.Call)
               for stmt in n.body for sub in ast.walk(stmt)):
            continue  # the call that should raise/warn is present
        findings.append(Finding(file, n.lineno, "C51",
                                "no call inside the block — nothing can raise or warn"))

    # C48: dark-patch — the test flips a known test-mode toggle (env var or a
    # module/settings flag) to a test-mode value and then asserts, so it exercises
    # the product's test-only branch instead of real behaviour. Detection-only;
    # v1 covers RAW writes (os.environ[...]= / settings.TESTING= / global TESTING=).
    c48_lines = set()
    _assert_nodes = list(_iter_assertion_nodes(func))
    if _assert_nodes:
        for w in _c48_toggle_writes(func):
            after = [a for a in _assert_nodes if a.lineno > w.lineno]
            if not after:
                continue  # nothing asserted after the flip — not the dark-patch
            before = any(a.lineno < w.lineno for a in _assert_nodes)
            checks_toggle = any(_c48_assert_reads_toggle(a, w) for a in after)
            # A genuine assertion *before* the flip means real behaviour is already
            # verified; the post-flip asserts are incidental, not a dark-patch (#107).
            # The exception: a post-flip assertion that inspects the toggled flag
            # itself IS the dark-patch, so keep flagging that case.
            if before and not checks_toggle:
                continue
            findings.append(Finding(file, w.lineno, "C48",
                                    "test sets a test-mode flag then asserts — drive real behaviour, not the test-only branch"))
            c48_lines.add(w.lineno)

    # C29: direct os.environ assignment in a test — the mutation outlives the test
    # and contaminates every test that runs after. Use monkeypatch.setenv() which
    # saves and restores the original value automatically. When C48 already fired on
    # the same write, that more specific finding owns the line (no double report).
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Subscript) \
                        and dotted_name(tgt.value) in ("os.environ",) \
                        and n.lineno not in c48_lines:
                    findings.append(Finding(file, n.lineno, "C29",
                                            "use monkeypatch.setenv() to auto-restore"))
        elif isinstance(n, ast.Call):
            dn = dotted_name(n.func)
            if dn == "os.environ.update" or dn.endswith(".environ.update") \
                    or dn in ("os.putenv", "putenv"):
                findings.append(Finding(file, n.lineno, "C29",
                                        "use monkeypatch.setenv() to auto-restore"))

    # D1: 2+ assertions with no message — when the test fails you cannot tell
    # which assertion triggered it (Assertion Roulette). Function-level smell.
    # D3: same assertion written twice — the duplicate adds no coverage.
    _asserts = [n for n in children_no_nesting(func) if isinstance(n, ast.Assert)]
    if len(_asserts) >= 2 and all(n.msg is None for n in _asserts):
        findings.append(Finding(file, line, "D1", "%d assertions" % len(_asserts)))
    _seen_dumps = {}
    for n in _asserts:
        try:
            dump = ast.dump(n.test)
        except Exception:
            continue
        if dump in _seen_dumps:
            findings.append(Finding(file, n.lineno, "D3"))
        else:
            _seen_dumps[dump] = n.lineno

    # D4: @pytest.mark.parametrize with more than 2 cases and no ids= argument.
    # Without ids=, pytest names cases test_foo[0], test_foo[1], etc.; the failing
    # case cannot be identified from the test name alone. Add ids= with short
    # descriptive strings, or a callable that names each case.
    for d in func.decorator_list:
        if not isinstance(d, ast.Call):
            continue
        dn = dotted_name(d.func)
        if dn not in ("pytest.mark.parametrize", "mark.parametrize", "parametrize"):
            continue
        if any(kw.arg == "ids" for kw in d.keywords if kw.arg):
            continue
        if len(d.args) < 2:
            continue
        cases_arg = d.args[1]
        # C45: an empty argvalues list means pytest generates zero cases — the
        # test is collected but never runs, and the suite stays green. high.
        if isinstance(cases_arg, (ast.List, ast.Tuple)) and len(cases_arg.elts) == 0:
            findings.append(Finding(file, d.lineno, "C45",
                                    "empty parametrize list — the test runs zero times"))
        if isinstance(cases_arg, (ast.List, ast.Tuple)) and len(cases_arg.elts) > 2:
            findings.append(Finding(file, d.lineno, "D4",
                                    "%d cases without ids=" % len(cases_arg.elts)))

        # C37: duplicate case in the same @pytest.mark.parametrize call.
        # Compare every argument set pairwise by literal VALUE (not adjacency,
        # not ast.dump): two cases collide when their normalized literal values
        # are equal, wherever they sit in the list. FP guard: only fully literal
        # sets take part — a set with any Name/call/dynamic value (or a dynamic
        # pytest.param id) is skipped, since two such cases may resolve to
        # different runtime values and are not a provable duplicate.
        if isinstance(cases_arg, (ast.List, ast.Tuple)):
            _seen = set()
            for _elt in cases_arg.elts:
                _key = _parametrize_literal_key(_elt)
                if _key is None:
                    continue  # dynamic case: cannot prove a duplicate, skip it
                if _key in _seen:
                    findings.append(Finding(file, d.lineno, "C37",
                                            "duplicate parametrize case — same argument set runs the same scenario twice"))
                    break
                _seen.add(_key)

    # C30: responses.add() / httpretty.register_uri() without activating the library
    # interceptor. Without @responses.activate (or an equivalent context manager), the
    # mock response is registered but HTTP calls bypass it and hit the real network.
    # The test passes only when the real server is up and returning the expected data.
    _has_responses_add = any(
        isinstance(n, ast.Call) and dotted_name(n.func) in RESPONSES_SETUP_CALLS
        for n in children_no_nesting(func)
    )
    if _has_responses_add:
        _interceptor_active = False
        for d in func.decorator_list:
            dn = dotted_name(d.func if isinstance(d, ast.Call) else d)
            if dn.endswith("activate") and ("responses" in dn or "httpretty" in dn):
                _interceptor_active = True
                break
        if not _interceptor_active:
            for n in children_no_nesting(func):
                if isinstance(n, ast.Call):
                    dn = dotted_name(n.func)
                    if dn in ("responses.start", "httpretty.enable"):
                        _interceptor_active = True
                        break
                if isinstance(n, ast.With):
                    for item in n.items:
                        ce = item.context_expr
                        dn = dotted_name(ce.func if isinstance(ce, ast.Call) else ce)
                        if "responses" in dn or "httpretty" in dn:
                            _interceptor_active = True
                            break
                if _interceptor_active:
                    break
        if not _interceptor_active:
            for n in children_no_nesting(func):
                if isinstance(n, ast.Call) and dotted_name(n.func) in RESPONSES_SETUP_CALLS:
                    findings.append(Finding(file, n.lineno, "C30"))

    # C31: capsys/capfd.readouterr() called but its result is never asserted.
    # The test captures stdout/stderr yet verifies nothing about the content —
    # the capture has no effect on pass/fail, making the test a false green.
    # Two patterns: result discarded entirely (bare Expr statement), or result
    # assigned to a name that never appears inside an assert.
    _assert_names = {
        sub.id
        for node in ast.walk(func)
        if isinstance(node, ast.Assert)
        for sub in ast.walk(node.test)
        if isinstance(sub, ast.Name)
    }
    # C31/C50 also honor the delegate-pattern exemption: a captured value handed to
    # a helper call counts as used, not discarded (see names_reaching_assert_or_call).
    _used_names = names_reaching_assert_or_call(func)
    for n in children_no_nesting(func):
        if isinstance(n, ast.Expr) \
                and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Attribute) \
                and n.value.func.attr == "readouterr":
            findings.append(Finding(file, n.lineno, "C31",
                                    "readouterr() result discarded — nothing is verified"))
        elif isinstance(n, ast.Assign) \
                and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Attribute) \
                and n.value.func.attr == "readouterr":
            names = set()
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
                elif isinstance(tgt, ast.Tuple):
                    for elt in tgt.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
            if names and not (names & _used_names):
                findings.append(Finding(file, n.lineno, "C31",
                                        "readouterr() result captured but never asserted"))

    # C50: log output captured but never asserted (logging analogue of C31).
    # Two shapes: `with self.assertLogs(...) as cm` where cm is never read in any
    # assertion, or `with caplog.at_level(...)` / a `caplog`-rooted read where the
    # caplog fixture name never reaches an assertion. The capture has no effect on
    # pass/fail. _used_names covers assert tests plus any name passed to a Call
    # (xunit assert*, mock-asserts, and verification helpers via the delegate
    # exemption), so a caplog handed to verify_logs(caplog) counts as read.
    _assert_read_names = _used_names
    for n in children_no_nesting(func):
        if not isinstance(n, ast.With):
            continue
        for item in n.items:
            ce = item.context_expr
            # xunit: with self.assertLogs(...) [as cm]
            if isinstance(ce, ast.Call) and isinstance(ce.func, ast.Attribute) \
                    and ce.func.attr in ("assertLogs", "assertNoLogs") \
                    and isinstance(ce.func.value, ast.Name) \
                    and ce.func.value.id in ("self", "cls"):
                if ce.func.attr == "assertNoLogs":
                    break  # assertNoLogs is itself the assertion
                bound = item.optional_vars.id \
                    if isinstance(item.optional_vars, ast.Name) else None
                if bound is None or bound not in _assert_read_names:
                    findings.append(Finding(file, n.lineno, "C50",
                                            "assertLogs output captured but never asserted"))
                break
            # pytest: with caplog.at_level(...) / caplog.set_level(...)
            if isinstance(ce, ast.Call) and isinstance(ce.func, ast.Attribute) \
                    and ce.func.attr in ("at_level", "set_level") \
                    and root_name(ce) == "caplog":
                if "caplog" not in _assert_read_names:
                    findings.append(Finding(file, n.lineno, "C50",
                                            "caplog output captured but never asserted"))
                break

    # C33: sklearn/ML metric result never asserted. Calling accuracy_score(),
    # f1_score(), model.score(), etc. without asserting on the return value means
    # the test passes regardless of the model's actual performance — a model with
    # 10% accuracy passes as easily as one with 95%. Two patterns: result
    # discarded entirely (bare Expr), or assigned to a name never used in assert.
    # The generic `.score()` method only counts when the file imports sklearn
    # (imports_sklearn); otherwise it is an ordinary method (game.score(),
    # vectorizer.score()) and flagging it is a false positive. The named free
    # functions stay ungated — their names are unambiguously sklearn.
    for n in children_no_nesting(func):
        _is_metric_call = False
        if isinstance(n.value if isinstance(n, (ast.Expr, ast.Assign)) else n, ast.Call):
            call_node = n.value if isinstance(n, (ast.Expr, ast.Assign)) else None
            if call_node is not None and isinstance(call_node, ast.Call):
                func_name = dotted_name(call_node.func).split(".")[-1]
                is_method = isinstance(call_node.func, ast.Attribute)
                if func_name in ML_METRIC_FUNCTIONS or \
                        (is_method and func_name in ML_SCORE_METHODS
                         and imports_sklearn):
                    _is_metric_call = True
        if not _is_metric_call:
            continue
        call_node = n.value
        if isinstance(n, ast.Expr):
            findings.append(Finding(file, n.lineno, "C33",
                                    "metric result discarded — no threshold checked"))
        elif isinstance(n, ast.Assign):
            names = set()
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
                elif isinstance(tgt, ast.Tuple):
                    for elt in tgt.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
            if names and not (names & _assert_names):
                findings.append(Finding(file, n.lineno, "C33",
                                        "metric result captured but never asserted"))

    # M2: test function body exceeds the configured line-count threshold.
    # A very long test almost always does more than one thing, which makes
    # failures hard to pinpoint and refactoring costly.
    if long_test_threshold > 0:
        end = getattr(func, "end_lineno", None)
        if end is not None:
            n_lines = end - func.lineno + 1
            if n_lines > long_test_threshold:
                findings.append(Finding(file, line, "M2",
                                        "%d lines (threshold: %d)" % (n_lines, long_test_threshold)))

    # C36: pytest.fail() with no reason. An empty failure message leaves no
    # clue for the developer who sees the red build. At minimum add a short
    # string explaining what invariant was violated.
    for n in children_no_nesting(func):
        if not (isinstance(n, ast.Call) and
                dotted_name(n.func) == "pytest.fail"):
            continue
        if n.args or any(kw.arg in ("reason", "msg") for kw in n.keywords):
            continue
        findings.append(Finding(file, n.lineno, "C36",
                                "add a descriptive reason to pytest.fail()"))

    # D6: print() calls in a test body. Print statements left after debugging
    # bypass the test oracle: they produce output but check nothing, and pollute
    # CI logs. Off by default; enable with D6 = "info" in severity config.
    for n in children_no_nesting(func):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) \
                and dotted_name(n.value.func) == "print":
            findings.append(Finding(file, n.lineno, "D6",
                                    "remove print() or replace with a proper assertion"))

    # D5: too many inline setup statements before the first assert. A test that
    # creates objects and transforms data directly in its body, rather than
    # delegating to a fixture, tangles the "arrange" and "act" phases and makes
    # it hard to see what is actually under test.
    if inline_setup_threshold > 0:
        _setup_n = 0
        for _stmt in func.body:
            if isinstance(_stmt, ast.Assert):
                if _setup_n >= inline_setup_threshold:
                    findings.append(Finding(file, line, "D5",
                                            "%d setup statements before first assert"
                                            % _setup_n))
                break
            if isinstance(_stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                _setup_n += 1
            elif isinstance(_stmt, ast.Expr) and isinstance(_stmt.value, ast.Call):
                _setup_n += 1


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------
def looks_like_loose_test(func):
    """Out-of-convention function that looks like a forgotten test."""
    name = func.name.lower()
    if name.startswith(HELPER_PREFIXES):
        return False
    args = func.args
    n_pos = len(args.args)
    if n_pos > 0 and args.args[0].arg in ("self", "cls"):
        n_pos -= 1
    if n_pos != 0 or args.vararg or args.kwarg or args.kwonlyargs:
        return False
    for n in ast.walk(func):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Call) and is_call_to(n, "pytest.raises", "raises"):
            return True
    return False


def has_property_test_decorator(func):
    """The test is a property/fuzz test driven by a framework that generates
    inputs and runs the body many times: hypothesis `@given`, `@fuzz`, or a
    `@hypothesis...` decorator. A body with no explicit assert is idiomatic
    there - the implicit oracle is 'no exception over all generated inputs' - so
    it is not an empty/checks-nothing test."""
    for d in func.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        name = dotted_name(target)
        last = name.split(".")[-1]
        if last in ("given", "fuzz") or "hypothesis" in name:
            return True
    return False


SKIP_MARKERS = {"skip", "skipif", "skipIf", "skipUnless", "skipTest", "xfail", "SKIP"}


def has_skip_decorator(func):
    """The test is decorated to skip or expect-failure: `@pytest.mark.skip`,
    `@skipif`, `@unittest.skipUnless`, `@pytest.mark.xfail`, etc. An empty body
    under such a marker is a deliberate placeholder ("not implemented yet" /
    "known-broken"), not a rotten-green test, because the marker stops it from
    running and passing silently."""
    for d in func.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if dotted_name(target).split(".")[-1] in SKIP_MARKERS:
            return True
    return False


def is_pytest_test_file(file):
    """A file pytest collects by default: test_*.py, *_test.py, or conftest.py.
    A loose, non-`test_`-named function only counts as a forgotten test when it
    lives in a collected file. In a helper/fixtures/example module (e.g.
    `_concurrency_fixtures.py`, a perf `command.py`) such a function is never a
    test pytest would have run, so it is not 'forgotten'."""
    base = os.path.basename(file)
    return (base == "conftest.py"
            or (base.startswith("test_") and base.endswith(".py"))
            or base.endswith("_test.py"))


def _is_xfail_without_strict(decorator):
    """Returns True if the decorator is @pytest.mark.xfail (or any import alias) without
    strict=True. A non-strict xfail treats XPASS (unexpected pass) as success, masking
    bugs that were fixed without the test being promoted to a proper passing test."""
    if isinstance(decorator, ast.Call):
        target = decorator.func
        keywords = decorator.keywords
    else:
        target = decorator
        keywords = []
    if dotted_name(target).split(".")[-1] != "xfail":
        return False
    for kw in keywords:
        if kw.arg == "strict":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is True)
    return True


def _is_skip_without_reason(decorator):
    """Returns True if the decorator is @pytest.mark.skip (bare @skip or call
    without reason=). A skip without a reason makes it impossible to know when
    the test should be re-enabled and may silently hide a permanently broken suite.
    Does NOT flag skipif/skipUnless — those carry a condition by design."""
    if isinstance(decorator, ast.Call):
        target = decorator.func
        keywords = decorator.keywords
    else:
        target = decorator
        keywords = []
    if dotted_name(target).split(".")[-1] != "skip":
        return False
    for kw in keywords:
        if kw.arg == "reason":
            return False
    return True


def _is_retry_marker(decorator):
    """True if the decorator marks the test for automatic retry on failure.

    Covers @pytest.mark.flaky, @pytest.mark.repeat, @flaky, @retry, etc.
    A retry loop makes the test report green on the Nth attempt, masking a
    flaky SUT instead of surfacing the root cause."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return dotted_name(target).split(".")[-1] in RETRY_MARKER_NAMES


def _suboptimal_assert_hint(test):
    """If `test` is a known suboptimal assert form, return a short hint; else None.

    Patterns (TS11 / detectable subset of TS05):
    - `assert not x in y`   →  use `assert x not in y`
    - `assert len(x) == 0`  →  use `assert not x`
    - `assert x == True`    →  use `assert x`
    - `assert x == False`   →  use `assert not x`
    - `assert x == None`    →  use `assert x is None`
    - `assert x != None`    →  use `assert x is not None`
    Literal on either side is checked (e.g. `True == x` also triggers).
    Does not fire when C5/C7 already own the node (called only in their else branch).
    """
    # assert not (x in y)  →  assert x not in y
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = test.operand
        if isinstance(inner, ast.Compare) and len(inner.ops) == 1 \
                and isinstance(inner.ops[0], ast.In):
            return "use `assert x not in y` instead of `assert not x in y`"

    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return None

    op, left, right = test.ops[0], test.left, test.comparators[0]

    # assert len(x) == 0  /  assert 0 == len(x)
    if isinstance(op, ast.Eq):
        if isinstance(left, ast.Call) \
                and dotted_name(left.func).split(".")[-1] == "len" \
                and isinstance(right, ast.Constant) \
                and right.value == 0 and not isinstance(right.value, bool):
            return "use `assert not x` instead of `assert len(x) == 0`"
        if isinstance(right, ast.Call) \
                and dotted_name(right.func).split(".")[-1] == "len" \
                and isinstance(left, ast.Constant) \
                and left.value == 0 and not isinstance(left.value, bool):
            return "use `assert not x` instead of `assert 0 == len(x)`"

    # Check both orderings for boolean/None constants (Eq and NotEq).
    # `v is True/False/None` correctly distinguishes from numeric 0/1 since
    # booleans are singletons: `0 is False` → False, `1 is True` → False.
    for const_node, _ in ((left, right), (right, left)):
        if not isinstance(const_node, ast.Constant):
            continue
        v = const_node.value
        if isinstance(op, ast.Eq):
            if v is True:
                return "use `assert x` instead of `assert x == True`"
            if v is False:
                return "use `assert not x` instead of `assert x == False`"
            if v is None:
                return "use `assert x is None` instead of `assert x == None`"
        elif isinstance(op, ast.NotEq):
            if v is None:
                return "use `assert x is not None` instead of `assert x != None`"

    return None


def is_fixture_decorator(node):
    """True if a decorator is a fixture DEFINITION marker: the leaf of its dotted
    name is exactly `fixture` (@pytest.fixture, @fixture, @pytest_asyncio.fixture).
    A substring check would also catch @pytest.mark.usefixtures, which decorates a
    real test case and must NOT switch analysis off."""
    target = node.func if isinstance(node, ast.Call) else node
    return dotted_name(target).split(".")[-1] == "fixture"


def has_fixture_decorator(func):
    return any(is_fixture_decorator(d) for d in func.decorator_list)


def parse_inline_ignores(source):
    ignores = {}
    for i, line in enumerate(source.splitlines(), start=1):
        m = IGNORE_RE.search(line)
        if not m:
            continue
        codes = m.group(1)
        if codes:
            ignores[i] = {c.strip() for c in codes.split(",") if c.strip()}
        else:
            ignores[i] = {"*"}
    return ignores


def is_testcase_subclass(node):
    """Return True if a ClassDef inherits from unittest.TestCase or known Django/etc. variants."""
    for base in node.bases:
        name = dotted_name(base)
        if name in ("TestCase", "unittest.TestCase", "SimpleTestCase",
                    "django.test.TestCase", "django.test.SimpleTestCase",
                    "django.test.TransactionTestCase"):
            return True
    return False


def analyze_file(file, long_test_threshold=50, inline_setup_threshold=5):
    findings = []
    try:
        with open(file, "r", encoding="utf-8") as fh:
            source = fh.read()
    except Exception:
        return findings
    try:
        tree = ast.parse(source, filename=file)
    except SyntaxError:
        return findings

    collected = is_pytest_test_file(file)
    layer = detect_file_layer(tree)
    level = detect_pyramid_level(tree)
    time_controlled = file_controls_time(tree)
    sklearn_imported = file_imports_sklearn(tree)
    async_def_names = file_async_def_names(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A top-level `test*` function is only a real (rotten-green) test if
            # pytest would collect the file. A `def test_...` in a non-test module
            # (a lint/format fixture like pylint's tests/functional/*.py or black's
            # tests/data/cases/*.py, or a plain helper module) is never run, so its
            # empty/weak body is not a false-green test.
            # A @pytest.fixture named test_* is not collected as a test (the
            # has_fixture_decorator guard was only on the C4 branch). FP-4.
            if node.name.startswith("test") and collected                     and not has_fixture_decorator(node):
                analyze_function(node, file, findings, file_layer=layer,
                                 controls_time=time_controlled,
                                 long_test_threshold=long_test_threshold,
                                 inline_setup_threshold=inline_setup_threshold,
                                 async_names=async_def_names,
                                 imports_sklearn=sklearn_imported)
            elif is_pytest_test_file(file) and looks_like_loose_test(node) \
                    and not has_fixture_decorator(node) \
                    and not is_web_route_handler(node) \
                    and not name_used_at_module_level(tree, node.name):
                findings.append(Finding(file, node.lineno, "C4",
                                        "'%s' does not start with test_, pytest skips it" % node.name))
        elif isinstance(node, ast.ClassDef):
            if (node.name.startswith("Test") or is_testcase_subclass(node)) and collected:
                has_init = any(
                    isinstance(m, ast.FunctionDef) and m.name == "__init__"
                    for m in node.body
                )
                if has_init:
                    findings.append(Finding(file, node.lineno, "C4b",
                                            "test class with __init__ is collected only if subclassed"))
                # a class-level skip/xfail marker makes every empty method in it a
                # deliberate placeholder, not a rotten-green test.
                class_skipped = has_skip_decorator(node)
                for d in node.decorator_list:
                    if _is_xfail_without_strict(d):
                        findings.append(Finding(file, node.lineno, "C25",
                                                "class-level xfail: add strict=True"))
                        break
                for d in node.decorator_list:
                    if _is_skip_without_reason(d):
                        findings.append(Finding(file, node.lineno, "C32",
                                                "class-level skip: add reason= to document why"))
                        break
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if m.name.startswith("test"):
                            analyze_function(m, file, findings, in_class=True,
                                             skip_exempt=class_skipped,
                                             file_layer=layer,
                                             controls_time=time_controlled,
                                             long_test_threshold=long_test_threshold,
                                             inline_setup_threshold=inline_setup_threshold,
                                             async_names=async_def_names,
                                             imports_sklearn=sklearn_imported)

    # C38: two test functions/methods in the same scope share a name. Python
    # binds the later def over the earlier, so the first test silently never runs
    # — it disappears from the suite with no error. Checked at module scope and
    # inside each class body.
    if collected:
        def _flag_duplicate_test_names(defs):
            seen = {}
            for d in defs:
                if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and d.name.startswith("test"):
                    if d.name in seen:
                        findings.append(Finding(
                            file, d.lineno, "C38",
                            "'%s' is defined again here — the earlier test never runs" % d.name))
                    seen[d.name] = d.lineno
        _flag_duplicate_test_names(tree.body)
        for _node in tree.body:
            # Only classes pytest actually collects: Test*-named or TestCase
            # subclasses. A plain helper class with duplicate test_* methods is
            # not collected, so its duplicates are not a vanished test (no C38).
            if isinstance(_node, ast.ClassDef) \
                    and (_node.name.startswith("Test") or is_testcase_subclass(_node)):
                _flag_duplicate_test_names(_node.body)

    # C24: module-level mutable global mutated inside a test function — the
    # mutation outlives the test and can pollute later tests in the same session.
    # Only fires when at least one test function writes to the global directly
    # (append/update/setitem/augassign); globals reset by an autouse fixture are
    # excluded because the fixture provides the required teardown.
    _globals = _module_mutable_bindings(tree)
    if _globals:
        _autouse_reset = _autouse_fixture_resets(tree, _globals)
        _effective = _globals - _autouse_reset
        if _effective:
            _test_funcs = [
                n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name.startswith("test")
            ]
            _mutated_by_tests = set()
            for _tf in _test_funcs:
                _mutated_by_tests |= _mutated_module_globals(_tf, _effective)
            for _stmt in tree.body:
                if not isinstance(_stmt, (ast.Assign, ast.AnnAssign)):
                    continue
                _targets = _stmt.targets if isinstance(_stmt, ast.Assign) else [_stmt.target]
                for _tgt in _targets:
                    if isinstance(_tgt, ast.Name) and _tgt.id in _mutated_by_tests:
                        findings.append(Finding(
                            file, _stmt.lineno, "C24",
                            "'%s' is module-level mutable state mutated by a test — "
                            "can leak between test runs" % _tgt.id,
                        ))

    for i, line in enumerate(source.splitlines(), start=1):
        if re.match(r"^\s*#\s*assert\b", line):
            findings.append(Finding(file, i, "CC"))

    ignores = parse_inline_ignores(source)
    src_lines = source.splitlines()
    kept = []
    for f in findings:
        spec = ignores.get(f.line)
        if spec and ("*" in spec or f.code in spec):
            continue
        if 1 <= f.line <= len(src_lines):
            f.snippet = " ".join(src_lines[f.line - 1].split())
        f.layer = layer
        f.level = level
        kept.append(f)
    return kept


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _rel_uri(path):
    """A forward-slash relative URI (load-bearing for GitHub code scanning)."""
    try:
        rel = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        rel = path
    return rel.replace("\\", "/")


def render_text(findings):
    if not findings:
        return "No false-positive patterns found in the analyzed tests."
    highs = [a for a in findings if a.conf == "high"]
    lows = [a for a in findings if a.conf == "low"]
    diags = [a for a in findings if a.conf == "info" and group_of(a.code) == "diagnostic"]
    coups = [a for a in findings if a.conf == "info" and group_of(a.code) == "coupling"]
    out = []

    def block(title, items):
        if not items:
            return
        out.append("\n" + title)
        out.append("-" * len(title))
        for a in sorted(items, key=lambda x: (x.file, x.line)):
            t = CASES[a.code][0]
            extra = ("  (%s)" % a.detail) if a.detail else ""
            out.append("  %s:%d  [%s] %s%s" % (a.file, a.line, a.code, t, extra))
            hint = FIX_HINTS.get(a.code, "")
            if hint:
                out.append("      level: %s   fix: %s" % (a.level, hint))
            else:
                out.append("      level: %s" % a.level)

    block("HIGH confidence (almost certainly a false positive)", highs)
    block("LOW confidence (test smell, confirm by hand or with /falsegreen)", lows)
    block("DIAGNOSTIC (readability - informational, exit 0)", diags)
    block("COUPLING (fragility - informational, exit 0)", coups)
    n_diag, n_coup = len(diags), len(coups)
    summary = "\nSummary: %d high, %d low" % (len(highs), len(lows))
    if n_diag or n_coup:
        summary += ", %d diagnostic, %d coupling" % (n_diag, n_coup)
    out.append(summary + ".")

    # Test-pyramid breakdown + the most common fixes, over the findings shown.
    shown = highs + lows + diags + coups
    if shown:
        by_level = {}
        by_code = {}
        for a in shown:
            by_level[a.level] = by_level.get(a.level, 0) + 1
            by_code[a.code] = by_code.get(a.code, 0) + 1
        order = ["unit", "integration", "e2e"]
        levels = [lv for lv in order if lv in by_level] + \
                 [lv for lv in sorted(by_level) if lv not in order]
        out.append("By level: " + ", ".join("%s:%d" % (lv, by_level[lv]) for lv in levels))
        top = sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        out.append("Top fixes:")
        for code, n in top:
            out.append("  %s (%d): %s" % (code, n, FIX_HINTS.get(code, CASES[code][0])))

    if highs or lows:
        out.append("Cases 12 and 18 (copied logic / wrong expected value) need the semantic")
        out.append("pass: run /falsegreen so the expected value is checked against intent.")
    return "\n".join(out)


def print_text(findings):
    print(render_text(findings))


_OUTPUT_EXT = {"text": "txt", "json": "json", "sarif": "sarif", "junit": "xml"}


def resolve_output_path(path, fmt):
    """Turn --output into a concrete file path. A directory (existing dir, a
    trailing separator, or an extension-less name like '.falsegreen') receives
    'report.<ext>' for the chosen format; anything else is treated as a file.
    Missing parent directories are created either way."""
    ext = _OUTPUT_EXT.get(fmt, "txt")
    base = os.path.basename(path.rstrip("/\\"))
    is_dir = (path.endswith(("/", "\\")) or os.path.isdir(path)
              or os.path.splitext(base)[1] == "")
    if is_dir:
        os.makedirs(path, exist_ok=True)
        return os.path.join(path, "report." + ext)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def render_json(findings):
    return json.dumps([a.dict() for a in findings], ensure_ascii=False, indent=2)


def _sarif_level(conf):
    if conf == "high":
        return "error"
    if conf == "low":
        return "warning"
    return "note"


def render_sarif(findings):
    """SARIF 2.1.0: HIGH -> error, LOW -> warning (via the finding's effective
    conf), forward-slash relative URIs, one tool + one implicit category."""
    codes = []
    for a in findings:
        if a.code not in codes:
            codes.append(a.code)
    rules = []
    for code in codes:
        title, default_conf, judgment = CASES[code]
        # fullDescription = catalog title + the fix hint (when one exists).
        full = title + ((". Fix: " + FIX_HINTS[code]) if code in FIX_HINTS else "")
        # Deep link to the rule on the docs site. The catalog heading slug is
        # <code-lowercased>-<title-words>, which the CASES title cannot reproduce
        # reliably (the docs heading text differs), so anchor to the page + the
        # lowercased code id. This lands on the catalog page, not the exact
        # heading: an intentional approximation, kept deterministic per rule id.
        rules.append({
            "id": code,
            "name": code,
            "shortDescription": {"text": title},
            "fullDescription": {"text": full},
            "defaultConfiguration": {"level": _sarif_level(default_conf)},
            "helpUri": DOCS_CATALOG_URI + "#" + code.lower(),
            "properties": {"tags": [judgment]},
        })
    results = []
    for a in findings:
        text = CASES[a.code][0] + (" (%s)" % a.detail if a.detail else "")
        results.append({
            "ruleId": a.code,
            "level": _sarif_level(a.conf),
            "message": {"text": text},
            "properties": {"tags": [CASES[a.code][2], "layer:" + a.layer,
                                     "level:" + a.level]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": _rel_uri(a.file)},
                    "region": {"startLine": a.line},
                }
            }],
        })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "falsegreen",
                "informationUri": TOOL_URI,
                "version": __version__,
                "rules": rules,
            }},
            "results": results,
        }],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def render_junit(findings):
    """JUnit XML: HIGH -> <failure>, LOW/INFO -> <skipped>. One case per finding."""
    n = len(findings)
    n_high = sum(1 for a in findings if a.conf == "high")
    n_non_high = n - n_high
    attrs = {"name": "falsegreen", "tests": str(n),
             "failures": str(n_high), "skipped": str(n_non_high), "errors": "0"}
    suites = ET.Element("testsuites", attrs)
    suite = ET.SubElement(suites, "testsuite", attrs)
    for a in sorted(findings, key=lambda x: (x.file, x.line)):
        title = CASES[a.code][0] + (" (%s)" % a.detail if a.detail else "")
        case = ET.SubElement(suite, "testcase", {
            "classname": "falsegreen.%s" % a.code,
            "name": "%s %s:%d" % (a.code, _rel_uri(a.file), a.line),
        })
        loc = "%s:%d" % (_rel_uri(a.file), a.line)
        if a.conf == "high":
            el = ET.SubElement(case, "failure", {"message": title})
            el.text = loc
        else:
            ET.SubElement(case, "skipped", {"message": "%s  %s" % (title, loc)})
    xml = ET.tostring(suites, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml


def summary_line(findings, n_files):
    n_high = sum(1 for a in findings if a.conf == "high")
    n_low = sum(1 for a in findings if a.conf == "low")
    n_info = sum(1 for a in findings if a.conf == "info")
    by_code = {}
    by_judgment = {}
    for a in findings:
        by_code[a.code] = by_code.get(a.code, 0) + 1
        j = CASES[a.code][2]
        by_judgment[j] = by_judgment.get(j, 0) + 1
    breakdown = " ".join("%s:%d" % (c, by_code[c]) for c in sorted(by_code))
    line = "falsegreen: scanned %d test file(s), %d finding(s) [%d high, %d low" % (
        n_files, len(findings), n_high, n_low)
    if n_info:
        line += ", %d info" % n_info
    line += "]"
    out = line + ("  " + breakdown if breakdown else "")
    if by_judgment:
        out += "\n  by judgment: " + " ".join(
            "%s:%d" % (j, by_judgment[j]) for j in sorted(by_judgment))
    return out


# ---------------------------------------------------------------------------
# Baseline (ratchet): fingerprint by content, not line number
# ---------------------------------------------------------------------------
def fingerprint(finding):
    """Stable id: sha1(relpath, code, detail, normalized snippet)[:16]. No line
    number, so the fingerprint survives unrelated line shifts in the file."""
    key = "\0".join([
        _rel_uri(finding.file), finding.code,
        finding.detail or "", finding.snippet or "",
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def load_baseline(path):
    """Read a baseline file into a set of fingerprints (empty set if unreadable)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return set()
    return {item["fingerprint"] for item in data.get("findings", [])
            if isinstance(item, dict) and item.get("fingerprint")}


def write_baseline(path, findings):
    """Write all current findings as a baseline. Returns how many were recorded."""
    items = [{
        "fingerprint": fingerprint(a),
        "code": a.code,
        "file": _rel_uri(a.file),
        "detail": a.detail,
    } for a in sorted(findings, key=lambda x: (x.file, x.line))]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "tool": "falsegreen", "findings": items},
                  fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return len(items)


def run(paths, staged=False, disable=None, config=None, config_path=None,
        stats=None, baseline=None):
    cli_disable = set(disable or [])
    if config is None:
        config = load_config(explicit=config_path)
    if staged:
        files = staged_files()
    elif paths:
        files = discover(paths)
    else:
        files = discover(["."])
    files = _apply_exclude(files, config.get("exclude", []))
    if stats is not None:
        stats["files"] = len(files)

    thresh = config.get("long_test_threshold", 50)
    setup_thresh = config.get("inline_setup_threshold", 5)
    findings = []
    seen = set()
    for f in files:
        for a in analyze_file(f, long_test_threshold=thresh, inline_setup_threshold=setup_thresh):
            conf = effective_conf(a.code, config, cli_disable)
            if conf == "off":
                continue
            a.conf = conf
            key = (a.file, a.line, a.code, a.detail)
            if key in seen:
                continue
            seen.add(key)
            findings.append(a)
    if baseline:
        findings = [a for a in findings if fingerprint(a) not in baseline]
    return findings


# ---------------------------------------------------------------------------
# Project-layer audit (--config-audit): the suite goes green by configuration,
# not by a smell inside any one test file. Reads the pytest/coverage config.
# ---------------------------------------------------------------------------
def _pytest_options(start):
    """Locate the pytest config in `start` and return (path, opts) where opts has
    'addopts' (str), 'filterwarnings' (list[str]) and 'cov_gate' (bool: a coverage
    threshold is configured somewhere). Returns (None, None) if no pytest config
    is found. Searches pyproject.toml, pytest.ini, tox.ini, setup.cfg in order."""
    import configparser

    def _cov_in_pyproject(raw):
        rep = (raw.get("tool", {}).get("coverage", {}).get("report", {})
               if isinstance(raw, dict) else {})
        return isinstance(rep, dict) and rep.get("fail_under") is not None

    pp = os.path.join(start, "pyproject.toml")
    if os.path.isfile(pp):
        raw = _read_toml(pp) or {}
        ini = raw.get("tool", {}).get("pytest", {}).get("ini_options")
        if ini is not None:
            addopts = ini.get("addopts", "")
            if isinstance(addopts, list):
                addopts = " ".join(str(a) for a in addopts)
            fw = ini.get("filterwarnings", [])
            fw = [fw] if isinstance(fw, str) else [str(x) for x in (fw or [])]
            cov = "--cov-fail-under" in addopts or _cov_in_pyproject(raw)
            return pp, {"addopts": addopts, "filterwarnings": fw, "cov_gate": cov}

    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"),
                          ("setup.cfg", "tool:pytest")):
        path = os.path.join(start, name)
        if not os.path.isfile(path):
            continue
        cp = configparser.ConfigParser()
        try:
            cp.read(path, encoding="utf-8")
        except Exception:
            continue
        if not cp.has_section(section):
            continue
        addopts = cp.get(section, "addopts", fallback="")
        fw_raw = cp.get(section, "filterwarnings", fallback="")
        fw = [ln.strip() for ln in fw_raw.splitlines() if ln.strip()]
        cov = "--cov-fail-under" in addopts
        if not cov and cp.has_section("coverage:report"):
            cov = cp.get("coverage:report", "fail_under", fallback="") != ""
        return path, {"addopts": addopts, "filterwarnings": fw, "cov_gate": cov}

    return None, None


_OPTIMIZE_RE = re.compile(r"(?:^|\s)-OO?(?:\s|$)|PYTHONOPTIMIZE\s*=\s*[1-9]")


def _config_strips_asserts(path, addopts):
    """True if the resolved pytest config turns asserts off at runtime: a python
    -O/-OO flag or PYTHONOPTIMIZE set, in addopts or anywhere in the config file
    (a tox commands = python -O -m pytest). Reads the single file --config-audit
    already located, so detection stays scoped to parsed config, not CI YAML."""
    if _OPTIMIZE_RE.search(addopts or ""):
        return True
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return False
    return _OPTIMIZE_RE.search(text) is not None


def audit_config(start=None):
    """Project-layer audit. Read the pytest/coverage config and report the PL
    codes: ways the suite can report green by configuration. Findings carry the
    config file as `file` and level 'project'. Returns [] if no pytest config."""
    base = start or os.getcwd()
    path, opts = _pytest_options(base)
    findings = []
    if not path:
        return findings
    addopts = opts["addopts"] or ""
    # PL1: python -O / -OO or PYTHONOPTIMIZE anywhere in the resolved config file
    # strips every assert at runtime, so the suite reports green with no checks.
    # Scoped to the file --config-audit already parses (a tox commands = python -O
    # -m pytest, an addopts, or a [pytest]/[tool:pytest] section); CI YAML is out.
    if _config_strips_asserts(path, addopts):
        findings.append(Finding(path, 1, "PL1"))
    promotes = any(f.split(":", 1)[0].strip() == "error" for f in opts["filterwarnings"])
    if not promotes:
        findings.append(Finding(path, 1, "PL2"))
    if not opts["cov_gate"]:
        findings.append(Finding(path, 1, "PL7"))
    if re.search(r"(^|\s)(-x|--exitfirst|--maxfail)\b", addopts):
        findings.append(Finding(path, 1, "PL8"))
    for f in findings:
        f.level = "project"
        f.layer = "config"
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="falsegreen",
                                 description="False-positive scanner for Python tests.")
    ap.add_argument("paths", nargs="*", help="files or dirs (empty = cwd)")
    ap.add_argument("--staged", action="store_true", help="only test files staged in git")
    ap.add_argument("--format", choices=["text", "json", "sarif", "junit"], default="text",
                    help="output format (default: text)")
    ap.add_argument("--json", action="store_true", help="alias for --format json")
    ap.add_argument("--summary", action="store_true",
                    help="print a one-line scan summary to stderr")
    ap.add_argument("--output", default=None, metavar="PATH",
                    help="write the formatted output to PATH instead of stdout; "
                         "a directory (e.g. .falsegreen/) gets report.<ext>")
    ap.add_argument("--config-audit", action="store_true",
                    help="audit the project's pytest/coverage config for project-layer "
                         "false-green (PL codes) instead of scanning test files")
    ap.add_argument("--disable", default="", help="comma-separated case codes to skip (e.g. C6,C2b)")
    ap.add_argument("--config", default=None,
                    help="path to a .falsegreen.toml or pyproject.toml (default: auto-discover in cwd)")
    ap.add_argument("--baseline", nargs="?", const=".falsegreen-baseline.json", default=None,
                    metavar="PATH",
                    help="suppress findings recorded in PATH (default .falsegreen-baseline.json); "
                         "fail only on findings not in the baseline")
    ap.add_argument("--write-baseline", nargs="?", const=".falsegreen-baseline.json", default=None,
                    metavar="PATH",
                    help="record all current findings to PATH as a baseline, then exit 0")
    args = ap.parse_args(argv)

    disable = {c.strip() for c in args.disable.split(",") if c.strip()}
    stats = {}

    if args.write_baseline is not None:
        findings = run(args.paths, staged=args.staged, disable=disable,
                       config_path=args.config, stats=stats)
        n = write_baseline(args.write_baseline, findings)
        sys.stderr.write("falsegreen: wrote %d fingerprint(s) to %s\n"
                         % (n, args.write_baseline))
        return 0

    if args.config_audit:
        base = next((p for p in args.paths if os.path.isdir(p)), None) or os.getcwd()
        findings = audit_config(base)
        fmt = "json" if args.json else args.format
        renderers = {"text": render_text, "json": render_json,
                     "sarif": render_sarif, "junit": render_junit}
        rendered = renderers[fmt](findings)
        if args.output:
            dest = resolve_output_path(args.output, fmt)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(rendered + "\n")
        else:
            print(rendered)
        return 10 if findings else 0

    baseline = load_baseline(args.baseline) if args.baseline else None
    findings = run(args.paths, staged=args.staged, disable=disable,
                   config_path=args.config, stats=stats, baseline=baseline)

    fmt = "json" if args.json else args.format
    renderers = {"text": render_text, "json": render_json,
                 "sarif": render_sarif, "junit": render_junit}
    rendered = renderers[fmt](findings)

    if args.output:
        dest = resolve_output_path(args.output, fmt)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
        if args.summary:
            sys.stderr.write("falsegreen: wrote %s to %s\n" % (fmt, dest))
    else:
        print(rendered)

    if args.summary:
        sys.stderr.write(summary_line(findings, stats.get("files", 0)) + "\n")

    has_high = any(a.conf == "high" for a in findings)
    has_low = any(a.conf == "low" for a in findings)
    if has_high:
        return 20
    if has_low:
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main())
