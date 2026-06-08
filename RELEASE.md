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

1. Bump the version in `pyproject.toml`, `src/falsegreen/scanner.py` (`__version__`), and `src/falsegreen/__init__.py` in lockstep.
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version with today's date. Update the footer comparison links:
   - `[Unreleased]` line: change `vPREV...HEAD` to `vX.Y.Z...HEAD`
   - Add `[X.Y.Z]: .../compare/vPREV...vX.Y.Z`
3. Update the pre-commit `rev` in `README.md` to `vX.Y.Z`.
4. Run the self-scan: `python -m falsegreen src tests`. It must report zero HIGH findings before the tag is created.
5. Commit everything: `git add -A && git commit -m "release: X.Y.Z"`.
6. Tag and push: `git tag -a vX.Y.Z -m "falsegreen vX.Y.Z" && git push origin main --tags`.
7. Create the GitHub release: `gh release create vX.Y.Z --generate-notes` (or paste the CHANGELOG section manually). Publishing the release fires `release.yml`, which builds and uploads to PyPI.

Confirm the version is live: <https://pypi.org/project/falsegreen/>

## Version scheme

falsegreen follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html):
- **PATCH** (`0.x.Y`): bug fixes, false-positive fixes, documentation changes.
- **MINOR** (`0.X.0`): new detection codes, new config options, backward-compatible features.
- **MAJOR** (`X.0.0`): breaking changes to the CLI, config format, or output structure.
