# falsegreen examples

Worked samples for every code the scanner detects. Each code has a **BAD**
function the scanner flags and a **CLEAN** look-alike it leaves alone, so you can
see both the smell and the legitimate pattern it must not be confused with.

These are scan targets, not a runnable test suite. The bodies call helpers that
do not exist on purpose: the scanner reads the syntax tree, it never imports or
runs them. `examples/python/conftest.py` keeps pytest from collecting them, so
`pytest` at the repo root ignores this directory.

## Layout

Files are grouped by the five families from the methodology (see the main
[README](../README.md#the-methodology)):

| File | Family | Codes |
|---|---|---|
| `python/test_family_a_never_checks.py` | A. the test never checks anything | C1, C2, C2b, C3, C4, C4b, C17, C20, C21, C27, C38, C39, C43, C45, CC |
| `python/test_family_b_weak_always_true.py` | B. the check is weak or always true | C5, C6, C6b, C7, C8, C9, C11a, C13, C13b, C14, C16, C18, C25, C34, C42, C44 |
| `python/test_family_c_checks_own_setup.py` | C. the test checks its own setup | C19, C28, C29 |
| `python/test_family_d_external_state.py` | D. green depends on outside factors | C23, C24, C30, C31, C32, C35 |
| `python/test_family_e_wrong_thing.py` | E. the test checks the wrong thing | C33, C36, C37 |
| `python/test_diagnostic_codes.py` | diagnostic / coupling (opt-in) | D1, D3, D4, D5, D6, M2 |
| `python/test_c16_time_controlled.py` | C16 clean look-alike | (none: frozen clock) |
| `python/test_c22_async_liar.py` | C22 async liar (opt-in) | C22 |

A few codes need their own file because a file-wide signal would otherwise
change the result:

- **C16** is suppressed for a whole file that imports a time-control library
  (freezegun, time_machine). The BAD case lives in family B (no such import);
  the frozen-clock CLEAN look-alike lives in `test_c16_time_controlled.py`.
- **C22** (async liar) and the **diagnostic / coupling** codes are off by
  default, so they report nothing until enabled.

## Run the scanner on the examples

```bash
falsegreen examples/
```

The BAD functions are reported; the `*_clean` look-alikes are not. To see the
opt-in codes, enable them and lower the long-test threshold:

```bash
falsegreen examples/ --config examples/enable-optin.toml
```

where `enable-optin.toml` sets, for example:

```toml
long_test_threshold = 5

[severity]
C22 = "low"
D1 = "info"
D3 = "info"
D4 = "info"
D5 = "info"
D6 = "info"
M2 = "info"
```

## Codes the scanner does not detect

C40, C41, C46, C47, the PL runtime/culture series, and the Family E semantic
codes are deliberately not implemented in the static scanner. The reasons are in
the main [README](../README.md#codes-the-scanner-does-not-detect) and
[ARCHITECTURE.md](../ARCHITECTURE.md). They have no example here because the
scanner is not meant to flag them.
