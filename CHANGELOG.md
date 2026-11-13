# Changelog

All notable changes to wsteg are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

First public release: a desktop workbench for WAV/audio steganography CTF challenges.

### Added
- Desktop app (Qt/PySide6 + pyqtgraph) for Windows and Linux, with standalone builds.
- Exact WAV/RIFF parsing that keeps raw stored sample bits: 8/16/24/32-bit PCM, float,
  WAVE_FORMAT_EXTENSIBLE, RF64. Other formats are decoded via ffmpeg.
- Overview: format and channel stats, waveform, RIFF chunk list, data after the RIFF end.
- Spectrogram / waterfall with per-channel, Mid and Side views, linear/log frequency,
  levels histogram, and recomputation at screen resolution when zooming.
- Bit planes: per-bit ones and transition rates, a distribution heatmap, a bit viewer
  and a bit-plane image.
- Extraction: choose a channel or interleaved/sequential stream, start/end,
  `take` of every `step` samples, bit list in emit order, MSB/LSB packing, inversion.
  Results are scanned for flags, file signatures and entropy.
- `wsteg info` / `wsteg extract` command-line interface.
