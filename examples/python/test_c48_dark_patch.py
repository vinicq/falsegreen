# falsegreen examples - C48 dark-patch (test flips a test-mode flag then asserts).
#
# C48 flags a test that forces a known test-mode toggle (an env var or a
# module/settings flag whose name means "we are under test") into test mode and
# then asserts. The test exercises the product's test-only branch (if TESTING: ...)
# instead of the behaviour a real user hits. Detection-only; v1 covers raw writes
# (os.environ[...]=, settings.TESTING=, global TESTING=); the monkeypatch.setenv
# form stays with C29's "use monkeypatch" guidance.
#
# Expected scanner result for this file: C48 on test_dark_patch_env and
# test_dark_patch_flag. The look-alikes below are not C48 - a config value and a
# feature flag are real behaviour, and a flag write with no assertion is setup. The
# os.environ writes still report C29 (env-leak), which is a different smell from the
# dark patch; that is the C48-vs-C29 boundary, not a false positive.
import os


# BAD: forces TESTING=1 then asserts - exercises the product's test-only branch (C48).
def test_dark_patch_env():
    os.environ["TESTING"] = "1"
    assert compute() == "ok"


# BAD: a settings flag named TESTING set to True, then asserted (C48).
def test_dark_patch_flag():
    settings.TESTING = True
    assert compute() == "ok"


# CLEAN: DATABASE_URL is configuration, not a test-mode toggle - no C48.
def test_config_value_clean():
    os.environ["DATABASE_URL"] = "sqlite://"
    assert compute() == "ok"


# CLEAN: a product feature flag is real behaviour under test, not a dark patch - no C48.
def test_feature_flag_clean():
    settings.FEATURE_X = True
    assert compute() == "ok"


# CLEAN: the flag write has no assertion after it - it is setup, not a dark-patch test.
def test_setup_only_clean():
    os.environ["TESTING"] = "1"
    do_setup()
