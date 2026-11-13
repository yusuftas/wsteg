"""Spectrogram / waterfall tab with detail-on-zoom recomputation."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import analysis
from .common import SPEC_CMAPS, T, colormap, fmt_hz, fmt_time, hint, labeled, row, seg_buttons
from .selection import channel_name


class HzAxis(pg.AxisItem):
    """Frequency axis; in log mode the view coordinate is log10(Hz)."""
    log = False

    def tickValues(self, minVal, maxVal, size):
        if not self.log:
            return super().tickValues(minVal, maxVal, size)
        major, minor = [], []
        for e in range(0, 7):
            for k in (1, 2, 5):
                v = math.log10(k * 10 ** e)
                if minVal <= v <= maxVal:
                    (major if k == 1 else minor).append(v)
        if len(major) < 2:
            major, minor = sorted(major + minor), []
        return [(1.0, major), (0.3, minor)]

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            f = 10 ** v if self.log else v * scale
            out.append(f"{f / 1000:g}k" if abs(f) >= 1000 else f"{f:g}")
        return out


class SpectrogramTab(QWidget):
    seek = Signal(float)
    status = Signal(str)

    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.audio = None
        self.mode = "ch0"
        self.orient = "h"
        self.scale = "lin"
        self.db = None
        self.meta = None
        self.leveled = False
        self.dirty = False
        self._computed = None  # (t0, t1, cols)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.mode_box, self.mode_btns = QWidget(), {}
        self.mode_row = QVBoxLayout(self.mode_box)
        self.mode_row.setContentsMargins(0, 0, 0, 0)
        self.nfft = QComboBox()
        for n in (256, 512, 1024, 2048, 4096, 8192, 16384, 32768):
            self.nfft.addItem(str(n), n)
        self.nfft.setCurrentText("2048")
        self.overlap = QComboBox()
        for v, l in ((0, "0%"), (0.5, "50%"), (0.75, "75%"), (0.875, "87.5%"), (0.9375, "93.75%")):
            self.overlap.addItem(l, v)
        self.overlap.setCurrentIndex(2)
        self.window = QComboBox()
        for v, l in (("hann", "Hann"), ("hamming", "Hamming"), ("blackman", "Blackman"), ("rect", "Rectangular")):
            self.window.addItem(l, v)
        self.cmap = QComboBox()
        self.cmap.addItems(SPEC_CMAPS)
        orient_w, self.orient_btns = seg_buttons([("h", "Spectrogram"), ("w", "Waterfall")], "h", self._set_orient)
        scale_w, self.scale_btns = seg_buttons([("lin", "Linear"), ("log", "Log")], "lin", self._set_scale)
        mouse_w, _ = seg_buttons([("rect", "Box zoom"), ("pan", "Pan")], "rect", self._set_mouse)
        self.auto_btn = QPushButton("Auto levels")
        self.reset_btn = QPushButton("Reset view")
        self.png_btn = QPushButton("Save PNG…")
        self.loading = QLabel("")
        self.loading.setStyleSheet(f"color: {T['warn']}")

        lay.addLayout(row(self.mode_box, labeled("FFT", self.nfft), labeled("Overlap", self.overlap),
                          labeled("Window", self.window), labeled("Colors", self.cmap), orient_w, scale_w))
        lay.addLayout(row(mouse_w, self.auto_btn, self.reset_btn, self.png_btn, self.loading, None,
                          hint("drag a box to zoom (detail is recomputed) · wheel zooms · click seeks · "
                               "double-click resets · drag the bar on the right to set levels", wrap=False),
                          stretch=False))

        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, 1)
        self.readout = QLabel(" ")
        self.readout.setStyleSheet(f"color: {T['text2']}; font-family: monospace")
        self.info = QLabel(" ")
        self.info.setStyleSheet(f"color: {T['muted']}; font-family: monospace")
        lay.addLayout(row(self.readout, None, self.info, stretch=False))

        self.img = pg.ImageItem()
        self.hist = pg.HistogramLUTItem()
        self.hist.setImageItem(self.img)
        self.hist.gradient.setColorMap(colormap("magma"))
        self.playhead = None
        self.plot = None
        self._build_plot()

        self.timer = QTimer(self, singleShot=True, interval=180)
        self.timer.timeout.connect(self._maybe_recompute)
        for w in (self.nfft, self.overlap, self.window):
            w.currentIndexChanged.connect(lambda: self.compute(force=True))
        self.cmap.currentTextChanged.connect(lambda n: self.hist.gradient.setColorMap(colormap(n)))
        self.auto_btn.clicked.connect(self.auto_levels)
        self.reset_btn.clicked.connect(self.reset_view)
        self.png_btn.clicked.connect(self.save_png)
        self.glw.scene().sigMouseMoved.connect(self._hover)
        self.glw.scene().sigMouseClicked.connect(self._clicked)

    # ---------------------------------------------------------------- layout
    def _build_plot(self):
        if self.plot is not None:  # detach the long-lived items so clear() doesn't delete them
            self.plot.removeItem(self.img)
            self.glw.removeItem(self.hist)
        self.glw.clear()
        horiz = self.orient == "h"
        self.faxis = HzAxis(orientation="left" if horiz else "bottom")
        self.faxis.log = self.scale == "log"
        axes = {"left": self.faxis} if horiz else {"bottom": self.faxis}
        self.plot = self.glw.addPlot(row=0, col=0, axisItems=axes)
        self.plot.addItem(self.img)
        self.plot.setLabel("left" if horiz else "bottom", "frequency (Hz)")
        self.plot.setLabel("bottom" if horiz else "left", "time (s)")
        vb = self.plot.getViewBox()
        vb.invertY(not horiz)
        vb.setMouseMode(pg.ViewBox.RectMode if getattr(self, "_mouse", "rect") == "rect" else pg.ViewBox.PanMode)
        vb.sigRangeChanged.connect(lambda *_: self.timer.start())
        self.glw.addItem(self.hist, row=0, col=1)
        self.playhead = pg.InfiniteLine(0, angle=90 if horiz else 0, pen=pg.mkPen(T["text"], width=1))
        self.playhead.setVisible(False)
        self.plot.addItem(self.playhead)

    def _freq_extent(self):
        """(low, high) of the frequency axis in view coordinates."""
        nyq = self.audio.sample_rate / 2
        if self.scale == "log":
            return math.log10(self._log_fmin()), math.log10(nyq)
        return 0.0, nyq

    def _log_fmin(self):
        return max(10.0, self.audio.sample_rate / int(self.nfft.currentData()))

    def _set_limits_and_full_view(self):
        if not self.audio:
            return
        f0, f1 = self._freq_extent()
        d = max(self.audio.duration, 1e-3)
        vb = self.plot.getViewBox()
        if self.orient == "h":
            vb.setLimits(xMin=0, xMax=d, yMin=f0, yMax=f1)
            vb.setRange(xRange=(0, d), yRange=(f0, f1), padding=0)
        else:
            vb.setLimits(xMin=f0, xMax=f1, yMin=0, yMax=d)
            vb.setRange(xRange=(f0, f1), yRange=(0, d), padding=0)

    # ---------------------------------------------------------------- controls
    def _set_orient(self, v):
        if v == self.orient:
            return
        tr, fr = self._visible_time(), self._visible_freq()
        self.orient = v
        self._build_plot()
        self._set_limits_and_full_view()
        self._restore_view(tr, fr)
        self._show()

    def _set_scale(self, v):
        if v == self.scale:
            return
        tr, fr = self._visible_time(), self._visible_freq()
        self.scale = v
        self.faxis.log = v == "log"
        self._set_limits_and_full_view()
        self._restore_view(tr, fr)
        self._show()

    def _set_mouse(self, v):
        self._mouse = v
        if self.plot:
            self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode if v == "rect" else pg.ViewBox.PanMode)

    def _set_mode(self, v):
        self.mode = v
        self.compute(force=True)

    def _restore_view(self, tr, fr):
        """Restore a (time range, freq range in Hz) after an axis change."""
        if not tr or not fr:
            return
        conv = (lambda f: math.log10(max(f, self._log_fmin()))) if self.scale == "log" else (lambda f: f)
        f0, f1 = conv(fr[0]), conv(fr[1])
        vb = self.plot.getViewBox()
        if self.orient == "h":
            vb.setRange(xRange=tr, yRange=(f0, f1), padding=0)
        else:
            vb.setRange(xRange=(f0, f1), yRange=tr, padding=0)

    # ---------------------------------------------------------------- data
    def set_audio(self, audio):
        self.audio = audio
        self.db = self.meta = self._computed = None
        self.leveled = False
        self.img.clear()
        # channel mode buttons
        while self.mode_row.count():
            it = self.mode_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        opts = [(f"ch{i}", channel_name(i, audio.channels)) for i in range(audio.channels)]
        if audio.channels >= 2:
            opts += [("mid", "Mid (L+R)"), ("side", "Side (L−R)")]
        if self.mode not in dict(opts):
            self.mode = "ch0"
        w, self.mode_btns = seg_buttons(opts, self.mode, self._set_mode)
        self.mode_row.addWidget(w)
        self._set_limits_and_full_view()
        self.dirty = True

    def refresh_if_dirty(self):
        if self.dirty and self.audio:
            self.dirty = False
            self._set_limits_and_full_view()
            self.compute(force=True)

    def _visible_time(self):
        if not self.plot:
            return None
        (x0, x1), (y0, y1) = self.plot.getViewBox().viewRange()
        return (x0, x1) if self.orient == "h" else (y0, y1)

    def _visible_freq(self):
        if not self.plot or not self.audio:
            return None
        (x0, x1), (y0, y1) = self.plot.getViewBox().viewRange()
        a, b = (y0, y1) if self.orient == "h" else (x0, x1)
        return (10 ** a, 10 ** b) if self.scale == "log" else (a, b)

    def _time_pixels(self):
        vb = self.plot.getViewBox()
        size = vb.size()
        px = size.width() if self.orient == "h" else size.height()
        return int(max(64, min(8192, px * self.devicePixelRatioF())))

    def _maybe_recompute(self):
        if not self.audio or self.dirty or not self.isVisible():
            return
        t0, t1 = self._visible_time()
        d = self.audio.duration
        t0, t1 = max(0.0, t0), min(d, t1)
        if t1 <= t0:
            return
        cols = self._time_pixels()
        if self._computed:
            c0, c1, ccols = self._computed
            have_res = ccols / max(c1 - c0, 1e-9)
            need_res = cols / (t1 - t0)
            if c0 <= t0 + 1e-6 and c1 >= t1 - 1e-6 and have_res >= need_res * 0.8:
                return
        self.compute()

    def compute(self, force=False):
        if not self.audio:
            return
        vt = self._visible_time() or (0, self.audio.duration)
        t0, t1 = max(0.0, vt[0]), min(self.audio.duration, vt[1])
        if t1 - t0 < 1e-4:
            t0, t1 = 0.0, self.audio.duration
        cols = self._time_pixels()
        self.loading.setText("computing…")
        x = self.audio.signal(self.mode)
        self.runner.submit(
            "spectrogram", analysis.spectrogram, x, self.audio.sample_rate,
            nfft=int(self.nfft.currentData()), overlap=float(self.overlap.currentData()),
            window=self.window.currentData(), cols=cols, t0=t0, t1=t1,
            on_ok=self._got,
        )

    def _got(self, res):
        self.db, self.meta = res
        m = self.meta
        self._computed = (m["t0"], m["t1"], m["cols"])
        self.loading.setText("")
        if not self.leveled:
            self.auto_levels()
            self.leveled = True
        self._show()
        self.info.setText(
            f"nfft {m['nfft']} · hop {m['hop']} · {m['frames']:,} frames → {m['cols']} cols"
            f"{' (max-pooled)' if m['frames'] > m['cols'] else ''} · bin {m['bin_hz']:.2f} Hz · "
            f"computed {fmt_time(m['t0'])}–{fmt_time(m['t1'])} · peak {m['max_db']:.1f} dBFS")

    def _show(self):
        """Place the current dB matrix in the plot for the active orientation/scale."""
        if self.db is None:
            return
        m, db = self.meta, self.db
        bins, binhz = db.shape[1], m["bin_hz"]
        if self.scale == "log":
            fmin, nyq = self._log_fmin(), self.audio.sample_rate / 2
            nrows = int(min(2048, max(256, bins)))
            freqs = np.logspace(math.log10(fmin), math.log10(nyq), nrows)
            idx = np.clip(np.round(freqs / binhz).astype(int), 0, bins - 1)
            fimg = db[:, idx]
            f0, fh = math.log10(fmin), math.log10(nyq) - math.log10(fmin)
        else:
            fimg = db
            f0, fh = -binhz / 2, bins * binhz
        levels = self.hist.getLevels()
        t0, th = m["t0"], m["t1"] - m["t0"]
        if self.orient == "h":
            self.img.setImage(fimg.T, autoLevels=False, levels=levels)
            self.img.setRect(QRectF(t0, f0, th, fh))
        else:
            self.img.setImage(fimg, autoLevels=False, levels=levels)
            self.img.setRect(QRectF(f0, t0, fh, th))

    def auto_levels(self):
        if self.db is None:
            return
        sample = self.db.ravel()[:: max(1, self.db.size // 400_000)]
        lo, hi = np.percentile(sample, [35, 99.95])
        self.hist.setLevels(float(lo), float(max(hi, lo + 10)))

    def reset_view(self):
        self._set_limits_and_full_view()

    # ---------------------------------------------------------------- mouse
    def _view_point(self, scene_pos):
        vb = self.plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return None
        p = vb.mapSceneToView(scene_pos)
        t, fv = (p.x(), p.y()) if self.orient == "h" else (p.y(), p.x())
        f = 10 ** fv if self.scale == "log" else fv
        return t, f

    def _hover(self, pos):
        if not self.audio or self.db is None:
            return
        tp = self._view_point(pos)
        if tp is None:
            self.readout.setText(" ")
            return
        t, f = tp
        m = self.meta
        s = f"t {fmt_time(t)}   f {fmt_hz(f)}"
        if m["t0"] <= t <= m["t1"] and 0 <= f <= self.audio.sample_rate / 2:
            col = min(m["cols"] - 1, int((t - m["t0"]) / max(m["t1"] - m["t0"], 1e-12) * m["cols"]))
            b = min(self.db.shape[1] - 1, int(round(f / m["bin_hz"])))
            s += f"   {self.db[col, b]:.1f} dBFS"
        self.readout.setText(s)

    def _clicked(self, ev):
        if ev.button() != Qt.LeftButton or not self.audio:
            return
        if ev.double():
            self.reset_view()
            return
        tp = self._view_point(ev.scenePos())
        if tp:
            self.seek.emit(max(0.0, tp[0]))

    def set_playhead(self, t: float, visible: bool):
        if self.playhead is not None:
            self.playhead.setValue(t)
            self.playhead.setVisible(visible)

    def save_png(self):
        if not self.audio:
            return
        base = self.audio.name.rsplit(".", 1)[0]
        path, _ = QFileDialog.getSaveFileName(self, "Save spectrogram", f"{base}_{self.mode}_spectrogram.png",
                                              "PNG (*.png)")
        if path:
            self.glw.grab().save(path)
            self.status.emit(f"saved {path}")
