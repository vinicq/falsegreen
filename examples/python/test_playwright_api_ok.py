# falsegreen examples - C6 softening: `<resp>.ok` is a real API/HTTP oracle.
#
# Code: C6 (weak check), layer-aware softening (issue #20 lineage).
#
# `APIResponse.ok` (Playwright), `Response.ok` (requests), and Selenium's
# APIResponse `.ok` are all a 2xx boolean property. In a web/browser test,
# `assert <resp>.ok` IS the success check, not a weak "something came back", so
# it is softened regardless of the response variable's name. The softening
# anchors on the `.ok` attribute FORM and stays gated to web/browser context.
#
# Each BAD function is flagged; each CLEAN look-alike is left alone. The scanner
# only reads the syntax tree, so the undefined helpers never run.
from playwright.sync_api import APIRequestContext


# --- CLEAN: the idiomatic API-response success assertion ---------------------

# CLEAN: `.ok` is the 2xx status oracle; the variable name is irrelevant.
def test_api_response_ok_clean(api_request_context: APIRequestContext):
    new_issue = api_request_context.post("/issues", data={})
    assert new_issue.ok

# CLEAN: same, with a get. `.ok` softened even though `issues` is not a
# "response"-named variable.
def test_api_get_ok_clean(api_request_context: APIRequestContext):
    issues = api_request_context.get("/issues")
    assert issues.ok


# --- BAD: genuinely weak checks that sit one token from the CLEAN `.ok` -------

# BAD: bare truthiness of the response object, not its status. One token from
# `assert new_issue.ok`, but it only proves an object came back.
def test_api_bare_response_truthiness(api_request_context: APIRequestContext):
    new_issue = api_request_context.post("/issues", data={})
    assert new_issue                   # C6 - truthiness, not the status oracle

# BAD: `.ok` is a property, not callable. The Call form is left flagged on
# purpose (the fix softens the attribute form only, not the Call).
def test_api_ok_called_as_method(api_request_context: APIRequestContext):
    new_issue = api_request_context.post("/issues", data={})
    assert new_issue.ok()              # C6 - .ok() misuse, Call form not softened
