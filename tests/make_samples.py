"""Generate demo/test WAV files with known hidden content.

    python tests/make_samples.py [outdir]

demo_stereo16.wav  44.1k 16-bit stereo
    - left: text "CTF{HIDDEN}" drawn in the spectrogram (5-11 kHz)
    - right: payload in bits 1,0 of even samples (step=2, take=1), MSB-first
demo_mono24.wav    48k 24-bit mono
    - payload in bit 0 of every 3rd sample starting at 1000, LSB-first packing
    - plus trailing bytes after the RIFF body
"""
import os
import struct
import sys

import numpy as np

FONT = {
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "{": ["00110", "01000", "01000", "10000", "01000", "01000", "00110"],
    "}": ["01100", "00010", "00010", "00001", "00010", "00010", "01100"],
}

PAYLOAD_STEREO = b"flag{even_samples_bits_1_0}"
PAYLOAD_MONO = b"flag{every_3rd_lsb_first}"


def text_tones(text, sr, t_start, col_dur=0.06, f_lo=5000, f_hi=11000, n=None):
    cols = []
    for ch in text:
        g = FONT[ch]
        for x in range(5):
            cols.append([g[6 - y][x] == "1" for y in range(7)])  # bottom row first
        cols.append([False] * 7)
    n = n or int(sr * (t_start + len(cols) * col_dur + 0.5))
    t = np.arange(n) / sr
    out = np.zeros(n)
    freqs = np.linspace(f_lo, f_hi, 7)
    for y, f in enumerate(freqs):
        env = np.zeros(n)
        for x, col in enumerate(cols):
            if col[y]:
                a = int((t_start + x * col_dur) * sr)
                env[a:a + int(col_dur * sr)] = 1.0
        out += env * np.sin(2 * np.pi * f * t)
    return out


def embed(samples_u, positions, payload, bits, pack="msb"):
    bs = np.unpackbits(np.frombuffer(payload, dtype=np.uint8), bitorder="big" if pack == "msb" else "little")
    k = len(bits)
    bs = np.concatenate([bs, np.zeros((-len(bs)) % k, dtype=np.uint8)]).reshape(-1, k)
    pos = positions[: len(bs)]
    v = samples_u[pos].astype(np.int64)
    for j, b in enumerate(bits):
        v = (v & ~(1 << b)) | (bs[:, j].astype(np.int64) << b)
    samples_u[pos] = v.astype(samples_u.dtype)


def wav_bytes(raw_bytes, sr, ch, width, extra_chunks=b"", trailing=b""):
    fmt = struct.pack("<HHIIHH", 1, ch, sr, sr * ch * width, ch * width, width * 8)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra_chunks
    body += b"data" + struct.pack("<I", len(raw_bytes)) + raw_bytes + (b"\0" if len(raw_bytes) & 1 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body + trailing


def make(outdir):
    os.makedirs(outdir, exist_ok=True)
    rng = np.random.default_rng(1)

    sr = 44100
    n = sr * 6
    t = np.arange(n) / sr
    music = 0.25 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t))
    music += 0.1 * np.sin(2 * np.pi * 330 * t) + 0.01 * rng.standard_normal(n)
    left = music + 0.05 * text_tones("CTF{HIDDEN}", sr, 0.5, n=n)
    right = music[::-1] * 0.8 + 0.01 * rng.standard_normal(n)
    L = np.clip(left * 32767, -32768, 32767).astype(np.int16).view(np.uint16)
    R = np.clip(right * 32767, -32768, 32767).astype(np.int16).view(np.uint16).copy()
    embed(R, np.arange(0, n, 2), PAYLOAD_STEREO, bits=[1, 0], pack="msb")
    inter = np.stack([L, R], axis=1).astype("<u2").tobytes()
    lst = b"LIST" + struct.pack("<I", 26) + b"INFOICMT" + struct.pack("<I", 14) + b"nothing here\0\0"
    with open(os.path.join(outdir, "demo_stereo16.wav"), "wb") as f:
        f.write(wav_bytes(inter, sr, 2, 2, extra_chunks=lst))

    sr = 48000
    n = sr * 3
    t = np.arange(n) / sr
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.001 * rng.standard_normal(n)
    v = np.clip(x * (1 << 23), -(1 << 23), (1 << 23) - 1).astype(np.int32)
    u = (v & 0xFFFFFF).astype(np.uint32)
    embed(u, np.arange(1000, n, 3), PAYLOAD_MONO, bits=[0], pack="lsb")
    b = np.stack([u & 0xFF, (u >> 8) & 0xFF, (u >> 16) & 0xFF], axis=1).astype(np.uint8).tobytes()
    with open(os.path.join(outdir, "demo_mono24.wav"), "wb") as f:
        f.write(wav_bytes(b, sr, 1, 3, trailing=b"TRAILING:look_after_riff_end"))


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "samples"))
