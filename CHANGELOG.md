# Changelog

All notable changes to wsteg are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `wsteg extract -a`: *Try combinations* from the command line, similar to `zsteg -a`.
  Candidate bits are 0..`--upto N` (default 3) or an explicit `--bits` set. Ranked rows
  are printed with the `wsteg extract` command for each hit, or as JSON with `--json`.
- Coloured `extract -a` output (zsteg-style): file signatures and text at the start of the
  output stand out, noise is dimmed. `--color auto|always|never`, `NO_COLOR` is respected.

### Changed
- *Try combinations* ranks by file signature, then text at the start of the output, then
  the longest run of printable text in the first 4 KiB (a new *Text run* column), then
  printable ratio. Rows starting with text are highlighted like file signatures.

### Removed
- The flag-pattern search (`xxx{...}`) in Extract and *Try combinations*. It matched random
  bytes too often and ranked noise above real payloads; the text itself is shown anyway.

## [0.3.0] - 2026-11-16

### Added
- **Hide** tab: write a file or a typed text into chosen sample bits and save the result
  as a new WAV. It uses the same sample selection, bit list, bit order, MSB/LSB packing
  and inversion as Extract, checks live that the data fits, and verifies the result by
  reading it back. Everything except the chosen bits stays byte-identical.
- `wsteg hide` command-line counterpart.
- Extract: *Try combinations* now also tries every selection of the chosen bits (each
  bit alone, every pair, and so on), so the chosen bits act as candidates. Large runs
  ask for confirmation, and the best 300 results are listed.

### Changed
- *Try combinations* is much faster: each bit plane is read once per source instead of
  once per combination.

## [0.2.0] - 2026-11-13

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
