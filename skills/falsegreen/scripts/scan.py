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

__version__ = "0.1.0"  # keep in lockstep with pyproject / plugin.json (see T1)
TOOL_URI = "https://github.com/vinicq/falsegreen"

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
# see SKILL.md). Lets output/SARIF/docs group findings by category without
# splitting the flat module (the CI drift guard is a byte-for-byte file diff).
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
    "C3":  ("assert inside try whose except swallows the error", "high", "J1"),
    "C4":  ("test is not collected by pytest (silently never runs)", "high", "J1"),
    "C4b": ("test class has __init__ (not collected unless subclassed)", "low", "J1"),
    "C5":  ("always-true check (assert True / tuple / or True)", "high", "J2"),
    "C6":  ("weak check (only verifies that something came back)", "low", "J4"),
    "C7":  ("compares a thing to itself (always matches)", "high", "J2"),
    "C8":  ("exact equality on a float (fails on rounding, not bugs)", "low", "J4"),
    "C9":  ("pytest.raises too broad (accepts any error)", "low", "J4"),
    "C13": ("mock assertion misspelled / not called (always passes)", "high", "J3"),
    "C13b":("patch without autospec (lets mock typos pass)", "low", "J3"),
    "C14": ("golden/snapshot generated from the output itself", "low", "J2"),
    "C16": ("result depends on time, randomness or a fixed sleep", "low", "J1"),
    "C17": ("skip inside a broad except hides a real failure", "high", "J1"),
    "C20": ("assertion in dead code after return/raise/fail (never runs)", "high", "J1"),
    "C21": ("every assertion is conditional, none runs unconditionally", "low", "J1"),
    "CC":  ("commented-out assert (check switched off)", "low", "J1"),
}

# Real mock API assertion methods.
MOCK_ASSERTS_VALID = {
    "assert_called", "assert_called_once", "assert_called_with",
    "assert_called_once_with", "assert_any_call", "assert_has_calls",
    "assert_not_called",
}
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

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "build",
    "dist", "site-packages", ".eggs",
}

IGNORE_RE = re.compile(r"#\s*falsegreen:\s*ignore(?:\[([A-Za-z0-9, ]+)\])?")


# ---------------------------------------------------------------------------
# Config file ([tool.falsegreen] in pyproject.toml, or .falsegreen.toml)
# ---------------------------------------------------------------------------
SEVERITY_VALUES = {"high", "low", "off"}
EMPTY_CONFIG = {"disable": set(), "exclude": [], "severity": {}}


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
        return {"disable": set(), "exclude": [], "severity": {}}
    disable = {str(c) for c in (data.get("disable") or [])}
    exclude = [str(g) for g in (data.get("exclude") or [])]
    severity = {}
    for code, level in (data.get("severity") or {}).items():
        if isinstance(level, str) and level.lower() in SEVERITY_VALUES:
            severity[code] = level.lower()
        else:
            sys.stderr.write(
                "falsegreen: ignoring invalid severity %r for %s (use high|low|off)\n"
                % (level, code))
    return {"disable": disable, "exclude": exclude, "severity": severity}


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
            # `x is x` / `obj.attr is obj.attr` is always true. But `f() is f()`
            # is NOT: it asserts two separate calls return the SAME object, the
            # canonical lru_cache / memoization / singleton identity test. Only
            # flag an `is` self-compare when no call is involved.
            if isinstance(op, ast.Is):
                if any(isinstance(n, ast.Call) for n in ast.walk(test.left)):
                    return False
            return True
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


def assert_weak(test):
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


def assert_exact_float(test):
    if isinstance(test, ast.Compare) and any(isinstance(o, ast.Eq) for o in test.ops):
        sides = [test.left] + list(test.comparators)
        for side in sides:
            if isinstance(side, ast.Constant) and isinstance(side.value, float):
                return True
            if side.__class__.__name__ == "Num" and isinstance(getattr(side, "n", None), float):
                return True
    return False


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
class Finding:
    __slots__ = ("file", "line", "code", "detail", "conf", "snippet")

    def __init__(self, file, line, code, detail=""):
        self.file = file
        self.line = line
        self.code = code
        self.detail = detail
        self.conf = CASES[code][1]  # effective confidence; run() may override it
        self.snippet = ""           # normalized source at the finding; set in analyze_file

    def dict(self):
        title = CASES[self.code][0]
        return {
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "confidence": self.conf,
            "title": title,
            "detail": self.detail,
        }


