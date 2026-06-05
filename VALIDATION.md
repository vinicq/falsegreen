# Validation log

falsegreen is validated against real projects, not just its own test suite. This
file records every project it has been run on, what the run revealed, and the
decision that came out of it. Public open-source projects are named; a few internal
codebases are referred to by shape only.

The method: clone the project, run the scanner (and, for the semantic pass, read a
sample of tests), then classify each finding as a real smell, a false positive in
the tool, or a deliberate exclusion. A false positive at HIGH confidence is the most
serious outcome, because HIGH blocks commits, so those are fixed before anything
else.

## Completed

| Project | Scope | What the run revealed | Decision |
|---|---|---|---|
| internal service suite (pytest, ~200 test files) | scanner + one semantic pass | C6 fired on called boolean predicates (`isinstance`, `.exists()`, `any()`); C1 fired on loops over non-empty literal tuples | Fixed both as false positives, with regression tests (commit 77bb61b). The semantic pass found a real coverage gap in a security filter. |
| internal HTTP/markdown tool (pytest, 48 test files) | scanner + semantic pass | C7 fired HIGH on `load_module() is load_module()`, the lru_cache identity test | Fixed: an `is` self-compare with a call is not a tautology (commit d6f4afc). Produced a dual-use report the team could act on. |

## Completed (Python, public open-source, >200 stars, >=500 tests)

Run 2026-06-03, fixed and re-scanned 2026-06-05. Eight projects, named by owner/repo:
`encode/httpx`, `encode/starlette`, `pallets/flask`, `fastapi/fastapi`,
`encode/django-rest-framework`, `aio-libs/aiohttp`, `sanic-org/sanic`,
`pallets/werkzeug`. The pass surfaced about 47 HIGH-confidence false positives in
two rule classes. Both were fixed with fires-on-bad and stays-clean regression
tests, and a re-scan brought the HIGH count to 0 across all 8 projects (scanner
suite 90 -> 101 tests).

| Rule | What the run revealed | Decision |
|---|---|---|
| **C7** (self-compare) | Fired on deliberate `__eq__` / `__hash__` tests: `aio-libs/aiohttp` `assert resp1 == resp1; assert not resp1 == resp2`, `encode/starlette` `assert ws == ws; assert ws in {ws}` | Exempt a `==` self-compare when the same test runs a discriminating or membership check on the same operand (`!=`, `not ==`, or `x in {x}`). A lone `assert x == x` still fires. |
| **C4** (uncollected test) | Fired on `test*`-named web route handlers and local callbacks: `fastapi/fastapi` `@app.get`, `pallets/werkzeug` `@Request.application`, `sanic-org/sanic` `@app.post`, `pallets/flask` `@click.command`, `aio-libs/aiohttp` local coroutines scheduled with `asyncio.create_task` | A function that is referenced (called, awaited, scheduled, or passed as a callback) runs, so it is not forgotten. Flag only an undecorated, no-argument nested `test*` with a check in its own body that is never referenced, or a top-level test-shaped function never called. |
| **C18** (LOW) | Fires on intentional `__repr__` / `__str__` tests (`encode/httpx`). Not a false green, a common look-alike | Documented as a look-alike in the reference; stays LOW, the semantic pass adjudicates. |

### Semantic pass (LLM), Python benchmark

A labeled corpus of 24 cases (10 rotten, 14 sound) across cases 10, 11, 12, 18,
run blind on a small model (Claude Haiku), scored: precision 1.00 (no false alarms
on the 14 sound tests), recall 0.70 overall, recall 1.00 on the clear-cut smells,
F1 0.82. The three misses were borderline cases where the precision-first guardrail
defers to "sound". A real-module cross-check (aiohttp, flask) judged 16 of 16 tests
sound and independently agreed with the C7 and C4 fixes.

This table is updated as each language and project is validated.
