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


def test_bitplanes(window):
    w = window
    w.tabs.setCurrentIndex(2)
    w.pump(2.0)
    bp = w.bitplanes
    assert bp.stats and len(bp.stats["ones"]) == 16
    assert bp.win is not None and bp.plane is not None
    bp.set_offset(1000)
    w.pump(0.5)
    assert bp.win["offset"] == 1000


def test_second_file(window):
    w = window
    w.open_path(os.path.join(w.tmp, "demo_mono24.wav"))
    w.pump(1.5)
    assert w.audio.nbits == 24 and w.file_combo.count() == 2
    w.tabs.setCurrentIndex(0)
    w.pump(0.5)
    assert w.overview.trail_panel.isVisibleTo(w.overview)
