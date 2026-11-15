# SPDX-License-Identifier: GPL-3.0-or-later
"""Hide a payload in chosen sample bits: the exact inverse of `extract`."""
from __future__ import annotations

import numpy as np

from .extract import (
    ExtractOptions,
    Selection,
    extract_bitstream,
    pack_bits,
    select,
    selected_count,
    stream_length,
)
from .wavio import Audio, with_samples


def capacity_bits(audio: Audio, sel: Selection, bits: list[int]) -> int:
    """How many payload bits the selected samples can hold."""
    return selected_count(stream_length(audio, sel.source), sel) * len(bits)


def selected_positions(audio: Audio, sel: Selection, count: int) -> np.ndarray:
    """Indices into `audio.raw.reshape(-1)` of the first `count` selected samples,
    in the order `select` yields them."""
    total = selected_count(stream_length(audio, sel.source), sel)
    i = np.arange(min(count, total), dtype=np.int64)
    if sel.reverse:
        i = total - 1 - i
    take = min(sel.take, sel.step)
    src = sel.start + (i // take) * sel.step + (i % take)  # index in the source stream
    if sel.source == "interleaved":
        return src
    if sel.source == "sequential":
        return (src % audio.frames) * audio.channels + src // audio.frames
    ch = int(sel.source[2:]) if sel.source.startswith("ch") else 0
    if not 0 <= ch < audio.channels:
        raise ValueError(f"channel {ch} out of range")
    return src * audio.channels + ch


def embed(audio: Audio, sel: Selection, opts: ExtractOptions, payload: bytes) -> np.ndarray:
    """Return a copy of `audio.raw` with `payload` written so that
    `extract(audio, sel, opts)` reads it back. Raises ValueError if it doesn't fit.

    Bits are consumed in `opts.bits` order per sample; a payload that ends part-way
    through a sample leaves that sample's remaining bits untouched.
    """
    bits = list(opts.bits)
    if not bits:
        raise ValueError("no bits selected")
    if len(set(bits)) != len(bits):
        raise ValueError("a bit is listed twice; each bit can hold only one payload bit")
    for b in bits:
        if not 0 <= b < audio.nbits:
            raise ValueError(f"bit {b} out of range 0..{audio.nbits - 1}")
    need, have = len(payload) * 8, capacity_bits(audio, sel, bits)
    if need > have:
        raise ValueError(f"payload needs {need:,} bits but the selection holds only {have:,}")
    raw = np.array(audio.raw, order="C")  # copy
    if not need:
        return raw
    stream = np.unpackbits(np.frombuffer(payload, dtype=np.uint8), bitorder="big" if opts.pack == "msb" else "little")
    if opts.invert:
        stream = stream ^ 1
    k = len(bits)
    flat = raw.reshape(-1)
    pos = selected_positions(audio, sel, -(-need // k))
    vals = flat[pos]
    dt = vals.dtype.type
    for j, b in enumerate(bits):
        col = stream[j::k].astype(vals.dtype)  # the samples that still get payload bit j
        v = vals[:len(col)]
        vals[:len(col)] = (v & ~(dt(1) << dt(b))) | (col << dt(b))
    flat[pos] = vals
    return raw


def hide(audio: Audio, sel: Selection, opts: ExtractOptions, payload: bytes) -> tuple[bytes, dict]:
    """Embed `payload` and return (new file bytes, stats). The result is read back
    and compared with the payload before it is returned."""
    raw = embed(audio, sel, opts, payload)
    data = with_samples(audio, raw)
    probe = Audio(name=audio.name, sample_rate=audio.sample_rate, channels=audio.channels,
                  sample_width=audio.sample_width, bits_per_sample=audio.bits_per_sample,
                  fmt_code=audio.fmt_code, raw=raw, original=b"")
    back = pack_bits(extract_bitstream(select(probe, sel), opts.bits, opts.invert, len(payload) * 8), opts.pack)
    if back != payload:
        raise RuntimeError("verification failed: the embedded data does not read back")
    changed = raw != audio.raw
    capacity = capacity_bits(audio, sel, opts.bits)
    return data, {
        "payload_bytes": len(payload), "payload_bits": len(payload) * 8,
        "capacity_bits": capacity, "samples_used": -(-len(payload) * 8 // len(opts.bits)),
        "samples_changed": int(changed.sum()),
    }
