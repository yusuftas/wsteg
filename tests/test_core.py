import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import make_samples  # noqa: E402

from wsteg import analysis  # noqa: E402
from wsteg.extract import ExtractOptions, Selection, extract, select, selected_index  # noqa: E402
from wsteg.wavio import load_file  # noqa: E402

TMP = tempfile.mkdtemp()
make_samples.make(TMP)


def test_parse_stereo():
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    assert (a.channels, a.sample_rate, a.sample_width, a.frames) == (2, 44100, 2, 44100 * 6)
    assert [c.id for c in a.chunks] == ["fmt ", "LIST", "data"]
    assert not a.warnings


def test_extract_even_bits10():
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    sel = Selection(source="ch1", start=0, step=2, take=1)
    data, _ = extract(a, sel, ExtractOptions(bits=[1, 0], pack="msb", max_bytes=64))
    assert data.startswith(make_samples.PAYLOAD_STEREO)


def test_extract_mono24_every3rd_lsbfirst_and_trailing():
    a = load_file(os.path.join(TMP, "demo_mono24.wav"))
    assert a.sample_width == 3 and a.nbits == 24
    assert a.trailing == b"TRAILING:look_after_riff_end"
    data, _ = extract(a, Selection("ch0", 1000, None, 3, 1), ExtractOptions(bits=[0], pack="lsb", max_bytes=40))
    assert data.startswith(make_samples.PAYLOAD_MONO)
    # 24-bit sign decoding
    assert a.signed(0).min() < 0


def test_select_take():
    a = load_file(os.path.join(TMP, "demo_mono24.wav"))
    s = select(a, Selection("ch0", 5, None, 5, 2))
    src = a.raw[:, 0]
    assert s[0] == src[5] and s[1] == src[6] and s[2] == src[10] and s[3] == src[11]
    assert selected_index(Selection("ch0", 5, None, 5, 2), 3) == 11


def test_interleaved():
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    # interleaved stream: R channel's even frames are odd positions 1, 5, 9 ...
    sel = Selection("interleaved", 1, None, 4, 1)
    data, _ = extract(a, sel, ExtractOptions(bits=[1, 0], max_bytes=64))
    assert data.startswith(make_samples.PAYLOAD_STEREO)


def test_bitstats_and_spectrogram():
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    st = analysis.bit_stats(select(a, Selection("ch1")), a.nbits, 64)
    assert len(st["ones"]) == 16 and len(st["heat"][0]) == 64
    q, meta = analysis.spectrogram(a.signal("ch0"), a.sample_rate, nfft=1024, cols=400)
    assert q.shape == (meta["cols"], 513) and meta["cols"] == 400
    # text tones at ~5 kHz should be well above the floor somewhere in the file
    b5k = int(5000 / a.sample_rate * 1024)
    assert q[:, b5k].max() > q[:, b5k].min() + 30  # dB
    assert np.isfinite(meta["max_db"])
