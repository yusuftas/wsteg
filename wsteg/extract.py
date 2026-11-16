# SPDX-License-Identifier: GPL-3.0-or-later
"""Sample selection, bit extraction and payload detection."""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import asdict, dataclass, field, replace

import numpy as np

from .wavio import Audio


@dataclass
class Selection:
    """Which samples to read.

    source: 'ch0', 'ch1', ... a single channel;
            'interleaved' all channels in file order (L R L R ...);
            'sequential' all of ch0 then all of ch1 ...
    The stream is then sliced [start:end], and from every group of `step`
    samples the first `take` are kept (step=2,take=1 -> every other sample).
    """
    source: str = "ch0"
    start: int = 0
    end: int | None = None
    step: int = 1
    take: int = 1
    reverse: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Selection":
        end = d.get("end")
        return cls(
            source=str(d.get("source", "ch0")),
            start=max(0, int(d.get("start") or 0)),
            end=int(end) if end not in (None, "", "null") else None,
            step=max(1, int(d.get("step") or 1)),
            take=max(1, int(d.get("take") or 1)),
            reverse=str(d.get("reverse", "")).lower() in ("1", "true", "yes", "on"),
        )


def source_stream(audio: Audio, source: str) -> np.ndarray:
    if source == "interleaved":
        return audio.raw.reshape(-1)
    if source == "sequential":
        return audio.raw.T.reshape(-1)
    ch = int(source[2:]) if source.startswith("ch") else 0
    if not 0 <= ch < audio.channels:
        raise ValueError(f"channel {ch} out of range")
    return audio.raw[:, ch]


def stream_length(audio: Audio, source: str) -> int:
    return audio.frames * (audio.channels if source in ("interleaved", "sequential") else 1)


def selected_count(stream_len: int, sel: Selection) -> int:
    """Number of samples `select` yields from a source stream of `stream_len` samples."""
    end = stream_len if sel.end is None else min(stream_len, sel.end)
    n = max(0, end - sel.start)
    take = min(sel.take, sel.step)
    return n // sel.step * take + min(take, n % sel.step)


def select(audio: Audio, sel: Selection) -> np.ndarray:
    s = source_stream(audio, sel.source)[sel.start:sel.end]
    if sel.step > 1:
        take = min(sel.take, sel.step)
        if take == 1:
            s = s[::sel.step]
        else:
            n = len(s) // sel.step * sel.step
            full = s[:n].reshape(-1, sel.step)[:, :take].reshape(-1)
            s = np.concatenate([full, s[n:][:take]])
    if sel.reverse:
        s = s[::-1]
    return s


