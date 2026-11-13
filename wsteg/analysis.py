"""Spectrogram, waveform and bit-plane analysis (numpy only)."""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

DB_MIN = -160.0  # floor for silent bins


def _window(name: str, n: int) -> np.ndarray:
    if name == "hamming":
        return np.hamming(n)
    if name == "blackman":
        return np.blackman(n)
    if name in ("rect", "rectangular", "none"):
        return np.ones(n)
    return np.hanning(n)


def spectrogram(x: np.ndarray, sr: int, nfft=2048, overlap=0.75, window="hann",
                cols=1600, t0=0.0, t1=None, max_frames_per_col=64):
    """STFT magnitude in dBFS, max-pooled to at most `cols` time columns.

    Max pooling (rather than decimation) keeps short events such as drawn
    text visible in long files. Returns (float32 dBFS [cols, bins], meta).
    """
    nfft = int(min(max(nfft, 32), 65536))
    s0 = int(max(0, t0) * sr)
    s1 = len(x) if t1 is None else int(min(len(x), max(t1, t0) * sr))
    seg = x[s0:s1]
    if len(seg) < nfft:
        seg = np.pad(seg, (0, nfft - len(seg)))
    hop = max(1, int(round(nfft * (1 - min(max(overlap, 0.0), 0.97)))))
    nframes = 1 + (len(seg) - nfft) // hop
    cols = int(max(1, min(cols, 8192)))
    # Keep the work bounded for long files: widen the hop if needed.
    if nframes > cols * max_frames_per_col:
        hop = max(1, (len(seg) - nfft) // (cols * max_frames_per_col))
        nframes = 1 + (len(seg) - nfft) // hop
    cols = min(cols, nframes)
    win = _window(window, nfft).astype(np.float32)
    scale = 2.0 / win.sum()
    frames = sliding_window_view(seg, nfft)[::hop][:nframes]
    bounds = np.linspace(0, nframes, cols + 1).astype(np.int64)
    bins = nfft // 2 + 1
    out = np.empty((cols, bins), dtype=np.float32)
    batch_frames = max(1, (1 << 22) // nfft)  # ~4M samples per FFT batch
    c = 0
    while c < cols:
        c_end = c + 1
        while c_end < cols and bounds[c_end + 1] - bounds[c] <= batch_frames:
            c_end += 1
        f0, f1 = bounds[c], bounds[c_end]
        mag = np.abs(np.fft.rfft(frames[f0:f1] * win, axis=1)).astype(np.float32)
        out[c:c_end] = np.maximum.reduceat(mag, bounds[c:c_end] - f0, axis=0)
        c = c_end
    db = np.maximum(20 * np.log10(np.maximum(out * scale, 1e-12)), DB_MIN).astype(np.float32)
    meta = {
        "cols": int(cols), "bins": int(bins), "nfft": nfft, "hop": hop,
        "sample_rate": sr,
        # requested span, and the centre times of the first / last frame
        "t0": s0 / sr, "t1": max(s1, s0 + nfft) / sr,
        "frame_t0": (s0 + nfft / 2) / sr,
        "frame_t1": (s0 + (nframes - 1) * hop + nfft / 2) / sr,
        "frames": int(nframes), "bin_hz": sr / nfft,
        "max_db": float(db.max()) if db.size else DB_MIN,
    }
    return db, meta


def waveform(x: np.ndarray, cols=2000):
    """Min/max envelope per column."""
    n = len(x)
    cols = max(1, min(cols, n))
    if n == 0:
        return [], []
    bounds = np.linspace(0, n, cols + 1).astype(np.int64)[:-1]
    return (np.minimum.reduceat(x, bounds).round(4).tolist(),
            np.maximum.reduceat(x, bounds).round(4).tolist())


def bit_stats(s: np.ndarray, nbits: int, chunks=256):
    """Per-bit statistics over a selected sample stream.

    ones:        fraction of samples with the bit set (0.5 ~ random/noise or payload)
    transitions: fraction of consecutive samples where the bit flips
    heat:        ones-fraction per bit per chunk of the stream (bit x chunk),
                 which shows *where* in the file a bit plane changes behaviour.
    """
    n = len(s)
    res = {"n": int(n), "nbits": nbits, "ones": [], "transitions": [], "heat": [], "chunk_size": 0}
    if n == 0:
        return res
    chunks = int(max(1, min(chunks, n)))
    bounds = np.linspace(0, n, chunks + 1).astype(np.int64)
    starts, sizes = bounds[:-1], np.diff(bounds)
    flips = s[1:] ^ s[:-1] if n > 1 else s[:0]
    one = s.dtype.type(1)
    for b in range(nbits):
        bit = ((s >> s.dtype.type(b)) & one).astype(np.uint8)
        res["ones"].append(float(bit.mean()))
        res["transitions"].append(float(((flips >> s.dtype.type(b)) & one).mean()) if n > 1 else 0.0)
        sums = np.add.reduceat(bit.astype(np.uint32), starts)
        res["heat"].append((sums / sizes).round(4).tolist())
    res["chunk_size"] = float(n / chunks)
    res["chunk_starts"] = starts.tolist()
    return res


def bit_image(s: np.ndarray, bit: int, max_bits=1 << 23) -> bytes:
    """One bit plane of the stream, packed MSB-first (for 2-D rendering)."""
    s = s[:max_bits]
    plane = ((s >> s.dtype.type(bit)) & s.dtype.type(1)).astype(np.uint8)
    return np.packbits(plane, bitorder="big").tobytes()
