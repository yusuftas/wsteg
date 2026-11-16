# SPDX-License-Identifier: GPL-3.0-or-later
"""Offscreen smoke test of the desktop app: load a file and drive every tab.

Skipped automatically when PySide6/pyqtgraph aren't installed.
"""
import os
import sys
import tempfile
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WSTEG_NO_AUDIO", "1")  # CI runners have no sound device
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import make_samples  # noqa: E402
from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def window():
    from wsteg.gui.app import MainWindow
    from wsteg.gui.common import apply_theme

    tmp = tempfile.mkdtemp()
    make_samples.make(tmp)
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "dark")
    w = MainWindow()
    w.resize(1400, 950)
    w.show()

    def pump(sec=0.5):
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            QThreadPool.globalInstance().waitForDone(20)
        app.processEvents()

    w.pump = pump
    w.tmp = tmp
    w.open_path(os.path.join(tmp, "demo_stereo16.wav"))
    pump(2.0)
    yield w
    w.close()


def test_loaded(window):
    assert window.audio is not None and window.audio.channels == 2
    assert window.overview.chunks.rowCount() == 3


def test_spectrogram_views(window):
    w = window
    w.tabs.setCurrentIndex(1)
    w.pump(1.5)
    assert w.spec.db is not None
    w.spec.plot.getViewBox().setRange(xRange=(0.4, 2.6), yRange=(4000, 12000), padding=0)
    w.pump(1.5)
    t0, t1, _ = w.spec._computed
    assert t0 == pytest.approx(0.4, abs=0.05) and t1 == pytest.approx(2.6, abs=0.05)  # detail-on-zoom
    w.spec._set_orient("w")
    w.pump(1.0)
    w.spec._set_orient("h")
    w.spec._set_scale("log")
    w.spec.reset_view()
    w.pump(1.0)
    w.spec._set_scale("lin")
    w.spec._set_mode("side")
    w.pump(1.0)
    assert w.spec.db is not None


def test_extract_finds_flag(window):
    w = window
    w.sel.update(source="ch1", start=0, step=2, take=1)
    w.tabs.setCurrentIndex(3)
    w.pump(0.3)
    w.extract.chips[1].setChecked(True)
    assert w.extract.bits() == [1, 0]
    w.extract.max_bytes.setValue(2000)
    w.extract.run()
    w.pump(1.5)
    assert w.extract.result["data"].startswith(make_samples.PAYLOAD_STEREO)
    assert make_samples.PAYLOAD_STEREO.decode() in w.extract.summary.text()


def test_extract_combinations(window):
    w = window
    w.sel.update(source="ch0", start=0, step=2, take=1)  # wrong channel on purpose
    w.tabs.setCurrentIndex(3)
    ex = w.extract
    ex.set_audio(w.audio)  # fresh state: only the bit chips and "Try combinations" are offered
    ex.manual.setChecked(False)
    w.pump(0.2)
    assert not ex.settings.isVisible() and not ex.combo_table.isVisible() and not ex.views.isVisible()
    ex.bits_edit.setText("0,1")
    ex._text_changed()
    ex.pack.setCurrentIndex(ex.pack.findData("lsb"))
    ex._shortcut()  # Ctrl+Enter tries the combinations while the settings are closed
    w.pump(1.5)
    assert ex.combo_table.isVisible() and not ex.settings.isVisible() and not ex.views.isVisible()
    # L, R, interleaved x (b0, b1, b1+b0 in both orders) x pack
    assert len(ex.combos) == ex.combo_table.rowCount() == ex.combination_count() == 3 * 4 * 2
    best = ex.combos[0]
    assert (best["source"], best["bits"], best["pack"]) == ("ch1", [1, 0], "msb")
    assert make_samples.PAYLOAD_STEREO.decode() in ex.combo_table.item(0, 3).text()
    ex.combo_table.setCurrentCell(0, 0)  # picking a row opens its settings and runs it in full
    w.pump(1.5)
    assert ex.settings.isVisible() and ex.views.isVisible() and ex.combo_table.isVisible()
    assert "again" not in ex.combo_info.text()  # the picked source doesn't make the table stale
    w.sel.update(start=4)
    assert "again" in ex.combo_info.text()
    w.sel.update(start=0)
    # a wider candidate set: every selection of b3..b0 is tried
    ex.picker.set_bits([3, 2, 1, 0])
    assert "again" in ex.combo_info.text()
    w.sel.update(source="ch0")
    ex.run_combinations()
    w.pump(1.5)
    best = ex.combos[0]
    assert len(ex.combos) == ex.combination_count() == 3 * (4 + 11 * 2) * 2
    assert (best["source"], best["bits"]) == ("ch1", [1, 0]) and "bit selection" in ex.combo_info.text()
    ex.apply_combination(0)
    w.pump(1.5)
    assert ex.bits() == [1, 0] and "again" not in ex.combo_info.text()  # narrowed chips, table still valid
    assert ex.result["data"].startswith(make_samples.PAYLOAD_STEREO)
    # a huge run asks first, and an absurd one is refused
    asked = []
    ex.confirm_big = lambda n: asked.append(n) or False
    ex.picker.set_bits(list(range(15)))
    assert 100_000 < ex.combination_count() < 3_000_000
    ex.run_combinations()
    assert asked == [ex.combination_count()] and ex.brute.isEnabled()
    ex.picker.set_bits(list(range(16)))
    ex.picker.order.setCurrentIndex(ex.picker.order.findData("custom"))
    w.sel.update(source="interleaved")
    w.audio.channels, real = 40, w.audio.channels  # pretend: 41 sources push it over the limit
    try:
        ex.run_combinations()
    finally:
        w.audio.channels = real
    assert "too many" in ex.summary.text() and len(asked) == 1 and ex.brute.isEnabled()
    w.sel.update(source="ch1")
    ex.picker.set_bits([1, 0])


