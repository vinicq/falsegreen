# Releasing falsegreen

Publishing to PyPI uses Trusted Publishing (OIDC) through
`.github/workflows/release.yml`. There is no API token to manage. The publish job
proves its identity to PyPI with a short-lived OIDC credential.

## One-time setup (human, do this once before the first publish)

### 1. PyPI Trusted Publisher

On PyPI, while the project does not exist yet, add a **pending publisher**:

- Go to https://pypi.org/manage/account/publishing/
- Add a new pending publisher with:
  - PyPI Project Name: `falsegreen`
  - Owner: `vinicq`
  - Repository name: `falsegreen`
  - Workflow name: `release.yml`
  - Environment name: `pypi`

This reserves the name and authorizes the workflow to upload. It works before the
package exists.

### 2. GitHub environment

Create an environment named `pypi` so the publish job can attach to it:

```
gh api -X PUT repos/vinicq/falsegreen/environments/pypi
```

Optionally add a tag-only deployment branch policy and a required reviewer
(yourself) so an upload cannot happen by accident.

## Publishing a version

1. Bump the version in `pyproject.toml`, `src/falsegreen/scanner.py`
   (`__version__`), and `src/falsegreen/__init__.py` in lockstep. Move the
   CHANGELOG `[Unreleased]` entries under the new version with today's date.
   Update the footer comparison links at the bottom of `CHANGELOG.md`:
   - `[Unreleased]` line: change `vPREV...HEAD` to `vX.Y.Z...HEAD`
   - Add `[X.Y.Z]: .../compare/vPREV...vX.Y.Z`
2. Run the self-scan: `python -m falsegreen src tests`. It must report zero
   HIGH findings before the tag is created.
3. Tag and push: `git tag -a vX.Y.Z -m "falsegreen vX.Y.Z" && git push origin vX.Y.Z`.
4. Create the GitHub release: `gh release create vX.Y.Z --notes-from-tag`
   (or paste the CHANGELOG section manually). Publishing the release fires
   `release.yml`, which builds and uploads to PyPI.

Then confirm the version is live: https://pypi.org/project/falsegreen/
