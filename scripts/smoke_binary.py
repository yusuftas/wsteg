# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke-test a PyInstaller build: CLI extraction + GUI startup (offscreen).

    python scripts/smoke_binary.py dist/linux/wsteg
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import make_samples  # noqa: E402


def main():
    folder = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "wsteg"))
    ext = ".exe" if os.name == "nt" else ""
    cli, gui = os.path.join(folder, "wsteg" + ext), os.path.join(folder, "wsteg-gui" + ext)
    tmp = tempfile.mkdtemp()
    make_samples.make(tmp)
    wav = os.path.join(tmp, "demo_stereo16.wav")

    out = os.path.join(tmp, "out.bin")
    subprocess.run([cli, "extract", wav, "--source", "ch1", "--step", "2", "--bits", "1,0",
                    "--max-bytes", "64", "-o", out], check=True, timeout=120)
    with open(out, "rb") as f:
        assert f.read().startswith(make_samples.PAYLOAD_STEREO), "CLI extraction returned the wrong payload"
    print("cli ok")

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", WSTEG_NO_AUDIO="1")
    p = subprocess.Popen([gui, wav], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(10)
    if p.poll() is not None:
        print(p.stdout.read().decode(errors="replace"))
        sys.exit(f"GUI exited early with code {p.returncode}")
    p.kill()
    p.wait()
    print("gui ok")


if __name__ == "__main__":
    main()
