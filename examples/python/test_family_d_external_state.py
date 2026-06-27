# falsegreen examples - Family D: green depends on outside factors.
#
# Codes: C23, C24, C30, C31, C32, C35
#
# A real file at a fixed path, module-level shared state, an unactivated HTTP
# interceptor, a discarded output capture, an undocumented skip, or a retry
# decorator that hides non-determinism.
import os

import pytest


# --- C23: opens a real file at a hard-coded literal path ---------------------

# BAD: the path does not exist in CI or on another machine (mystery guest).
def test_c23_literal_path():
    with open("tests/fixtures/config.json") as f:   # C23 - hard-coded path
        data = json.load(f)
    assert data["key"] == "value"

# CLEAN: a path that comes from a fixture binds to nothing on disk.
def test_c23_fixture_path_clean(data_file):
    with open(data_file) as f:
        content = f.read()
    assert content == "ok"

# CLEAN: pytest tmp_path is created per test.
def test_c23_tmp_path_clean(tmp_path):
    p = tmp_path / "out.txt"
    p.write_text("hello")
    with open(p) as f:
        assert f.read() == "hello"


# --- C24: module-level mutable state shared across tests ---------------------

STORE = []   # module-level mutable - shared across every test in this file


# BAD: mutates the shared global, which can leak into later tests.
def test_c24_mutates_global():
    STORE.append(1)                    # C24 - leaks between test runs
    assert len(STORE) == 1


# --- C30: HTTP interceptor registered but never activated --------------------

# BAD: the mock is never activated, so a real HTTP request goes out.
def test_c30_no_activate():
    responses.add(responses.GET, "http://api.example.com/user", json={"id": 1})  # C30
    result = fetch_user(1)
    assert result["id"] == 1

# CLEAN: the decorator activates the interceptor.
@responses.activate
def test_c30_activated_clean():
    responses.add(responses.GET, "http://api.example.com/user", json={"id": 1})
    result = fetch_user(1)
    assert result["id"] == 1


# --- C31: capsys.readouterr() result never asserted --------------------------

# BAD: output is captured but nothing checks it.
def test_c31_discarded(capsys):
    print("hello")
    capsys.readouterr()                # C31 - captured, never asserted

# BAD: assigned but never read in an assertion.
def test_c31_assigned_not_asserted(capsys):
    print("hello")
    captured = capsys.readouterr()     # C31 - captured is never asserted

# CLEAN: the captured output is asserted.
def test_c31_asserted_clean(capsys):
    print("hello")  # falsegreen: ignore[D6]  (print is the subject under test, asserted below)
    captured = capsys.readouterr()
    assert captured.out == "hello\n"


# --- C32: @pytest.mark.skip without reason= ----------------------------------

# BAD: no explanation - the test may be forgotten permanently.
@pytest.mark.skip
def test_c32_no_reason():
    assert compute() == 42             # C32 - silent skip

# CLEAN: a documented reason.
@pytest.mark.skip(reason="blocked by issue #42 - network dependency")
def test_c32_with_reason_clean():
    assert fetch_remote() == 42

# CLEAN: skipif carries a condition, so reason is optional by design.
@pytest.mark.skipif(os.name == "nt", reason="posix only")
def test_c32_skipif_clean():
    assert run_posix() == 0


# --- C35: retry/flaky decorator masks non-determinism ------------------------

# BAD: the retry hides the flakiness instead of fixing its cause.
@pytest.mark.flaky(reruns=3)
def test_c35_flaky():
    result = fetch()                   # C35 - retries paper over the bug
    assert result is not None

# CLEAN: no retry - a deterministic test stands on its own.
def test_c35_stable_clean():
    assert compute() == 42
