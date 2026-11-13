"""Build a standalone wsteg app for the current OS (Windows or Linux).

    python scripts/build.py              # dist/wsteg/ folder + archive
    python scripts/build.py --onefile    # single wsteg-gui executable
PyInstaller cannot cross-compile: run this on each target OS (or use CI).
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from wsteg import __version__  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="single GUI executable instead of a folder")
    ap.add_argument("--no-archive", action="store_true")
    a = ap.parse_args()

    env = dict(os.environ, WSTEG_ONEFILE="1" if a.onefile else "0")
    osname = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system(), platform.system())
    dist, work = os.path.join(ROOT, "dist", osname), os.path.join(ROOT, "build", osname)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", dist, "--workpath", work, os.path.join(ROOT, "packaging", "wsteg.spec")]
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=env, cwd=ROOT)

    arch = platform.machine().lower().replace("amd64", "x86_64")
    tag = f"wsteg-{__version__}-{osname}-{arch}"
    if a.onefile:
        exe = "wsteg-gui.exe" if os.name == "nt" else "wsteg-gui"
        out = os.path.join(dist, tag + (".exe" if os.name == "nt" else ""))
        shutil.copy2(os.path.join(dist, exe), out)
        print("built", out)
        return
    folder = os.path.join(dist, "wsteg")
    print("built", folder)
    if not a.no_archive:
        fmt = "zip" if os.name == "nt" else "gztar"
        archive = shutil.make_archive(os.path.join(dist, tag), fmt, dist, "wsteg")
        print("archive", archive)


if __name__ == "__main__":
    main()
