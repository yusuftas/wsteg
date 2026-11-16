# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line entry point.

    python -m wsteg [files...]              # desktop app
    python -m wsteg info file.wav
    python -m wsteg extract file.wav --source ch0 --step 2 --bits 1,0 -o out.bin
    python -m wsteg extract file.wav -a --upto 3 --step 2   # try combinations of bits 0..3
    python -m wsteg hide cover.wav secret.bin -o out.wav --bits 0
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

from .embed import capacity_bits, hide
from .extract import (
    COMBO_MAX,
    COMBO_RATE,
    COMBO_WARN,
    PRINTABLE,
    TEXT_START_RUN,
    ExtractOptions,
    Selection,
    analyze_payload,
    count_combinations,
    extract,
    try_combinations,
)
from .term import Painter, use_color
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
    e.add_argument("--bits", default=None,
                   help="bit indices in output order, 0 = LSB (e.g. 1,0; default 0). "
                        "With -a: the candidate bits to combine")
    e.add_argument("--pack", choices=["msb", "lsb"], default="msb")
    e.add_argument("--invert", action="store_true")
    e.add_argument("--max-bytes", type=int, default=None)
    e.add_argument("-o", "--out", help="output file (default: print analysis + preview)")
    e.add_argument("-a", "--all", action="store_true",
                   help="try combinations: every source x every selection of the candidate bits x bit order x "
                        "MSB/LSB packing, keeping the sample range (--source is ignored); prints the best matches, ranked by file "
                        "signature, then longest printable text run")
    e.add_argument("--upto", type=int, default=None, metavar="N",
                   help="with -a: candidate bits are 0..N (default 3); use --bits instead for an explicit set")
    e.add_argument("--keep", type=int, default=30, metavar="N", help="with -a: rows to print (default 30, 0 = all)")
    e.add_argument("--json", action="store_true", help="with -a: print the rows as JSON")
    e.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="with -a: colour the table (auto: when printing to a terminal and NO_COLOR is unset)")

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

    if a.all:
        _try_all(e, a, audio, sel)
        return
    if a.upto is not None:
        e.error("--upto needs -a")
    try:
        opts = ExtractOptions.from_dict(
            {"bits": a.bits or "0", "pack": a.pack, "invert": a.invert, "max_bytes": a.max_bytes}, audio.nbits)
    except ValueError as err:
        sys.exit(f"wsteg extract: {err}")
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


def _extract_cmd(a, r) -> str:
    """The `wsteg extract` command that runs combination row `r` in full."""
    cmd = ["wsteg", "extract", a.file, "--source", r["source"]]
    if a.start:
        cmd += ["--start", str(a.start)]
    if a.end is not None:
        cmd += ["--end", str(a.end)]
    if a.step != 1:
        cmd += ["--step", str(a.step)]
    if a.take != 1:
        cmd += ["--take", str(a.take)]
    if a.reverse:
        cmd.append("--reverse")
    cmd += ["--bits", ",".join(map(str, r["bits"])), "--pack", r["pack"]]
    if a.invert:
        cmd.append("--invert")
    if a.max_bytes:
        cmd += ["--max-bytes", str(a.max_bytes)]
    cmd += ["-o", "out.bin"]
    if sys.platform == "win32":  # cmd.exe and PowerShell both take double quotes; shlex's single quotes break cmd.exe
        return subprocess.list2cmdline(cmd)
    return " ".join(shlex.quote(c) for c in cmd)