def selected_index(sel: Selection, i: int) -> int:
    """Map an index in the selected stream back to an index in the source stream
    (ignores `reverse`)."""
    take = min(sel.take, sel.step)
    return sel.start + (i // take) * sel.step + (i % take)


@dataclass
class ExtractOptions:
    bits: list[int] = field(default_factory=lambda: [0])  # in output order
    pack: str = "msb"  # 'msb': first extracted bit is the byte's MSB
    invert: bool = False
    max_bytes: int | None = None

    @classmethod
    def from_dict(cls, d: dict, nbits: int) -> "ExtractOptions":
        bits = d.get("bits", [0])
        if isinstance(bits, str):
            bits = [int(b) for b in re.split(r"[\s,]+", bits.strip()) if b != ""]
        bits = [int(b) for b in bits]
        if not bits:
            raise ValueError("no bits selected")
        for b in bits:
            if not 0 <= b < nbits:
                raise ValueError(f"bit {b} out of range 0..{nbits - 1}")
        mb = d.get("max_bytes")
        return cls(
            bits=bits,
            pack="lsb" if str(d.get("pack", "msb")).lower() == "lsb" else "msb",
            invert=str(d.get("invert", "")).lower() in ("1", "true", "yes", "on"),
            max_bytes=int(mb) if mb not in (None, "", 0, "0") else None,
        )


def extract_bitstream(samples: np.ndarray, bits: list[int], invert=False, max_bits=None) -> np.ndarray:
    k = len(bits)
    if max_bits is not None:
        samples = samples[: -(-max_bits // k)]
    sh = np.array(bits, dtype=samples.dtype)
    out = ((samples[:, None] >> sh) & 1).astype(np.uint8).reshape(-1)
    if invert:
        out ^= 1
    if max_bits is not None:
        out = out[:max_bits]
    return out


def pack_bits(bitstream: np.ndarray, pack="msb") -> bytes:
    n = len(bitstream) // 8 * 8
    return np.packbits(bitstream[:n], bitorder="big" if pack == "msb" else "little").tobytes()


def extract(audio: Audio, sel: Selection, opts: ExtractOptions) -> tuple[bytes, np.ndarray]:
    samples = select(audio, sel)
    max_bits = opts.max_bytes * 8 if opts.max_bytes else None
    bs = extract_bitstream(samples, opts.bits, opts.invert, max_bits)
    return pack_bits(bs, opts.pack), bs


def _sources(channels: int) -> list[str]:
    return [f"ch{i}" for i in range(channels)] + (["interleaved"] if channels > 1 else [])


def bit_lists(bits: list[int], max_bits: int | None = None) -> list[list[int]]:
    """Bit lists worth trying for the chosen bits.

    Without `max_bits`: the chosen bits high->low, low->high and in the order given.
    With `max_bits`: every subset of the chosen bits holding 1..max_bits bits, each
    high->low and low->high, smallest subsets first (plus the order given, if it is
    neither and is short enough).
    """
    pool = sorted(set(bits))
    if not max_bits:
        sets = [tuple(pool)] if pool else []
    else:
        sets = [c for k in range(1, min(max_bits, len(pool)) + 1) for c in itertools.combinations(pool, k)]
    out = []
    for c in sets:
        out.append(list(c[::-1]))
        if len(c) > 1:
            out.append(list(c))
    given = list(bits)
    if given and given not in out and (not max_bits or (len(set(given)) == len(given) <= max_bits)):
        out.append(given)
    return out


def count_combinations(channels: int, bits: list[int], max_bits: int | None = None) -> int:
    """len(combinations(...)) without building the list."""
    n = len(set(bits))
    if not max_bits:
        lists = len(bit_lists(bits))
    else:
        top = min(max_bits, n)
        lists = sum(math.comb(n, k) * (2 if k > 1 else 1) for k in range(1, top + 1))
        given = list(bits)
        if (len(given) == n and len(given) <= max_bits and given != sorted(given)
                and given != sorted(given, reverse=True)):
            lists += 1
    return len(_sources(channels)) * lists * 2


def combinations(channels: int, bits: list[int], max_bits: int | None = None) -> list[tuple[str, list[int], str]]:
    """Every (source, bit list, pack) to try: each channel plus all channels
    interleaved x `bit_lists` x MSB- and LSB-first packing."""
    return [(src, o, pack) for src in _sources(channels) for o in bit_lists(bits, max_bits) for pack in ("msb", "lsb")]


def try_combinations(audio: Audio, sel: Selection, opts: ExtractOptions, head_bytes=4096, preview=64,
                     max_bits: int | None = None, keep: int | None = None) -> list[dict]:
    """Extract only the first `head_bytes` of every combination and rank them.

    The sample range (start/end/step/take/reverse), inversion and max_bytes come
    from `sel` / `opts`; source, bit list and packing are varied (see `bit_lists`
    for what `max_bits` adds). Rows with a flag come first, then file signatures,
    then the most printable previews. `keep` limits the result to the best rows.
    """
    limit = min(head_bytes, opts.max_bytes) if opts.max_bytes else head_bytes
    lists = bit_lists(opts.bits, max_bits)
    printable = np.zeros(256, dtype=bool)
    printable[32:127] = True
    printable[[9, 10, 13]] = True
    rows, order = [], 0

    def rank(r):
        return (not r["flags"], not r["magic"], -r["printable"], r["order"])

    for src in _sources(audio.channels):
        samples = select(audio, replace(sel, source=src))[:limit * 8]
        one = samples.dtype.type(1)
        planes = {b: ((samples >> samples.dtype.type(b)) & one).astype(np.uint8)
                  for b in {b for o in lists for b in o}}
        for bits in lists:
            k = len(bits)
            n = -(-limit * 8 // k)
            stream = planes[bits[0]][:n] if k == 1 else np.stack([planes[b][:n] for b in bits], axis=1).reshape(-1)
            stream = stream[:limit * 8]
            if opts.invert:
                stream = stream ^ 1
            for pack in ("msb", "lsb"):
                data = pack_bits(stream, pack)
                head = data[:preview]
                rows.append({
                    "source": src, "bits": bits, "pack": pack, "head": head, "bytes": len(data),
                    "flags": [m.group().decode("latin-1") for m in FLAG_RE.finditer(data)][:50],
                    "magic": [desc for sig, desc in MAGICS if data.startswith(sig)],
                    "magic_later": _magic_later(data),
                    "printable": float(printable[np.frombuffer(head, np.uint8)].mean()) if head else 0.0,
                    "order": order,
                })
                order += 1
            if keep and len(rows) > 4 * keep + 1000:  # bound memory on big runs
                rows.sort(key=rank)
                del rows[keep:]
    rows.sort(key=rank)
    return rows[:keep] if keep else rows


# ---------------------------------------------------------------- detection

MAGICS: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"), (b"GIF89a", "GIF image"),
    (b"BM", "BMP image (weak)"),
    (b"PK\x03\x04", "ZIP archive (docx/jar/apk...)"),
    (b"PK\x05\x06", "ZIP archive (empty)"),
    (b"Rar!\x1a\x07", "RAR archive"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"%PDF", "PDF document"),
    (b"\x7fELF", "ELF executable"),
    (b"MZ", "DOS/PE executable (weak)"),
    (b"RIFF", "RIFF container (WAV/AVI/WEBP)"),
    (b"OggS", "Ogg"),
    (b"fLaC", "FLAC audio"),
    (b"ID3", "MP3 with ID3"),
    (b"\xff\xfb", "MP3 frame (weak)"),
    (b"\x00\x00\x00\x18ftyp", "MP4/MOV"), (b"\x00\x00\x00\x20ftyp", "MP4/MOV"),
    (b"SQLite format 3\x00", "SQLite database"),
    (b"-----BEGIN", "PEM block"),
    (b"\xca\xfe\xba\xbe", "Java class / Mach-O fat"),
    (b"<?xml", "XML"), (b"<svg", "SVG"), (b"<!DOCTYPE html", "HTML"),
]

FLAG_RE = re.compile(rb"[A-Za-z0-9_\-]{2,20}\{[ -|~]{2,200}?\}")
STRINGS_RE = re.compile(rb"[ -~\t]{6,}")


def _magic_later(data: bytes) -> list[dict]:
    """File signatures (4+ bytes) found after offset 0, nearest first."""
    later = []
    for sig, desc in MAGICS:
        if len(sig) < 4:
            continue
        i = data.find(sig, 1, 1 << 20)
        if i > 0:
            later.append({"offset": i, "type": desc})
    return sorted(later, key=lambda x: x["offset"])[:20]


def analyze_payload(data: bytes) -> dict:
    head = data[:1024]
    magic = [desc for sig, desc in MAGICS if data.startswith(sig)]
    # Magic bytes appearing later (payload with a junk prefix / length header).
    later = _magic_later(data)
    printable = sum(1 for c in head if 32 <= c < 127 or c in (9, 10, 13)) / max(1, len(head))
    flags = [m.group().decode("latin-1") for m in FLAG_RE.finditer(data[: 8 << 20])][:50]
    strings = [m.group().decode("latin-1") for m in STRINGS_RE.finditer(data[: 1 << 20])][:200]
    # Shannon entropy of the first 64 KiB.
    sample = np.frombuffer(data[:65536], dtype=np.uint8)
    ent = 0.0
    if len(sample):
        p = np.bincount(sample, minlength=256) / len(sample)
        p = p[p > 0]
        ent = float(-(p * np.log2(p)).sum())
    # A 4-byte big/little endian length prefix is a common pattern.
    length_hints = []
    if len(data) >= 4:
        for endian in ("big", "little"):
            v = int.from_bytes(data[:4], endian)
            if 0 < v <= len(data) - 4:
                length_hints.append({"endian": endian, "value": v})
    return {
        "magic": magic,
        "magic_later": later,
        "printable_ratio": printable,
        "entropy": ent,
        "flags": flags,
        "strings": strings,
        "length_prefix": length_hints,
    }


def selection_dict(sel: Selection) -> dict:
    return asdict(sel)
