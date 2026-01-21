# Release Checklist

**Status:** Active
**Last Updated:** 2026-01-20

Checklist for releasing a new version of `notebooklm-py`.

> **For Claude Code:** Follow this checklist step by step. **NO STEPS ARE OPTIONAL.** "Quick release" means efficient execution, NOT skipping steps.
>
> **Critical rules:**
> 1. **Never combine commit and push** - always commit first, show user, then ask "Push to main?"
> 2. **Explicit confirmation required** for: pushing to main, publishing to TestPyPI, creating tags, pushing tags
> 3. **"ok" is not confirmation** - restate what you're about to do and wait for explicit "yes"
> 4. **TestPyPI is mandatory** - it catches packaging issues that tests cannot detect

---

## Pre-Flight Summary

Before starting, present this summary to the user:

```
Release Plan for vX.Y.Z:
1. Update pyproject.toml and CHANGELOG.md
2. Commit (will show diff first)
3. ⏸️ CONFIRM: Push to main?
4. Wait for CI (test.yml + E2E)
5. ⏸️ CONFIRM: Publish to TestPyPI?
6. Verify TestPyPI package
7. ⏸️ CONFIRM: Create and push tag vX.Y.Z?
8. Wait for PyPI publish
9. Create GitHub release

Proceed with release preparation?
```

---

## Pre-Release

### Documentation

- [ ] Verify README.md reflects current features
- [ ] Check CLI reference matches `notebooklm --help` output
- [ ] Verify Python API docs match public exports in `__init__.py`
- [ ] Update `Last Updated` dates in modified docs
- [ ] Verify example scripts have valid syntax:
  ```bash
  python -m py_compile docs/examples/*.py
  ```

**Related docs to check/update if relevant:**

| Doc | Update when... |
|-----|----------------|
| [SKILL.md](../src/notebooklm/data/SKILL.md) | New CLI commands, changed flags, new workflows |
| [cli-reference.md](cli-reference.md) | Any CLI changes |
| [python-api.md](python-api.md) | New/changed Python API |
| [troubleshooting.md](troubleshooting.md) | New known issues, fixed issues to remove |
| [development.md](development.md) | Architecture changes, new test patterns |
| [configuration.md](configuration.md) | New env vars, config options |
| [stability.md](stability.md) | Public API changes, deprecations |

### Version Bump