def _try_all(parser, a, audio, sel):
    """`wsteg extract -a`: the GUI's Try combinations, printed as a ranked table."""
    if a.out:
        parser.error("-o can't be combined with -a (run the printed command for the row you want)")
    if a.bits is not None and a.upto is not None:
        parser.error("give either --bits or --upto with -a, not both")
    if a.bits is not None:
        bits = a.bits
    else:
        upto = 3 if a.upto is None else a.upto
        if not 0 <= upto < audio.nbits:
            sys.exit(f"wsteg extract: --upto {upto} out of range 0..{audio.nbits - 1}")
        bits = list(range(upto, -1, -1))
    try:
        opts = ExtractOptions.from_dict({"bits": bits, "invert": a.invert, "max_bytes": a.max_bytes}, audio.nbits)
    except ValueError as err:
        sys.exit(f"wsteg extract: {err}")
    pool = len(set(opts.bits))
    n = count_combinations(audio.channels, opts.bits, pool)
    if n > COMBO_MAX:
        sys.exit(f"wsteg extract: {pool} candidate bits give {n:,} combinations, which is too many "
                 f"(limit {COMBO_MAX:,}). Use a lower --upto or fewer --bits.")
    keep = a.keep if a.keep > 0 else None
    eta = f", roughly {n / COMBO_RATE / 60:.0f} minutes" if n > COMBO_WARN else ""
    print(f"trying {n:,} combinations of source x bit selection x bit order x packing "
          f"(candidate bits {','.join(map(str, sorted(set(opts.bits), reverse=True)))}{eta})...", file=sys.stderr)
    rows = try_combinations(audio, sel, opts, max_bits=pool, keep=keep)

    if a.json:
        out = [{"source": r["source"], "bits": r["bits"], "pack": r["pack"], "magic": r["magic"],
                "magic_later": r["magic_later"], "text_run": r["text_run"], "text_at": r["text_at"],
                "text": r["text"].decode("latin-1"), "printable": round(r["printable"], 3),
                "head_hex": r["head"].hex(), "command": _extract_cmd(a, r)} for r in rows]
        print(json.dumps(out, indent=2))
        return

    p = Painter(use_color(a.color))
    sw = max([len("source")] + [len(r["source"]) for r in rows])
    bw = max([len("bits")] + [len(r["bits"]) * 3 for r in rows])  # "b" + "n," per bit, room for two-digit bits
    print(p(f"  {'source':<{sw}}  {'bits':<{bw}} {'pk':<3} {'run':>5} {'print':>5}  text", "dim", "underline"))
    tiers = {"file": 0, "text": 0}
    for i, r in enumerate(rows):
        tier = "file" if r["magic"] else "text" if r["text_start"] else "noise"
        tiers[tier] = tiers.get(tier, 0) + 1
        # row label: marker + source / bits / pack, coloured by tier
        mark, label = {"file": ("*", ("bold", "bright_green")), "text": ("+", ("green",)),
                       "noise": (" ", ("dim",))}[tier]
        bits = "b" + ",".join(map(str, r["bits"]))
        head = p(f"{mark} {r['source']:<{sw}}  {bits:<{bw}} {r['pack']:<3}", *label)
        run_style = ("bold", "bright_green") if tier == "text" else ("yellow",) if r["text_run"] >= SNIPPET_RUN \
            else ("dim",)
        stats = p(f"{r['text_run']:>5}", *run_style) + p(f" {r['printable'] * 100:4.0f}%", *(
            () if r["printable"] >= 0.9 else ("dim",)))
        line = f"{head} {stats}  {_preview(p, r['head'][:PREVIEW], tier)}"
        found = [p(f"file: {m}", "bold", "bright_green") for m in r["magic"]]
        found += [p(f"{m['type']} @ 0x{m['offset']:x}", "yellow") for m in r["magic_later"][:2]]
        if r["text_run"] >= SNIPPET_RUN and r["text_at"] + SNIPPET_RUN > PREVIEW:  # the run is past the preview
            snippet = r["text"].decode("latin-1").translate(_CTRL)
            style = ("green", "bright_green") if tier == "text" else ("yellow", "bright_white")
            found.append(p(f"text @ 0x{r['text_at']:x}: ", style[0]) + p(f'"{snippet}"', style[1]))
        if found:
            line += "  " + p("[", "dim") + p("; ", "dim").join(found) + p("]", "dim")
        print(line)
        if tier == "file" or i == 0:
            print(p(f"    -> {_extract_cmd(a, r)}", "cyan"))
    shown = f"best {len(rows)} shown" if len(rows) < n else "all shown"
    print(f"{n:,} combinations tried, {shown}: {tiers['file']} with a file signature at offset 0 (*), "
          f"{tiers['text']} starting with {TEXT_START_RUN}+ chars of text (+). "
          "run = longest printable text run in the first 4 KiB", file=sys.stderr)


SNIPPET_RUN = 20  # show (in yellow) printable runs this long that start past the preview
PREVIEW = 48  # preview characters per row
STRING_MIN = 6  # printable runs at least this long are highlighted inside the preview
_CTRL = {9: " ", 10: " ", 13: " "}  # tab / newline / CR shown as spaces in a one-line snippet


def _preview(p, data: bytes, tier: str) -> str:
    """One-line preview: printable runs of STRING_MIN+ chars stand out, everything else is dimmed."""
    strong = ("bright_green",) if tier != "noise" else ("bright_white",)
    parts: list[tuple[tuple, str]] = []  # (styles, text), neighbours with the same style merged
    i = 0
    while i < len(data):
        j, ok = i, bool(PRINTABLE[data[i]])
        while j < len(data) and bool(PRINTABLE[data[j]]) == ok:
            j += 1
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in data[i:j])
        style = strong if ok and j - i >= STRING_MIN else ("dim",)
        if parts and parts[-1][0] == style:
            parts[-1] = (style, parts[-1][1] + text)
        else:
            parts.append((style, text))
        i = j
    return "".join(p(text, *style) for style, text in parts)


if __name__ == "__main__":
    main()
