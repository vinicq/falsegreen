# Security policy

Thanks for taking the time to report security issues responsibly. This page
tells you how to reach the maintainer privately and what to expect.

## Which versions get fixes

falsegreen is on its first development cycle. Security fixes land on the latest
commit on `main`. There is no separate long-term support branch yet.

| Version | Supported |
| ------- | --------- |
| `main`  | yes       |
| tagged releases below the latest | no |

When the first `1.x` release ships, this table will be updated.

## Attack surface, in plain terms

The scanner reads test files and parses them with Python's `ast` module. It does
**not** import or execute the code it scans, so a malicious test file cannot run
through the scanner alone. The realistic concerns are narrow: a crafted file that
makes the parser hang or crash, the `--staged` path shelling out to `git`, and
the generated pre-commit hook. Reports in those areas are welcome.

## How to report a vulnerability

Please do **not** open a public GitHub issue for security problems. Public issues
are visible to everyone, including people who might abuse the bug. Use a private
channel so the fix can ship before the bug is publicized.

- **GitHub Security Advisories (preferred):** open a private report at
  <https://github.com/vinicq/falsegreen/security/advisories/new>. This keeps the
  discussion inside the repo and lets the maintainer credit you in the release
  notes if you want.
- **Email:** `vinicq@gmail.com` with the subject prefix `[falsegreen security]`.

Include in the report:

- A short description of the issue and the impact you observed or expect.
- Steps to reproduce, ideally with a minimal test file that triggers it.
- The commit SHA (the long hash that identifies a commit) or version you tested.
- Whether the issue has already been disclosed elsewhere.

If you are not sure whether something counts as a security issue, send the report
anyway.

## What to expect

- An acknowledgement within five business days.
- A reproduction or follow-up question within ten business days.
- A fix or a clear "won't fix" rationale before any public disclosure.
- Credit in the release notes if you want it. Anonymous reports are fine too.

## What is not a security issue

These are bugs, not vulnerabilities. File them as regular issues:

- A false positive or false negative in detection. The scanner is heuristic; a
  wrong verdict is a quality bug, not a security hole.
- The scanner being slow on a very large file or repository.
- A finding you disagree with on style grounds.
