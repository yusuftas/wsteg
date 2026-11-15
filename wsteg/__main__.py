# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line entry point.

    python -m wsteg [files...]              # desktop app
    python -m wsteg info file.wav
    python -m wsteg extract file.wav --source ch0 --step 2 --bits 1,0 -o out.bin
    python -m wsteg hide cover.wav secret.bin -o out.wav --bits 0
"""
from __future__ import annotations

import argparse
import json
import sys

from .embed import capacity_bits, hide
from .extract import ExtractOptions, Selection, analyze_payload, extract
from .wavio import load_file


def _sel_args(p):
    p.add_argument("--source", default="ch0", help="ch0, ch1, ..., interleaved, sequential")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=None)
    p.add_argument("--step", type=int, default=1, help="group size: keep `take` of every `step` samples")
    p.add_argument("--take", type=int, default=1)
    p.add_argument("--reverse", action="store_true")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wsteg", description="Audio steganography toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gui", help="start the desktop app (default)")
    g.add_argument("files", nargs="*")

    i = sub.add_parser("info", help="print file / chunk info")
    i.add_argument("file")

    e = sub.add_parser("extract", help="extract bits to a file")
    e.add_argument("file")
    _sel_args(e)
    e.add_argument("--bits", default="0", help="bit indices in output order, 0 = LSB (e.g. 1,0)")
    e.add_argument("--pack", choices=["msb", "lsb"], default="msb")
    e.add_argument("--invert", action="store_true")
    e.add_argument("--max-bytes", type=int, default=None)
    e.add_argument("-o", "--out", help="output file (default: print analysis + preview)")

    h = sub.add_parser("hide", help="hide a file or text in chosen sample bits and write a new WAV")
    h.add_argument("file", help="cover audio file (not modified)")
    h.add_argument("payload", nargs="?", help="file to hide")
    h.add_argument("--text", help="hide this text (UTF-8) instead of a file")
    _sel_args(h)
    h.add_argument("--bits", default="0", help="bit indices in the order they are filled, 0 = LSB (e.g. 1,0)")
    h.add_argument("--pack", choices=["msb", "lsb"], default="msb")
    h.add_argument("--invert", action="store_true")
    h.add_argument("-o", "--out", required=True, help="output WAV file")

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("gui", "info", "extract", "hide", "-h", "--help"):
        argv = ["gui", *argv]  # bare `wsteg file.wav` opens the app
    a = ap.parse_args(argv)

    if a.cmd == "gui":
        from .gui.app import main as gui_main
        sys.exit(gui_main(a.files))

    audio = load_file(a.file)
    if a.cmd == "info":
        info = audio.info()
        for c in info["chunks"]:
            c.pop("preview_hex")
        print(json.dumps(info, indent=2, default=str))
        return

    sel = Selection(a.source, a.start, a.end, a.step, a.take, a.reverse)
    if a.cmd == "hide":
        if (a.payload is None) == (a.text is None):
            ap.error("hide: give either a payload file or --text")
        if a.text is not None:
            payload = a.text.encode("utf-8")
        else:
            with open(a.payload, "rb") as f:
                payload = f.read()
        try:
            opts = ExtractOptions.from_dict({"bits": a.bits, "pack": a.pack, "invert": a.invert}, audio.nbits)
            cap = capacity_bits(audio, sel, opts.bits)
            data, st = hide(audio, sel, opts, payload)
        except ValueError as e:
            sys.exit(f"wsteg hide: {e}")
        with open(a.out, "wb") as f:
            f.write(data)
        print(f"hid {len(payload)} bytes ({len(payload) * 8} of {cap} available bits) in {a.out}; "
              f"{st['samples_changed']} samples changed", file=sys.stderr)
        return

    opts = ExtractOptions.from_dict(
        {"bits": a.bits, "pack": a.pack, "invert": a.invert, "max_bytes": a.max_bytes}, audio.nbits)
    data, _ = extract(audio, sel, opts)
    if a.out:
        with open(a.out, "wb") as f:
            f.write(data)
        print(f"wrote {len(data)} bytes to {a.out}", file=sys.stderr)
    res = analyze_payload(data)
    res["strings"] = res["strings"][:10]
    print(json.dumps(res, indent=2), file=sys.stderr)
    if not a.out:
        sys.stdout.write(data[:512].decode("latin-1") + "\n")


if __name__ == "__main__":
    main()
