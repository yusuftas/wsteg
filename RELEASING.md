# Releasing wsteg

Releases are cut from a git tag. Pushing a tag `vX.Y.Z` runs
`.github/workflows/release.yml`, which:

1. checks the tag matches `wsteg.__version__` and that `CHANGELOG.md` has a `## [X.Y.Z]` section,
2. runs the test suite,
3. builds the standalone app on Windows and Linux (`scripts/build.py`) and smoke-tests
   each binary (`scripts/smoke_binary.py`),
4. creates a GitHub Release named `wsteg vX.Y.Z` with these files attached:
   - `wsteg-X.Y.Z-windows-x86_64.zip`
   - `wsteg-X.Y.Z-linux-x86_64.tar.gz`
   - `SHA256SUMS.txt`

   The release notes are the changelog section, followed by GitHub's auto-generated
   list of merged PRs and contributors.

Tags with a suffix (`v1.0.0-rc1`) are published as **pre-releases**.

## Steps

```bash
# 1. While working: add bullet points under "## [Unreleased]" in CHANGELOG.md.
# 2. On an up-to-date, clean main branch:
python scripts/release.py 0.2.0      # bumps __version__, dates the changelog, commits, tags v0.2.0
# 3. Publish (the script prints this command):
git push origin main v0.2.0
```

Watch the run under **Actions → release**. The release appears under **Releases**
when it finishes (about 10 minutes).

If the workflow fails, fix the problem, then move the tag to the fixed commit and push again:

```bash
git tag -d v0.2.0 && git push origin :refs/tags/v0.2.0   # delete the tag locally and on GitHub
git tag -a v0.2.0 -m "wsteg v0.2.0" && git push origin v0.2.0
```

If a release was already created, delete it on GitHub first. If it was already
downloaded, prefer releasing `0.2.1` instead.

## Versioning

[Semantic Versioning](https://semver.org/): bump the **patch** for fixes, the **minor**
for new features, and the **major** for breaking CLI or file-format changes.

## Other automation

- **ci.yml**: on every pull request and push to `main`: ruff lint, then tests on
  Ubuntu and Windows with Python 3.10 and 3.13 (the GUI tests run offscreen). Pushes
  to `main` also build both apps; download them from the run's **Artifacts** for
  14 days.
- **dependabot.yml**: monthly PRs to update GitHub Actions and Python dependencies.
