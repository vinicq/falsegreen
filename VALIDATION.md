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

## In progress (Python, public open-source, >200 stars, >=500 tests)

Run 2026-06-03 against `httpx`, `starlette`, `flask`, `fastapi`, `django-rest-framework`,
`aiohttp`, `sanic`, `werkzeug`. Two HIGH false positives surfaced and are being
fixed before this row is marked complete:

- **C7** flags `assert x == x` that tests `__eq__` reflexivity (seen in `aiohttp`,
  where the next line is `assert not resp1 == resp2`). Decision: stop flagging a
  `==` self-compare when the same test also compares the object with `!=` or
  `not ==`. A lone `assert x == x` stays flagged.
- **C4** flags web route handlers named `test*` (seen in `fastapi`, `werkzeug`,
  `sanic`, decorated with `@app.get` / `@app.post` / `@Request.application`).
  Decision: do not flag a `test*`-named function that carries a decorator or is a
  local callback; a genuinely uncollected top-level test stays flagged.
- **C18** (LOW) fires on intentional `__repr__` / `__str__` tests (`httpx`). Not a
  false green, but a common look-alike; documented in the reference.

This table is updated as each language and project is validated.