def has_assertion(func):
    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Call):
            target = dotted_name(n.func)
            if target.endswith("pytest.raises") or target.endswith("raises"):
                return True
            if target.endswith("pytest.fail") or target.endswith("fail"):
                return True
            last = target.split(".")[-1]
            if last in MOCK_ASSERTS_VALID or last.startswith("assert"):
                return True
        if isinstance(n, ast.With):
            for item in n.items:
                if is_call_to(item.context_expr, "pytest.raises", "raises"):
                    return True
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


def block_has_assertion(stmts):
    """True if the block contains a real check, not just any call."""
    for s in stmts:
        for sub in ast.walk(s):
            if isinstance(sub, ast.Assert):
                return True
            if isinstance(sub, ast.Call):
                t = dotted_name(sub.func)
                if t.endswith("raises") or t.split(".")[-1].startswith("assert"):
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


def _stmt_is_terminator(stmt):
    """An unconditional control-flow exit: nothing after it in this block runs."""
    if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        t = dotted_name(stmt.value.func)
        if t.endswith("pytest.fail") or t.split(".")[-1] == "fail":
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
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        t = dotted_name(stmt.value.func)
        last = t.split(".")[-1]
        if last in MOCK_ASSERTS_VALID or last.startswith("assert") \
                or t.endswith("raises") or last == "fail":
            return True
    if isinstance(stmt, ast.With):
        for item in stmt.items:
            if is_call_to(item.context_expr, "pytest.raises", "raises"):
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
        if isinstance(n, ast.Call):
            t = dotted_name(n.func)
            last = t.split(".")[-1]
            if last in MOCK_ASSERTS_VALID or last.startswith("assert") \
                    or t.endswith("raises"):
                return True
        if isinstance(n, ast.With):
            for item in n.items:
                if is_call_to(item.context_expr, "pytest.raises", "raises"):
                    return True
    return False


def analyze_function(func, file, findings, in_class=False):
    line = func.lineno
    mock_names = gather_mock_names(func)

    if not has_assertion(func):
        if empty_body(func):
            findings.append(Finding(file, line, "C2"))
        elif makes_any_call(func):
            findings.append(Finding(file, line, "C2b",
                                    "if the check lives in a helper called here, ignore"))

    for n in ast.walk(func):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not func:
            if n.name.startswith("test"):
                findings.append(Finding(file, n.lineno, "C4",
                                        "nested test function is not collected"))

    for n in children_no_nesting(func):
        if isinstance(n, ast.Assert):
            test = n.test
            if assert_always_true(test):
                findings.append(Finding(file, n.lineno, "C5"))
            elif assert_self_compare(test):
                findings.append(Finding(file, n.lineno, "C7"))
            else:
                if assert_exact_float(test):
                    findings.append(Finding(file, n.lineno, "C8"))
                weak = assert_weak(test)
                if weak:
                    findings.append(Finding(file, n.lineno, "C6", weak))

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

    # C20: a check that sits AFTER an unconditional terminator in the same block
    # (return / raise / break / continue / pytest.fail() / assert False) is dead
    # code, it never runs. Scanned per block body so a terminator in one branch
    # does not orphan a sibling at the parent level.
    for body in block_bodies(func):
        for stmt in dead_checks_after_terminator(body):
            findings.append(Finding(file, stmt.lineno, "C20"))

    for n in children_no_nesting(func):
        if isinstance(n, ast.Try):
            body_has_check = block_has_assertion(n.body)
            for h in n.handlers:
                skips = any(
                    is_call_to(c, "pytest.skip", "skip", "skipTest")
                    for c in ast.walk(h) if isinstance(c, ast.Call)
                )
                if skips and handler_broad(h):
                    findings.append(Finding(file, h.lineno, "C17"))
                elif handler_swallows(h) and handler_broad(h) and body_has_check:
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

    has_seed = any(
        is_call_to(c, "random.seed", "seed", "np.random.seed")
        for c in ast.walk(func) if isinstance(c, ast.Call)
    )
    for n in children_no_nesting(func):
        if isinstance(n, ast.Call):
            target = dotted_name(n.func)
            if target.endswith("time.sleep") or target.endswith("sleep"):
                findings.append(Finding(file, n.lineno, "C16", "fixed sleep"))
            elif target.endswith("datetime.now") or target.endswith("datetime.today") \
                    or target.endswith("date.today") or target.endswith("time.time"):
                findings.append(Finding(file, n.lineno, "C16", "reads the system clock"))
            elif (target.startswith("random.") or target.endswith("randint")
                  or target.endswith("choice") or target.endswith("shuffle")) and not has_seed:
                findings.append(Finding(file, n.lineno, "C16", "randomness without a fixed seed"))

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
            if checks_exists and writes:
                findings.append(Finding(file, n.lineno, "C14"))


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