- [ ] Determine version bump type using this decision tree:

  ```
  Did you add new items to `__all__` in `__init__.py`?
  ├── YES → MINOR (new public API)
  └── NO → PATCH (fixes, logging, UX, internal improvements)

  When in doubt, it's PATCH.
  ```

  See [Version Numbering](#version-numbering) for full details.

- [ ] Update version in `pyproject.toml`:
  ```toml
  version = "X.Y.Z"
  ```

### Changelog

- [ ] Get commits since last release:
  ```bash
  git log $(git describe --tags --abbrev=0)..HEAD --oneline
  ```
- [ ] Generate changelog entries in Keep a Changelog format:
  - **Added** - New features
  - **Fixed** - Bug fixes
  - **Changed** - Changes in existing functionality
  - **Deprecated** - Soon-to-be removed features
  - **Removed** - Removed features
  - **Security** - Security fixes
- [ ] Add entries under `## [Unreleased]` in `CHANGELOG.md`
- [ ] Move `[Unreleased]` content to new version section:
  ```markdown
  ## [Unreleased]

  ## [X.Y.Z] - YYYY-MM-DD
  ```
- [ ] Update comparison links at bottom of `CHANGELOG.md`:
  ```markdown
  [Unreleased]: https://github.com/teng-lin/notebooklm-py/compare/vX.Y.Z...HEAD
  [X.Y.Z]: https://github.com/teng-lin/notebooklm-py/compare/vPREV...vX.Y.Z
  ```

### Commit

- [ ] Verify changes:
  ```bash
  git diff
  ```
- [ ] Commit (do NOT push yet):
  ```bash
  git add pyproject.toml CHANGELOG.md
  git commit -m "chore: release vX.Y.Z"
  ```
- [ ] Show commit to user:
  ```bash
  git show --stat
  ```

---

## CI Verification

### Push to Main

- [ ] **⏸️ CONFIRM:** Ask user "Ready to push to main? This will trigger CI."
- [ ] Push to main:
  ```bash
  git push origin main
  ```
- [ ] Wait for **test.yml** to pass:
  - Linting and formatting
  - Type checking
  - Unit and integration tests (Python 3.10-3.14, all platforms)

### E2E Tests on Main

- [ ] Go to **Actions** → **Nightly E2E**
- [ ] Click **Run workflow**, select `main` branch
- [ ] Wait for E2E tests to pass

---

## Package Verification

> **⚠️ REQUIRED:** Do NOT skip TestPyPI verification. Always test on TestPyPI before publishing to PyPI. This catches packaging issues that unit tests cannot detect (missing files, broken imports, dependency problems).

### Publish to TestPyPI

- [ ] **⏸️ CONFIRM:** Ask user "Ready to publish to TestPyPI?"
- [ ] Go to **Actions** → **Publish to TestPyPI**
- [ ] Click **Run workflow**
- [ ] Wait for upload to complete
- [ ] Verify package appears: https://test.pypi.org/project/notebooklm-py/

> **Note:** TestPyPI does not allow re-uploading the same version. If you need to fix issues after publishing, bump the patch version and start over.

### Verify TestPyPI Package

- [ ] Go to **Actions** → **Verify Package**
- [ ] Click **Run workflow** with **source**: `testpypi`
- [ ] Wait for all tests to pass (unit, integration, E2E)
- [ ] If verification fails:
  1. Fix issues locally
  2. Bump patch version in `pyproject.toml`
  3. Update `CHANGELOG.md` with fix
  4. Amend or create new commit
  5. Push and re-run **Publish to TestPyPI**

---

## Release

### Tag and Publish

- [ ] **⏸️ CONFIRM:** Ask user "TestPyPI verified. Ready to create tag vX.Y.Z and publish to PyPI? This is irreversible."
- [ ] Create tag:
  ```bash
  git tag vX.Y.Z
  ```
- [ ] Push tag:
  ```bash
  git push origin vX.Y.Z
  ```
- [ ] Wait for **publish.yml** to complete
- [ ] Verify on PyPI: https://pypi.org/project/notebooklm-py/

### PyPI Verification

- [ ] Go to **Actions** → **Verify Package**
- [ ] Click **Run workflow** with:
  - **source**: `pypi`
- [ ] Wait for all tests to pass

### GitHub Release

- [ ] Create release from tag:
  ```bash
  gh release create vX.Y.Z --title "vX.Y.Z" --notes "$(cat CHANGELOG.md | sed -n '/## \[X.Y.Z\]/,/## \[/p' | sed '$d')"
  ```
  Or manually:
  - Go to **Releases** → **Draft a new release**
  - Select tag `vX.Y.Z`
  - Title: `vX.Y.Z`
  - Copy release notes from `CHANGELOG.md`
  - Publish release

---

## Troubleshooting

### CI fails after push

> **Warning:** Only do this immediately after your own push, before anyone else pulls.

```bash
# Fix locally, then amend
git add -A
git commit --amend --no-edit
git push --force origin main
```

### Need to abort after commit

> **Warning:** Force pushing rewrites history. Only do this if you haven't shared the commit.

```bash
# Undo release commit (local only)
git reset --hard HEAD~1

# If already pushed (use with caution)
git push --force origin main
```

### Tag already exists

```bash
# Delete local tag
git tag -d vX.Y.Z

# Delete remote tag (if pushed)
git push origin :refs/tags/vX.Y.Z
```

### TestPyPI upload fails

- Check if version already exists on TestPyPI
- TestPyPI doesn't allow re-uploading same version
- Bump to next patch version if needed

---

## Version Numbering

**IMPORTANT:** Read [stability.md](stability.md) before deciding version bump.

| Change Type | Bump | Example |
|-------------|------|---------|
| RPC method ID fixes | PATCH | 0.1.0 → 0.1.1 |
| Bug fixes | PATCH | 0.1.1 → 0.1.2 |
| Internal improvements (logging, auth UX, CI) | PATCH | 0.1.2 → 0.1.3 |
| **New public API** (new classes, methods in `__all__`) | MINOR | 0.1.3 → 0.2.0 |
| Breaking changes to public API | MAJOR | 0.2.0 → 1.0.0 |

**Key distinction:** "New features" means new **public API surface** (additions to `__all__` in `__init__.py`). Internal improvements, better error messages, logging enhancements, and UX improvements are PATCH releases.
