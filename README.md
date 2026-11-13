# wsteg: audio steganography workbench

A cross-platform desktop app (Windows + Linux) for WAV/audio stego CTF challenges,
built with Qt (PySide6) and pyqtgraph. It reads the **exact stored sample bits**,
never resampled or converted to float, so bit-level work is exact.

## Run it

**Prebuilt (no Python needed):** download from the GitHub **Releases** page, unpack `wsteg-<ver>-windows-x86_64.zip` or
`wsteg-<ver>-linux-x86_64.tar.gz` and run `wsteg-gui` (`wsteg-gui.exe` on Windows).
The same folder also has a console `wsteg` CLI.

**From source (any OS with Python ≥ 3.10):**
```bash
pip install -r requirements.txt
python -m wsteg                       # open the app
python -m wsteg challenge.wav         # open the app with a file
```
Open files with Ctrl+O, by drag & drop, from *File → Open recent*, or on the command line.
Several files can be loaded at once; switch between them with the toolbar dropdown.

Linux needs the usual Qt runtime libraries. On Debian/Ubuntu:
`sudo apt install libegl1 libxkbcommon-x11-0 libxcb-cursor0 libpulse0`.
Playback uses PulseAudio, which also works on PipeWire desktops. Set `WSTEG_NO_AUDIO=1`
to disable playback entirely.

## Features

**Overview**: format, rate, bit depth and container width, frames, per-channel stats
(an identical-L/R check, unique values, DC), a zoomable waveform (click to seek), the
RIFF chunk list with a hex/ASCII preview, and **bytes after the RIFF end** (save them
with one click).

**Spectrogram / waterfall**
- Channel views: each channel, **Mid (L+R)** and **Side (L−R)**. Side often reveals
  content hidden in the difference between channels.
- FFT 256–32768, overlap, window, colormaps, linear/log frequency axis.
- Spectrogram (time →) or waterfall (time ↓) orientation.
- Levels histogram on the right: drag to set floor and ceiling. *Auto levels* resets them.
- **Detail on zoom**: box-zoom or wheel-zoom and the visible range is recomputed at
  screen resolution. Long files are max-pooled rather than decimated, so short
  features such as drawn text don't disappear.
- Hover readout (time, frequency, dBFS). Click to seek, double-click to reset.
  Playhead follows playback. Save as PNG.

**Bit planes** (follows the shared *Samples* selection)
- Ones ratio and transition rate for every bit (MSB ← → LSB).
- Heatmap of each bit's ones ratio across the file, in chunks, with an adjustable
  color range (50 ± 1% shows faint bias). Click a cell to jump the bit viewer there.
- Bit viewer: raw bits of consecutive samples. Rows chosen in Extract are highlighted.
  Hover shows the hex, signed and binary value and the source index.
- Bit plane image: one bit per sample wrapped into rows of width W. Hidden bitmaps
  and QR codes show up here. Zoomable, and saves as a native-resolution PNG.

**Extract**
- Source: one channel, all channels interleaved (file order), or all channels sequentially.
- Samples: start/end, then **keep `take` of every `step` samples**. Presets cover all,
  even and odd. Reverse is optional.
- Bits: toggle chips or type the list. Bits are emitted **in the listed order**
  (`1,0` vs `0,1`). Bytes can be packed MSB-first or LSB-first, with optional inversion
  and a max-bytes limit.
- Result: flag-pattern search, magic bytes (also at later offsets), entropy, printable
  ratio and a length-prefix hint. Views: hex, text, bits and strings. Save as `.bin`.
  Ctrl+Enter runs the extraction.

Bit numbering: **bit 0 = LSB** of the stored sample container (8-bit WAV is unsigned,
16/24/32-bit are two's complement as stored, float is the raw IEEE bit pattern).
Non-WAV input (FLAC, MP3, OGG…) is decoded through `ffmpeg` when it is on PATH.

Shortcuts: Ctrl+O open, Ctrl+W close file, Ctrl+1…4 switch tabs, Space play/pause.

## CLI

```bash
wsteg info file.wav
wsteg extract file.wav --source ch1 --step 2 --bits 1,0 --pack msb -o out.bin
wsteg extract file.wav --source interleaved --start 1 --step 4 --bits 0 --max-bytes 200
```
(`python -m wsteg …` from source.)

## Building the standalone app

PyInstaller can't cross-compile, so run the build on each target OS:
```bash
pip install -r requirements.txt pyinstaller
python scripts/build.py              # dist/<os>/wsteg/  + .zip (Windows) / .tar.gz (Linux)
python scripts/build.py --onefile    # dist/<os>/wsteg-<ver>-<os>-<arch>[.exe], a single file
```
On GitHub, CI handles this: every push to `main` builds both platforms, and pushing a
`vX.Y.Z` tag publishes a GitHub Release with both builds attached.
See [RELEASING.md](RELEASING.md).

## Tests / demo files

```bash
python tests/make_samples.py     # writes samples/demo_stereo16.wav, samples/demo_mono24.wav
python -m pytest tests
```
- `demo_stereo16.wav` has `CTF{HIDDEN}` drawn in the left channel's spectrogram and a
  flag in bits 1,0 of the right channel's even samples.
- `demo_mono24.wav` has a flag in the LSB of every third sample from sample 1000,
  packed LSB-first, plus trailing bytes.

## Layout

```
wsteg/wavio.py      WAV/RIFF parser that keeps raw sample bits (+ ffmpeg fallback)
wsteg/analysis.py   spectrogram (max-pooled STFT), waveform, bit statistics
wsteg/extract.py    sample selection, bit extraction, payload detection
wsteg/gui/          Qt desktop app (one module per tab)
packaging/          PyInstaller spec, entry points, icons
scripts/            build.py, make_icon.py
```

## License

Copyright (C) 2026 Yusuf Tas

wsteg is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

The standalone builds also bundle third-party components under their own licenses:
Qt/PySide6 (LGPL-3.0), pyqtgraph (MIT), NumPy (BSD-3-Clause), FFmpeg libraries shipped
with Qt Multimedia (LGPL-2.1+), and on Linux libxcb-cursor (MIT).
