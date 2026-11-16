# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import make_samples  # noqa: E402

from wsteg import analysis  # noqa: E402
from wsteg.extract import ExtractOptions, Selection, extract, select, selected_index, try_combinations  # noqa: E402
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


def test_try_combinations():
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    rows = try_combinations(a, Selection("ch0", 0, None, 2, 1), ExtractOptions(bits=[0, 1], pack="lsb"))
    assert len(rows) == 12 and len({(r["source"], tuple(r["bits"]), r["pack"]) for r in rows}) == 12
    assert (rows[0]["source"], rows[0]["bits"], rows[0]["pack"]) == ("ch1", [1, 0], "msb")
    assert rows[0]["head"].startswith(make_samples.PAYLOAD_STEREO[:32]) and rows[0]["flags"]
    m = load_file(os.path.join(TMP, "demo_mono24.wav"))
    rows = try_combinations(m, Selection("ch0", 1000, None, 3, 1), ExtractOptions(bits=[0]))
    assert [(r["source"], r["pack"]) for r in rows] == [("ch0", "lsb"), ("ch0", "msb")]


def test_try_combinations_bit_subsets():
    from dataclasses import replace

    from wsteg.extract import bit_lists, combinations, count_combinations
    assert bit_lists([2, 0, 1], 2) == [[0], [1], [2], [1, 0], [0, 1], [2, 0], [0, 2], [2, 1], [1, 2]]
    assert bit_lists([2, 0, 1], 3)[-3:] == [[2, 1, 0], [0, 1, 2], [2, 0, 1]]  # the order given comes last
    assert bit_lists([1, 0]) == [[1, 0], [0, 1]] and bit_lists([3], 5) == [[3]]
    for ch in (1, 2):
        for bits in ([0], [1, 0], [2, 0, 1], [5, 3, 0, 9], list(range(7))):
            for mb in (None, 1, 2, 3, 9):
                assert count_combinations(ch, bits, mb) == len(combinations(ch, bits, mb)), (ch, bits, mb)
    # the flag sits in bits 1,0 of ch1; found from a wider candidate set
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    sel, opts = Selection("ch0", 0, None, 2, 1), ExtractOptions(bits=[3, 2, 1, 0], invert=False)
    rows = try_combinations(a, sel, opts, max_bits=2)
    assert len(rows) == 3 * (4 + 6 * 2) * 2
    assert (rows[0]["source"], rows[0]["bits"], rows[0]["pack"]) == ("ch1", [1, 0], "msb") and rows[0]["flags"]
    assert try_combinations(a, sel, opts, max_bits=2, keep=5) == rows[:5]
    for inv in (False, True):  # every preview equals what a full extraction starts with
        o = replace(opts, bits=[2, 0, 1], invert=inv)
        for r in try_combinations(a, Selection("ch0", 3, 9000, 3, 2, True), o, max_bits=3):
            want, _ = extract(a, Selection(r["source"], 3, 9000, 3, 2, True),
                              replace(o, bits=r["bits"], pack=r["pack"], max_bytes=4096))
            assert r["head"] == want[:64] and r["bytes"] == len(want)


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


def test_with_samples_is_byte_identical():
    from wsteg.wavio import with_samples
    for name in ("demo_stereo16.wav", "demo_mono24.wav"):
        a = load_file(os.path.join(TMP, name))
        assert with_samples(a, a.raw) == a.original  # chunks and trailing bytes included


def test_embed_positions_match_select():
    from wsteg.embed import selected_positions
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    a.raw = (np.arange(a.raw.size) % 65521).astype(np.uint16).reshape(a.raw.shape)  # value ~ flat index
    flat = a.raw.reshape(-1)
    for sel in (Selection("ch0"), Selection("ch1", 3, 5000, 7, 3), Selection("interleaved", 1, None, 4, 1),
                Selection("sequential", 10, 400000, 5, 2), Selection("ch1", 0, 1001, 2, 1, True),
                Selection("sequential", 264590, None, 3, 2, True), Selection("ch0", 999999999)):
        want = select(a, sel)
        pos = selected_positions(a, sel, 10**9)
        assert len(pos) == len(want) and np.array_equal(flat[pos], want), sel
        assert np.array_equal(selected_positions(a, sel, 50), pos[:50])


def test_hide_roundtrip():
    from wsteg.embed import capacity_bits, hide
    from wsteg.wavio import load_bytes
    payload = bytes(range(256)) + b"flag{hidden_by_wsteg}"
    cases = [
        ("demo_stereo16.wav", Selection("ch0"), ExtractOptions(bits=[0])),
        ("demo_stereo16.wav", Selection("interleaved", 5, None, 3, 2), ExtractOptions(bits=[2, 0, 1], pack="lsb")),
        ("demo_stereo16.wav", Selection("sequential", 100, None, 1, 1, True), ExtractOptions(bits=[15, 0], invert=True)),
        ("demo_mono24.wav", Selection("ch0", 7, None, 5, 1), ExtractOptions(bits=[1, 0, 23], pack="lsb")),
    ]
    for name, sel, opts in cases:
        a = load_file(os.path.join(TMP, name))
        data, st = hide(a, sel, opts, payload)
        b = load_bytes(data, name)
        opts.max_bytes = len(payload)
        assert extract(b, sel, opts)[0] == payload, (name, sel)
        assert len(data) == len(a.original) and b.trailing == a.trailing and not b.warnings
        # only the chosen bits of the used samples may differ
        mask = 0
        for bit in opts.bits:
            mask |= 1 << bit
        diff = a.raw ^ b.raw
        assert not (diff & ~np.array(mask, dtype=diff.dtype)).any()
        assert 0 < st["samples_changed"] == int((diff != 0).sum()) <= st["samples_used"]
        assert st["capacity_bits"] == capacity_bits(a, sel, opts.bits)
    # a payload ending mid-sample leaves the remaining bits of that sample alone
    a = load_file(os.path.join(TMP, "demo_stereo16.wav"))
    data, st = hide(a, Selection("ch0"), ExtractOptions(bits=[0, 1, 2]), b"\xff")
    b = load_bytes(data, "x.wav")
    assert st["samples_used"] == 3 and (a.raw[2, 0] ^ b.raw[2, 0]) & 0b100 == 0
    assert np.array_equal(a.raw[3:], b.raw[3:])


def test_hide_capacity_and_errors():
    import pytest

    from wsteg.embed import capacity_bits, hide
    a = load_file(os.path.join(TMP, "demo_mono24.wav"))
    sel = Selection("ch0", 0, 80)
    assert capacity_bits(a, sel, [0]) == 80 and capacity_bits(a, sel, [0, 1]) == 160
    hide(a, sel, ExtractOptions(bits=[0]), b"0123456789")  # exactly full
    with pytest.raises(ValueError, match="needs 88 bits"):
        hide(a, sel, ExtractOptions(bits=[0]), b"0123456789A")
    with pytest.raises(ValueError, match="twice"):
        hide(a, sel, ExtractOptions(bits=[0, 0]), b"x")
    with pytest.raises(ValueError, match="out of range"):
        hide(a, sel, ExtractOptions(bits=[24]), b"x")