def test_bitplanes(window):
    w = window
    w.tabs.setCurrentIndex(2)
    w.pump(2.0)
    bp = w.bitplanes
    assert bp.stats and len(bp.stats["ones"]) == 16
    assert bp.win is None and bp.plane is None  # other views load only when shown
    assert bp.stack.currentWidget() is bp.slots["stats"]
    bp.show_view("viewer")
    w.pump(0.5)
    assert bp.win is not None and bp.plane is None
    bp.set_offset(1000)
    w.pump(0.5)
    assert bp.win["offset"] == 1000
    bp.show_view("image")
    w.pump(0.5)
    assert bp.plane is not None and not bp.dirty
    rows, width = bp.plane["img"].shape  # square by default
    assert rows == width and (width - 1) ** 2 < bp.plane["n"] <= width ** 2
    bp.bi_width.setValue(256)
    bp.load_plane()
    w.pump(0.5)
    assert bp.plane["img"].shape == (-(-bp.plane["n"] // 256), 256)
    bp.bi_width.setValue(0)
    bp.load_plane()
    w.pump(0.5)
    # pop a view out, keep it live while another view is selected, then dock it again
    bp.pop_out("image")
    bp.show_view("heat")
    assert bp.pages["image"].window() is bp.windows["image"]
    bp.plane = None
    w.sel.update(start=10)
    w.pump(0.8)
    assert bp.plane is not None and "window" in bp.dirty
    bp.windows["image"].close()
    w.pump(0.2)
    assert not bp.windows and bp.pages["image"].parent() is bp.slots["image"]
    w.sel.update(start=0)


def test_hide(window):
    w = window
    w.tabs.setCurrentIndex(4)
    h = w.hide_tab
    w.sel.update(source="ch0", start=0, end=None, step=1, take=1)
    w.pump(0.2)
    assert not h.go.isEnabled() and "No data yet" in h.capacity.text()
    h.set_payload_text("flag{made_with_the_hide_tab}")
    h.picker.set_bits([0, 2])
    h.pack.setCurrentIndex(h.pack.findData("lsb"))
    assert h.go.isEnabled() and "Fits" in h.capacity.text() and h.payload_info.text().startswith("28 bytes")
    w.sel.update(end=100)  # 100 samples x 2 bits = 200 bits < 224
    assert not h.go.isEnabled() and "Does not fit" in h.capacity.text() and "3 bytes short" in h.capacity.text()
    w.sel.update(end=112)
    assert h.go.isEnabled()
    secret = os.path.join(w.tmp, "secret.bin")
    with open(secret, "wb") as f:
        f.write(bytes(range(256)) * 4)
    h.set_payload_file(secret)
    assert not h.go.isEnabled()
    w.sel.update(end=None)
    assert h.go.isEnabled() and h.payload_info.text().startswith("1,024 bytes")
    out = os.path.join(w.tmp, "out_stego.wav")
    n = w.file_combo.count()
    h.hide_to(out)
    w.pump(2.5)
    assert h.last["payload_bytes"] == 1024 and "verified" in h.result.text()
    assert w.file_combo.count() == n + 1 and w.audio.name == "out_stego.wav"  # result opened
    # read it back through the Extract tab with the same settings
    ex = w.extract
    ex.picker.set_bits([0, 2])
    ex.pack.setCurrentIndex(ex.pack.findData("lsb"))
    ex.max_bytes.setValue(1024)
    ex.run()
    w.pump(1.5)
    assert ex.result["data"] == bytes(range(256)) * 4
    ex.max_bytes.setValue(0)
    w.close_file()
    w.pump(0.5)


def test_second_file(window):
    w = window
    w.open_path(os.path.join(w.tmp, "demo_mono24.wav"))
    w.pump(1.5)
    assert w.audio.nbits == 24 and w.file_combo.count() == 2
    w.tabs.setCurrentIndex(0)
    w.pump(0.5)
    assert w.overview.trail_panel.isVisibleTo(w.overview)