def has_fixture_decorator(func):
    for d in func.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if "fixture" in dotted_name(target):
            return True
    return False


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


def analyze_file(file):
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

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                analyze_function(node, file, findings)
            elif looks_like_loose_test(node) and not has_fixture_decorator(node):
                findings.append(Finding(file, node.lineno, "C4",
                                        "'%s' does not start with test_, pytest skips it" % node.name))
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                has_init = any(
                    isinstance(m, ast.FunctionDef) and m.name == "__init__"
                    for m in node.body
                )
                if has_init:
                    findings.append(Finding(file, node.lineno, "C4b",
                                            "test class with __init__ is collected only if subclassed"))
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if m.name.startswith("test"):
                            analyze_function(m, file, findings, in_class=True)

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

    block("HIGH confidence (almost certainly a false positive)", highs)
    block("LOW confidence (test smell, confirm by hand or with /falsegreen)", lows)
    out.append("\nSummary: %d high, %d low." % (len(highs), len(lows)))
    out.append("Cases 12 and 18 (copied logic / wrong expected value) need the semantic")
    out.append("pass: run /falsegreen so the expected value is checked against intent.")
    return "\n".join(out)


def print_text(findings):
    print(render_text(findings))


def render_json(findings):
    return json.dumps([a.dict() for a in findings], ensure_ascii=False, indent=2)


def _sarif_level(conf):
    return "error" if conf == "high" else "warning"


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
        rules.append({
            "id": code,
            "name": code,
            "shortDescription": {"text": title},
            "defaultConfiguration": {"level": _sarif_level(default_conf)},
            "helpUri": TOOL_URI,
            "properties": {"tags": [judgment]},
        })
    results = []
    for a in findings:
        text = CASES[a.code][0] + (" (%s)" % a.detail if a.detail else "")
        results.append({
            "ruleId": a.code,
            "level": _sarif_level(a.conf),
            "message": {"text": text},
            "properties": {"tags": [CASES[a.code][2]]},
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
    """JUnit XML: HIGH -> <failure>, LOW -> <skipped>. One case per finding."""
    n = len(findings)
    n_high = sum(1 for a in findings if a.conf == "high")
    n_low = n - n_high
    attrs = {"name": "falsegreen", "tests": str(n),
             "failures": str(n_high), "skipped": str(n_low), "errors": "0"}
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
    n_low = len(findings) - n_high
    by_code = {}
    by_judgment = {}
    for a in findings:
        by_code[a.code] = by_code.get(a.code, 0) + 1
        j = CASES[a.code][2]
        by_judgment[j] = by_judgment.get(j, 0) + 1
    breakdown = " ".join("%s:%d" % (c, by_code[c]) for c in sorted(by_code))
    line = "falsegreen: scanned %d test file(s), %d finding(s) [%d high, %d low]" % (
        n_files, len(findings), n_high, n_low)
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

    findings = []
    seen = set()
    for f in files:
        for a in analyze_file(f):
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
                    help="write the formatted output to PATH instead of stdout")
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

    baseline = load_baseline(args.baseline) if args.baseline else None
    findings = run(args.paths, staged=args.staged, disable=disable,
                   config_path=args.config, stats=stats, baseline=baseline)

    fmt = "json" if args.json else args.format
    renderers = {"text": render_text, "json": render_json,
                 "sarif": render_sarif, "junit": render_junit}
    rendered = renderers[fmt](findings)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
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
