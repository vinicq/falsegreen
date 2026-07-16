# Releasing falsegreen

Publishing to PyPI uses Trusted Publishing (OIDC) through `.github/workflows/release.yml`. There is no API token to manage. The publish job proves its identity to PyPI with a short-lived OIDC credential.

## One-time setup (do this once before the first publish)

### 1. PyPI Trusted Publisher

On PyPI, while the project does not exist yet, add a **pending publisher**:

- Go to <https://pypi.org/manage/account/publishing/>
- Add a new pending publisher with:
  - PyPI Project Name: `falsegreen`
  - Owner: `vinicq`
  - Repository name: `falsegreen`
  - Workflow name: `release.yml`
  - Environment name: `pypi`

This reserves the name and authorizes the workflow to upload before the package exists.

### 2. GitHub environment

Create an environment named `pypi` so the publish job can attach to it:

```bash
gh api -X PUT repos/vinicq/falsegreen/environments/pypi
```

Optionally add a tag-only deployment branch policy and a required reviewer so an upload cannot happen by accident.

## Publishing a version

`main` is a protected branch: direct pushes are rejected, every change lands
through a pull request with the test CI (`test (3.8/3.11/3.13)`) green, and
commits on `main` must be signed. A release is prepared on a branch, merged via
PR, and only then tagged. The tag push and the GitHub release are not blocked by
branch protection (they act on `refs/tags/*`, not on `main`).

### Prepare the release on a branch

1. Branch off `main`: `git checkout main && git pull && git checkout -b release/X.Y.Z`.
2. Bump the version in lockstep in **all four** places (the `test_version_lockstep`
   test fails on any mismatch): `pyproject.toml`, `src/falsegreen/scanner.py`
   (`__version__`), `CITATION.cff` (`version:`), and `src/falsegreen/__init__.py`
   (it re-exports `__version__` from `scanner`, so no separate string to edit, but
   confirm the import still resolves). Set `CITATION.cff` `date-released` to today.
3. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version with
   today's date. Update the footer comparison links:
   - `[Unreleased]` line: change `vPREV...HEAD` to `vX.Y.Z...HEAD`
   - Add `[X.Y.Z]: .../compare/vPREV...vX.Y.Z`
4. Update the pre-commit `rev` in `README.md` to `vX.Y.Z`.
5. Run the self-scan: `python -m falsegreen src tests`. It must report zero HIGH
   findings. Run `pytest -q` and `ruff check src tests` too.
6. Commit (signed) and push the branch: `git commit -S -am "[no-issue] release: X.Y.Z"`
   then `git push -u origin release/X.Y.Z`.

### Merge and publish

7. Open the PR: `gh pr create --base main --title "[no-issue] release: X.Y.Z"`.
   Wait for the three `test (...)` checks to pass, then merge (squash):
   `gh pr merge --squash --delete-branch`. The squash commit on `main` is signed
   by GitHub (verified), satisfying the signed-commits rule.
8. Sync and tag the **merged** commit on `main` (not the branch commit — squash
   creates a new SHA): `git checkout main && git pull && git tag -a vX.Y.Z -m
   "falsegreen vX.Y.Z" && git push origin vX.Y.Z`.
9. Create the GitHub release: `gh release create vX.Y.Z --generate-notes` (or paste
   the CHANGELOG section manually). Publishing the release fires `release.yml`,
   which builds and uploads to PyPI via OIDC.

Confirm the version is live: <https://pypi.org/project/falsegreen/>

## Version scheme

falsegreen follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html):
- **PATCH** (`0.x.Y`): bug fixes, false-positive fixes, documentation changes.
- **MINOR** (`0.X.0`): new detection codes, new config options, backward-compatible features.
- **MAJOR** (`X.0.0`): breaking changes to the CLI, config format, or output structure.
