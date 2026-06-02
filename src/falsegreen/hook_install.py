#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Install the falsegreen scanner as a repo git pre-commit hook.

This is the no-framework path, for people who do not use the pre-commit tool.
If you already use https://pre-commit.com, prefer the .pre-commit-hooks.yaml
entry instead (versioned, shared across clones).

The hook runs the scanner against staged test files on every commit.
- HIGH-confidence findings block the commit (override once with
  `git commit --no-verify`, or globally by exporting FALSEGREEN_BLOCK=0).
- LOW-confidence findings only warn.

Usage:
  python -m falsegreen.hook_install [--repo PATH]
  python -m falsegreen.hook_install --uninstall [--repo PATH]
"""

import argparse
import os
import sys

MARKER = "# >>> falsegreen pre-commit hook >>>"
MARKER_END = "# <<< falsegreen pre-commit hook <<<"

HOOK = """#!/bin/sh
{marker}
PY="$(command -v python3 || command -v python || command -v py)"
if [ -z "$PY" ]; then
  echo "[falsegreen] python not found on PATH, skipping test scan"
  exit 0
fi
"$PY" -m falsegreen --staged
CODE=$?
if [ "$CODE" -eq 20 ] && [ "${{FALSEGREEN_BLOCK:-1}}" != "0" ]; then
  echo ""
  echo "[falsegreen] high-confidence false positives above. Commit blocked."
  echo "  - fix them, or bypass this once with:  git commit --no-verify"
  echo "  - deep semantic audit (expected vs intended): run /falsegreen"
  exit 1
fi
exit 0
{marker_end}
""".format(marker=MARKER, marker_end=MARKER_END)


def find_git_dir(repo):
    git = os.path.join(repo, ".git")
    if os.path.isdir(git):
        return git
    if os.path.isfile(git):
        with open(git, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("gitdir:"):
                    return line.split(":", 1)[1].strip()
    return None


def install(repo):
    git_dir = find_git_dir(repo)
    if not git_dir:
        print("error: %s is not a git repository (no .git found)." % repo)
        return 1
    hooks_dir = os.path.join(git_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    hook_path = os.path.join(hooks_dir, "pre-commit")

    if os.path.exists(hook_path):
        with open(hook_path, "r", encoding="utf-8", errors="replace") as fh:
            existing = fh.read()
        if MARKER not in existing:
            backup = hook_path + ".bak"
            with open(backup, "w", encoding="utf-8") as fh:
                fh.write(existing)
            print("note: existing pre-commit backed up to %s" % backup)

    with open(hook_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HOOK)
    try:
        os.chmod(hook_path, 0o755)
    except Exception:
        pass

    print("installed pre-commit hook -> %s" % hook_path)
    print("it runs `python -m falsegreen --staged` (install the package first: pip install falsegreen).")
    print("HIGH-confidence findings block the commit. Set FALSEGREEN_BLOCK=0 to warn only.")
    return 0


def uninstall(repo):
    git_dir = find_git_dir(repo)
    if not git_dir:
        print("error: %s is not a git repository." % repo)
        return 1
    hook_path = os.path.join(git_dir, "hooks", "pre-commit")
    if not os.path.exists(hook_path):
        print("no pre-commit hook to remove.")
        return 0
    with open(hook_path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    if MARKER not in content:
        print("pre-commit hook was not installed by falsegreen, leaving it alone.")
        return 0
    backup = hook_path + ".bak"
    if os.path.exists(backup):
        with open(backup, "r", encoding="utf-8") as fh:
            prev = fh.read()
        with open(hook_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(prev)
        os.remove(backup)
        print("restored previous pre-commit hook from backup.")
    else:
        os.remove(hook_path)
        print("removed pre-commit hook.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install the falsegreen pre-commit hook.")
    ap.add_argument("--repo", default=".", help="path to the target repo (default: cwd)")
    ap.add_argument("--uninstall", action="store_true", help="remove the hook")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if args.uninstall:
        return uninstall(repo)
    return install(repo)


if __name__ == "__main__":
    sys.exit(main())
