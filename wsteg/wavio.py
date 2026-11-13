# SPDX-License-Identifier: GPL-3.0-or-later
"""WAV loading that preserves the exact stored sample bits.

Unlike most audio libraries (which convert to float and may resample), this
parser keeps the raw stored integer of every sample so bit-level analysis is
exact. It also records every RIFF chunk and any bytes trailing the RIFF body,
which are common hiding spots in CTF challenges.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np

FORMAT_NAMES = {
    0x0001: "PCM",
    0x0003: "IEEE float",
    0x0006: "A-law",
    0x0007: "mu-law",
    0xFFFE: "Extensible",
}

RAW_DTYPES = {1: np.uint8, 2: np.uint16, 3: np.uint32, 4: np.uint32, 8: np.uint64}


@dataclass
class Chunk:
    id: str
    offset: int  # offset of chunk header in file
    size: int  # declared body size
    available: int  # body bytes actually present
    preview: bytes  # first bytes of body

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "offset": self.offset,
            "size": self.size,
            "available": self.available,
            "preview_hex": self.preview.hex(),
            "preview_ascii": _ascii(self.preview),
        }


@dataclass
class Audio:
    name: str
    sample_rate: int
    channels: int
    sample_width: int  # bytes per sample container
    bits_per_sample: int  # declared bits per sample
    fmt_code: int  # effective format code (PCM / float / ...)
    raw: np.ndarray  # (frames, channels) unsigned stored bits
    original: bytes  # original file bytes (for playback / hex views)
    chunks: list[Chunk] = field(default_factory=list)
    trailing: bytes = b""
    data_offset: int = 0
    warnings: list[str] = field(default_factory=list)
    source: str = "wav"
    path: str | None = None  # file on disk (used for playback)
    _float_cache: dict = field(default_factory=dict, repr=False)

    @property
    def is_float(self) -> bool:
        return self.fmt_code == 3

    @property
    def frames(self) -> int:
        return self.raw.shape[0]

    @property
    def nbits(self) -> int:
        """Number of bit planes in the stored container."""
        return self.sample_width * 8

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def signed(self, ch: int) -> np.ndarray:
        """Integer sample values of a channel (two's complement decoded)."""
        r = self.raw[:, ch]
        w = self.sample_width
        if w == 1:
            return r.astype(np.int16) - 128
        if w == 2:
            return r.astype(np.uint16).view(np.int16)
        if w == 3:
            v = r.astype(np.int32)
            return np.where(v >= 1 << 23, v - (1 << 24), v)
        if w == 4:
            return np.ascontiguousarray(r).view(np.int32)
        return np.ascontiguousarray(r).view(np.int64)

    def channel_float(self, ch: int) -> np.ndarray:
        """Channel as float32 in roughly [-1, 1]."""
        if ch in self._float_cache:
            return self._float_cache[ch]
        r = np.ascontiguousarray(self.raw[:, ch])
        if self.is_float and self.sample_width == 4:
            x = r.view(np.float32).astype(np.float32)
        elif self.is_float and self.sample_width == 8:
            x = r.view(np.float64).astype(np.float32)
        else:
            x = self.signed(ch).astype(np.float32) / float(1 << (self.nbits - 1))
        x = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        self._float_cache[ch] = x
        return x

    def signal(self, mode: str) -> np.ndarray:
        """Float signal for a view mode: 'ch0'.., 'mid' (L+R)/2, 'side' (L-R)/2."""
        if mode in ("mid", "side") and self.channels >= 2:
            key = mode
            if key not in self._float_cache:
                a, b = self.channel_float(0), self.channel_float(1)
                self._float_cache[key] = ((a + b) if mode == "mid" else (a - b)) * 0.5
            return self._float_cache[key]
        ch = int(mode[2:]) if mode.startswith("ch") else 0
        return self.channel_float(min(max(ch, 0), self.channels - 1))

    def info(self) -> dict:
        chans = []
        for c in range(self.channels):
            x = self.channel_float(c)
            s = self.signed(c) if not self.is_float else None
            rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0
            chans.append({
                "index": c,
                "min": float(s.min()) if s is not None and len(s) else (float(x.min()) if len(x) else 0),
                "max": float(s.max()) if s is not None and len(s) else (float(x.max()) if len(x) else 0),
                "rms_dbfs": 20 * np.log10(rms) if rms > 0 else None,
                "dc": float(x.mean()) if len(x) else 0.0,
                "unique_values": int(len(np.unique(self.raw[:, c]))) if self.frames <= 20_000_000 else None,
            })
        identical = None
        if self.channels >= 2:
            identical = bool(np.array_equal(self.raw[:, 0], self.raw[:, 1]))
        return {
            "name": self.name,
            "source": self.source,
            "file_size": len(self.original),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width": self.sample_width,
            "bits_per_sample": self.bits_per_sample,
            "nbits": self.nbits,
            "format": FORMAT_NAMES.get(self.fmt_code, f"0x{self.fmt_code:04x}"),
            "is_float": self.is_float,
            "frames": self.frames,
            "duration": self.duration,
            "data_offset": self.data_offset,
            "chunks": [c.to_dict() for c in self.chunks],
            "trailing_size": len(self.trailing),
            "trailing_hex": self.trailing[:256].hex(),
            "trailing_ascii": _ascii(self.trailing[:256]),
            "warnings": self.warnings,
            "channel_stats": chans,
            "channels_identical": identical,
        }


def _ascii(b: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def parse_wav(data: bytes, name: str = "file.wav") -> Audio:
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    warnings: list[str] = []
    rf64 = data[:4] == b"RF64"
    riff_size = struct.unpack_from("<I", data, 4)[0]
    riff_end = 8 + riff_size
    if rf64 or riff_end > len(data):
        if not rf64:
            warnings.append(f"RIFF size {riff_size} exceeds file length {len(data)}")
        riff_end = len(data)

    chunks: list[Chunk] = []
    fmt = None
    data_span = None
    ds64_data_size = None
    pos = 12
    limit = riff_end
    while pos + 8 <= limit:
        cid_b = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if cid_b == b"ds64" and size >= 16:
            ds64_data_size = struct.unpack_from("<Q", data, body + 8)[0]
        if cid_b == b"data" and rf64 and size == 0xFFFFFFFF and ds64_data_size is not None:
            size = ds64_data_size
        avail = max(0, min(size, len(data) - body))
        cid = "".join(chr(c) if 32 <= c < 127 else "?" for c in cid_b)
        chunks.append(Chunk(cid, pos, size, avail, data[body:body + min(avail, 64)]))
        if avail < size:
            warnings.append(f"chunk '{cid}' declares {size} bytes but only {avail} present")
        if cid_b == b"fmt " and fmt is None:
            fmt = data[body:body + avail]
        elif cid_b == b"data" and data_span is None:
            data_span = (body, avail)
        pos = body + size + (size & 1)
        # If the declared RIFF size ended before we found the data chunk, keep scanning.
        if pos + 8 > limit and data_span is None and limit < len(data):
            limit = len(data)
            warnings.append("data chunk lies beyond declared RIFF size")

    if fmt is None or len(fmt) < 16:
        raise ValueError("missing or short 'fmt ' chunk")
    if data_span is None:
        raise ValueError("missing 'data' chunk")

    audio_format, channels, sr, _byte_rate, block_align, bps = struct.unpack_from("<HHIIHH", fmt, 0)
    eff_format = audio_format
    if audio_format == 0xFFFE and len(fmt) >= 26:
        eff_format = struct.unpack_from("<H", fmt, 24)[0]
    if channels <= 0:
        raise ValueError("channel count is 0")
    if block_align <= 0:
        block_align = channels * ((bps + 7) // 8)
        warnings.append("block_align is 0; derived from bits_per_sample")
    width = block_align // channels
    if width * channels != block_align:
        warnings.append(f"block_align {block_align} not divisible by channels {channels}")
    if width not in RAW_DTYPES:
        raise ValueError(f"unsupported sample container width {width} bytes")
    if eff_format not in (1, 3):
        warnings.append(f"format {FORMAT_NAMES.get(eff_format, hex(eff_format))} decoded as raw integers")
    if eff_format == 3 and width not in (4, 8):
        warnings.append("float format with non-float width; treating as integer PCM")
        eff_format = 1

    start, avail = data_span
    nbytes = avail - (avail % block_align)
    if nbytes != avail:
        warnings.append(f"data chunk has {avail % block_align} stray bytes after last full frame")
    raw = _decode_raw(data, start, nbytes, width).reshape(-1, channels)

    trailing = data[riff_end:] if riff_end < len(data) else b""
    # Bytes after the last chunk but inside the RIFF are also interesting.
    return Audio(
        name=name, sample_rate=sr, channels=channels, sample_width=width,
        bits_per_sample=bps, fmt_code=eff_format, raw=raw, original=data,
        chunks=chunks, trailing=trailing, data_offset=start, warnings=warnings,
    )


def _decode_raw(data: bytes, offset: int, nbytes: int, width: int) -> np.ndarray:
    if width == 3:
        b = np.frombuffer(data, dtype=np.uint8, count=nbytes, offset=offset).reshape(-1, 3).astype(np.uint32)
        return b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    dt = np.dtype(RAW_DTYPES[width]).newbyteorder("<")
    return np.frombuffer(data, dtype=dt, count=nbytes // width, offset=offset).astype(RAW_DTYPES[width])


def decode_with_ffmpeg(data: bytes, name: str) -> Audio:
    """Fallback for non-WAV input (FLAC, MP3, OGG...): decode to WAV via ffmpeg.

    Lossless formats (FLAC, ALAC) keep their exact sample bits; lossy ones won't
    carry LSB payloads but the spectrogram is still useful.
    """
    if not shutil.which("ffmpeg"):
        raise ValueError("not a WAV file and ffmpeg is not installed")
    suffix = os.path.splitext(name)[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        codec, sample_fmt, bits = "?", "", 0
        if shutil.which("ffprobe"):
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                 "stream=codec_name,sample_fmt,bits_per_raw_sample", "-of", "json", path],
                capture_output=True, timeout=60,
            )
            try:
                s = json.loads(p.stdout)["streams"][0]
                codec = s.get("codec_name", "?")
                sample_fmt = s.get("sample_fmt", "")
                bits = int(s.get("bits_per_raw_sample") or 0)
            except (KeyError, IndexError, ValueError):
                pass
        if sample_fmt.startswith("flt") or sample_fmt.startswith("dbl"):
            pcm = "pcm_f32le"
        elif bits > 16 or sample_fmt.startswith("s32"):
            pcm = "pcm_s24le" if bits == 24 else "pcm_s32le"
        elif sample_fmt.startswith("u8"):
            pcm = "pcm_u8"
        else:
            pcm = "pcm_s16le"
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-c:a", pcm, "-f", "wav", "-"],
            capture_output=True, timeout=300,
        )
        if p.returncode != 0 or not p.stdout:
            raise ValueError("ffmpeg failed: " + p.stderr.decode(errors="replace")[:300])
    finally:
        os.unlink(path)
    audio = parse_wav(p.stdout, name)
    audio.original = data
    audio.chunks, audio.trailing = [], b""
    audio.source = f"ffmpeg ({codec} -> {pcm})"
    audio.warnings = [w for w in audio.warnings if "RIFF size" not in w]
    audio.warnings.append(f"decoded from {codec} via ffmpeg; bit planes are of the decoded PCM")
    return audio


def load_bytes(data: bytes, name: str) -> Audio:
    try:
        return parse_wav(data, name)
    except ValueError as e:
        if data[:4] in (b"RIFF", b"RF64"):
            raise
        try:
            return decode_with_ffmpeg(data, name)
        except ValueError as e2:
            raise ValueError(f"{e}; {e2}") from None


def load_file(path: str) -> Audio:
    with open(path, "rb") as f:
        audio = load_bytes(f.read(), os.path.basename(path))
    audio.path = os.path.abspath(path)
    return audio
