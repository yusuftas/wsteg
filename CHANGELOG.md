# Changelog

All notable changes to wsteg are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Extract: **Try combinations** previews the start of the output for every source
  (each channel, all interleaved) × bit order × MSB/LSB packing, ranked by flags, file
  signatures and printable ratio. Click a row to open its settings and run it in full.
- Bit planes: any view can be popped out into its own window.

### Changed
- Extract starts with just the sample range, the bit chips and *Try combinations*. Bit
  order, packing, inversion and max bytes stay hidden until a combination is picked or
  *Manual settings* is switched on. The full output appears under the combinations table.
- The bit plane image is square by default; set a width to wrap at a specific row length.
- Bit planes shows one view at a time (statistics, heatmap, bit viewer, bit plane image)
  chosen from a switcher, instead of one long scrolling page. Views are computed only
  while they are on screen.

## [0.1.0] - 2026-11-13

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
