"""Release helper (see RELEASING.md).

    python scripts/release.py 0.2.0        # bump version, date the changelog, commit + tag v0.2.0
    python scripts/release.py notes 0.2.0  # print the changelog section for 0.2.0 (used by CI)

Nothing is pushed; push the commit and tag yourself when ready.
"""
import datetime
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
INIT = os.path.join(ROOT, "wsteg", "__init__.py")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def section(text, heading_re):
    """Body of the first '## ' section whose heading matches heading_re."""
    m = re.search(rf"^## {heading_re}.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def notes(version):
    body = section(read(CHANGELOG), rf"\[{re.escape(version)}\]")
    if not body:
        sys.exit(f"CHANGELOG.md has no '## [{version}]' section")
    print(body)


def git(*args, capture=False):
    r = subprocess.run(["git", *args], cwd=ROOT, check=True, text=True, capture_output=capture)
    return r.stdout.strip() if capture else None


def release(version):
    if not SEMVER.match(version):
        sys.exit(f"'{version}' is not a semantic version like 1.2.3 or 1.2.3-rc1")
    if git("status", "--porcelain", capture=True):
        sys.exit("working tree is not clean; commit or stash first")
    tag = f"v{version}"
    if git("tag", "--list", tag, capture=True):
        sys.exit(f"tag {tag} already exists")

    log = read(CHANGELOG)
    unreleased = section(log, r"\[Unreleased\]")
    if not unreleased:
        sys.exit("CHANGELOG.md: add notes under '## [Unreleased]' first")
    today = datetime.date.today().isoformat()
    log = re.sub(r"^## \[Unreleased\].*?$", f"## [Unreleased]\n\n## [{version}] - {today}", log, count=1, flags=re.M)
    write(CHANGELOG, log)
    write(INIT, re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', read(INIT)))

    git("add", CHANGELOG, INIT)
    git("commit", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"wsteg {tag}")
    branch = git("branch", "--show-current", capture=True)
    print(f"Created commit and tag {tag}. Publish with:\n  git push origin {branch} {tag}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "notes":
        notes(sys.argv[2])
    elif len(sys.argv) == 2:
        release(sys.argv[1])
    else:
        sys.exit(__doc__)
