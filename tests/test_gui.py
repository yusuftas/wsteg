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
    assert len(ex.combos) == ex.combo_table.rowCount() == 3 * 2 * 2  # L, R, interleaved x order x pack
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
    assert w.sel.sel.source == "ch1" and ex.bits() == [1, 0] and ex.pack.currentData() == "msb"
    assert ex.result["data"].startswith(make_samples.PAYLOAD_STEREO)


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


def test_second_file(window):
    w = window
    w.open_path(os.path.join(w.tmp, "demo_mono24.wav"))
    w.pump(1.5)
    assert w.audio.nbits == 24 and w.file_combo.count() == 2
    w.tabs.setCurrentIndex(0)
    w.pump(0.5)
    assert w.overview.trail_panel.isVisibleTo(w.overview)
