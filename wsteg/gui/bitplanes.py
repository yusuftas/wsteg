# SPDX-License-Identifier: GPL-3.0-or-later
"""Bit-plane analysis tab: per-bit stats, distribution heatmap, bit viewer, bit-plane image,
shown one view at a time."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import analysis
from ..extract import select, selected_index
from .common import T, bits_cmap, diverging_cmap, hint, labeled, panel, row, seg_buttons, show_tip
from .selection import SelectionBar


def _bit_ticks(n):
    every = 1 if n <= 16 else 4 if n <= 32 else 8
    return [[(b, str(b)) for b in range(n) if b % every == 0 or b == n - 1]]


def _bit_window(audio, sel, offset, count):
    s = select(audio, sel)
    vals = s[offset:offset + count]
    shifts = np.arange(audio.nbits, dtype=vals.dtype)
    bits = ((vals[None, :] >> shifts[:, None]) & 1).astype(np.uint8) if len(vals) else np.zeros((audio.nbits, 0), np.uint8)
    return {"total": len(s), "offset": offset, "values": vals.copy(), "bits": bits}


def _bit_plane(audio, sel, bit, width, max_samples):
    s = select(audio, sel)[:max_samples]
    plane = ((s >> s.dtype.type(bit)) & s.dtype.type(1)).astype(np.uint8)
    if not width:  # square: the smallest width whose rows hold every sample
        width = max(1, math.isqrt(max(len(plane) - 1, 0)) + 1)
    rows = -(-len(plane) // width) if len(plane) else 1
    img = np.zeros(rows * width, np.uint8)
    img[:len(plane)] = plane
    return {"img": img.reshape(rows, width), "n": len(plane), "total": len(select(audio, sel))}


def _stats(audio, sel, chunks):
    s = select(audio, sel)
    st = analysis.bit_stats(s, audio.nbits, chunks)
    st["heat"] = np.array(st["heat"], dtype=np.float32) if st["heat"] else np.zeros((audio.nbits, 1), np.float32)
    return st


VIEWS = [("stats", "Bit statistics"), ("heat", "Distribution heatmap"),
         ("viewer", "Bit viewer"), ("image", "Bit plane image")]
# which computed data each view shows
VIEW_DATA = {"stats": "stats", "heat": "stats", "viewer": "window", "image": "plane"}


class _PopOut(QWidget):
    """Separate window holding one view; closing it docks the view back into the tab."""
    closed = Signal()

    def __init__(self, title, parent):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

    def closeEvent(self, ev):
        self.closed.emit()
        super().closeEvent(ev)


class BitPlanesTab(QWidget):
    """One view at a time, picked from the switcher; any view can be popped out into
    its own window. A view's data is only computed while that view is on screen."""

    def __init__(self, runner, sel_model, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.m = sel_model
        self.audio = None
        self.dirty = set()
        self.stats = None
        self.win = None
        self.plane = None
        self.highlight = [0]
        self.offset = 0
        self.view = "stats"
        self.pages, self.slots, self.windows = {}, {}, {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)
        outer.addWidget(SelectionBar(sel_model))
        switch, self.view_btns = seg_buttons(VIEWS, self.view, self.show_view)
        self.pop_btn = QPushButton("Pop out")
        self.pop_btn.setToolTip("Open this view in its own window, so it can sit next to another view or tab")
        self.pop_btn.clicked.connect(lambda: self.pop_out(self.view))
        outer.addLayout(row(labeled("View", switch), None, self.pop_btn, stretch=False))
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # --- per-bit bars
        page = QWidget()
        bars = QHBoxLayout(page)
        bars.setContentsMargins(0, 0, 0, 0)
        bars.setSpacing(12)
        self.ones_plot, p = self._bar_panel("Ones ratio per bit", "≈50% suggests noise or payload")
        bars.addWidget(p)
        self.trans_plot, p = self._bar_panel("Transition rate per bit", "bit differs from previous sample")
        bars.addWidget(p)
        self._add_view("stats", page)

        # --- heatmap
        p, pl = panel("Bit distribution across samples", "ones ratio per chunk · click a cell to open it in the bit viewer")
        self.range = QComboBox()
        for v, l in ((0.5, "0–100%"), (0.2, "50 ± 20%"), (0.05, "50 ± 5%"), (0.01, "50 ± 1%")):
            self.range.addItem(l, v)
        self.range.setCurrentIndex(1)
        self.range.setToolTip("Color range around 50% — narrow it to reveal subtle bias")
        self.chunks = QComboBox()
        for n in (64, 128, 256, 512, 1024, 2048):
            self.chunks.addItem(str(n), n)
        self.chunks.setCurrentText("256")
        pl.addLayout(row(labeled("Color range", self.range), labeled("Chunks", self.chunks)))
        self.heat_w = pg.PlotWidget()
        self.heat_w.setMinimumHeight(200)
        self.heat_img = pg.ImageItem()
        self.heat_w.addItem(self.heat_img)
        self.heat_w.setMouseEnabled(x=True, y=False)
        self.heat_w.setLabel("bottom", "selected sample index")
        self.heat_w.setLabel("left", "bit")
        self.heat_bar = pg.ColorBarItem(values=(0.3, 0.7), colorMap=diverging_cmap(), interactive=False,
                                        label="ones ratio", width=14)
        self.heat_bar.setImageItem(self.heat_img, insert_in=self.heat_w.getPlotItem())
        pl.addWidget(self.heat_w, 1)
        self._add_view("heat", p)

        # --- bit viewer
        p, pl = panel("Bit viewer", "raw stored bits of consecutive selected samples · highlighted rows = bits chosen in Extract")
        self.prev_btn, self.next_btn = QPushButton("◀"), QPushButton("▶")
        self.off_spin = QSpinBox()
        self.off_spin.setRange(0, 2_147_483_647)
        self.off_spin.setAccelerated(True)
        self.off_spin.setMinimumWidth(110)
        self.count = QComboBox()
        for n in (64, 128, 256, 512, 1024, 2048, 4096):
            self.count.addItem(str(n), n)
        self.count.setCurrentText("256")
        self.win_info = hint("", wrap=False)
        pl.addLayout(row(self.prev_btn, labeled("Offset", self.off_spin), self.next_btn,
                         labeled("Samples", self.count), self.win_info))
        self.win_w = pg.PlotWidget()
        self.win_w.setMinimumHeight(200)
        self.win_img = pg.ImageItem()
        self.win_img.setLookupTable(bits_cmap().getLookupTable(nPts=2))
        self.win_w.addItem(self.win_img)
        self.win_w.setMouseEnabled(x=True, y=False)
        self.win_w.setLabel("left", "bit")
        self.win_w.setLabel("bottom", "selected sample index")
        self.hl_items = []
        pl.addWidget(self.win_w, 1)
        self._add_view("viewer", p)

        # --- bit plane image
        p, pl = panel("Bit plane image", "one bit of each selected sample wrapped into rows — hidden images / QR codes show up here")
        self.bi_bit = QComboBox()
        self.bi_width = QSpinBox()
        self.bi_width.setRange(0, 65536)
        self.bi_width.setSpecialValueText("square")
        self.bi_width.setValue(0)
        self.bi_width.setMinimumWidth(80)
        self.bi_width.setToolTip("Row width in samples. 0 / 'square' picks the width that makes the image square")
        self.bi_max = QComboBox()
        for v, l in ((1 << 18, "256K"), (1 << 20, "1M"), (1 << 22, "4M"), (1 << 24, "16M")):
            self.bi_max.addItem(l, v)
        self.bi_max.setCurrentIndex(1)
        self.bi_save = QPushButton("Save PNG…")
        self.bi_info = hint("", wrap=False)
        pl.addLayout(row(labeled("Bit", self.bi_bit), labeled("Width", self.bi_width),
                         labeled("Max samples", self.bi_max), self.bi_save, self.bi_info))
        self.bi_glw = pg.GraphicsLayoutWidget()
        self.bi_glw.setMinimumHeight(200)
        self.bi_vb = self.bi_glw.addViewBox(lockAspect=True, invertY=True)
        self.bi_img = pg.ImageItem()
        self.bi_img.setLookupTable(bits_cmap().getLookupTable(nPts=2))
        self.bi_vb.addItem(self.bi_img)
        pl.addWidget(self.bi_glw, 1)
        pl.addWidget(hint("wheel to zoom, drag to pan, right-click → View All"))
        self._add_view("image", p)

        # --- wiring
        self.m.changed.connect(self._sel_changed)
        self.range.currentIndexChanged.connect(self._draw_heat)
        self.chunks.currentIndexChanged.connect(self.load_stats)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.off_spin.editingFinished.connect(lambda: self.set_offset(self.off_spin.value()))
        self.count.currentIndexChanged.connect(self.load_window)
        self.bi_bit.currentIndexChanged.connect(self.load_plane)
        self.bi_width.editingFinished.connect(self.load_plane)
        self.bi_max.currentIndexChanged.connect(self.load_plane)
        self.bi_save.clicked.connect(self._save_plane)
        for w, fn in ((self.ones_plot, self._bar_hover), (self.trans_plot, self._bar_hover),
                      (self.heat_w, self._heat_hover), (self.win_w, self._win_hover), (self.bi_glw, self._plane_hover)):
            w.scene().sigMouseMoved.connect(lambda pos, w=w, fn=fn: fn(w, pos))
        self.heat_w.scene().sigMouseClicked.connect(self._heat_click)

    def _bar_panel(self, title, sub):
        p, pl = panel(title, sub)
        w = pg.PlotWidget()
        w.setMinimumHeight(200)
        w.setMouseEnabled(x=False, y=False)
        w.hideButtons()
        w.setYRange(0, 1, padding=0.02)
        w.getPlotItem().invertX(True)
        w.showGrid(y=True, alpha=0.15)
        w.setLabel("bottom", "bit (MSB ← → LSB)")
        w.getAxis("left").setTicks([[(v, f"{int(v * 100)}%") for v in (0, 0.25, 0.5, 0.75, 1)]])
        w.addItem(pg.InfiniteLine(0.5, angle=0, pen=pg.mkPen(T["warn"], width=1, style=Qt.DashLine)))
        pl.addWidget(w, 1)
        return w, p

    # ---------------------------------------------------------------- views
    def _add_view(self, key, page):
        """Put `page` in a stack slot, next to the placeholder shown while it is popped out."""
        slot = QWidget()
        lay = QVBoxLayout(slot)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(page, 1)
        ph = QWidget()
        pl = QVBoxLayout(ph)
        pl.addStretch(1)
        pl.addWidget(hint(f"{dict(VIEWS)[key]} is open in its own window.", wrap=False), 0, Qt.AlignHCenter)
        back = QPushButton("Bring it back")
        back.clicked.connect(lambda: self.windows[key].close() if key in self.windows else None)
        pl.addWidget(back, 0, Qt.AlignHCenter)
        pl.addStretch(1)
        ph.hide()
        lay.addWidget(ph, 1)
        slot.placeholder = ph
        self.pages[key], self.slots[key] = page, slot
        self.stack.addWidget(slot)

    def show_view(self, key):
        self.view = key
        self.view_btns[key].setChecked(True)
        self.stack.setCurrentWidget(self.slots[key])
        self.pop_btn.setEnabled(key not in self.windows)
        self._refresh(self.isVisible())

    def pop_out(self, key):
        if key in self.windows:
            self.windows[key].raise_()
            self.windows[key].activateWindow()
            return
        win = _PopOut(f"{dict(VIEWS)[key]} · wsteg", self)
        win.layout().addWidget(self.pages[key])
        self.slots[key].placeholder.show()
        self.windows[key] = win
        win.closed.connect(lambda: self._dock(key))
        win.resize(1100, 620)
        win.show()
        self.pop_btn.setEnabled(key != self.view)
        self._refresh(self.isVisible())

    def _dock(self, key):
        win = self.windows.pop(key, None)
        if win is None:
            return
        slot = self.slots[key]
        slot.placeholder.hide()
        slot.layout().insertWidget(0, self.pages[key], 1)
        self.pages[key].show()
        win.deleteLater()
        self.pop_btn.setEnabled(self.view not in self.windows)

    def _shown(self, key, tab_visible):
        return key in self.windows or (tab_visible and key == self.view)

    # ---------------------------------------------------------------- data flow
    def set_audio(self, audio):
        self.audio = audio
        self.offset = 0
        self.bi_bit.blockSignals(True)
        self.bi_bit.clear()
        n = audio.nbits
        for b in range(n):
            self.bi_bit.addItem(f"bit {b}" + (" (LSB)" if b == 0 else " (MSB)" if b == n - 1 else ""), b)
        self.bi_bit.blockSignals(False)
        for w in (self.ones_plot, self.trans_plot):
            w.getAxis("bottom").setTicks(_bit_ticks(n))
            w.setXRange(-0.6, n - 0.4, padding=0)
        for w in (self.heat_w, self.win_w):
            w.getAxis("left").setTicks(_bit_ticks(n))
            w.setYRange(-0.5, n - 0.5, padding=0)
        self.dirty = {"stats", "window", "plane"}
        self._refresh(self.isVisible())

    def refresh_if_dirty(self):
        """Called when the tab is shown."""
        self._refresh(True)

    def _refresh(self, tab_visible):
        if not self.audio:
            return
        for data, load in (("stats", self.load_stats), ("window", self.load_window), ("plane", self.load_plane)):
            if data in self.dirty and any(self._shown(k, tab_visible) for k, d in VIEW_DATA.items() if d == data):
                load()

    def _sel_changed(self):
        if not self.audio:
            return
        self.offset = 0
        self.dirty = {"stats", "window", "plane"}
        self._refresh(self.isVisible())

    def load_stats(self):
        if self.audio:
            self.dirty.discard("stats")
            self.runner.submit("bitstats", _stats, self.audio, self.m.sel, int(self.chunks.currentData()),
                               on_ok=self._got_stats)

    def load_window(self):
        if self.audio:
            self.dirty.discard("window")
            self.runner.submit("bitwindow", _bit_window, self.audio, self.m.sel, self.offset,
                               int(self.count.currentData()), on_ok=self._got_window)

    def load_plane(self):
        if self.audio and self.bi_bit.count():
            self.dirty.discard("plane")
            self.runner.submit("bitplane", _bit_plane, self.audio, self.m.sel, int(self.bi_bit.currentData() or 0),
                               self.bi_width.value(), int(self.bi_max.currentData()), on_ok=self._got_plane)

    def set_offset(self, off):
        self.offset = max(0, int(off))
        self.load_window()

    def _step(self, d):
        self.set_offset(self.offset + d * int(self.count.currentData()))

    def set_highlight_bits(self, bits):
        self.highlight = list(bits)
        self._draw_bars()
        self._draw_highlight()

    # ---------------------------------------------------------------- drawing
    def _got_stats(self, st):
        self.stats = st
        self._draw_bars()
        self._draw_heat()

    def _draw_bars(self):
        if not self.stats:
            return
        n = self.stats["nbits"]
        hl = set(self.highlight)
        brushes = [pg.mkBrush(T["accent"]) if b in hl else pg.mkBrush(QColorA(T["accent"], 150)) for b in range(n)]
        for w, key in ((self.ones_plot, "ones"), (self.trans_plot, "transitions")):
            for it in [i for i in w.getPlotItem().items if isinstance(i, pg.BarGraphItem)]:
                w.removeItem(it)
            w.addItem(pg.BarGraphItem(x=np.arange(n), height=self.stats[key], width=0.8, brushes=brushes,
                                      pen=pg.mkPen(None)))

    def _draw_heat(self):
        if not self.stats:
            return
        st = self.stats
        r = float(self.range.currentData())
        n_samples = max(1, st["n"])
        self.heat_img.setImage(st["heat"], autoLevels=False)
        self.heat_img.setRect(QRectF(0, -0.5, n_samples, st["nbits"]))
        self.heat_bar.setLevels((0.5 - r, 0.5 + r))
        self.heat_w.getViewBox().setLimits(xMin=0, xMax=n_samples, yMin=-0.5, yMax=st["nbits"] - 0.5)
        self.heat_w.setXRange(0, n_samples, padding=0)

    def _got_window(self, w):
        self.win = w
        vals = w["bits"]
        cnt = vals.shape[1]
        self.off_spin.blockSignals(True)
        self.off_spin.setValue(w["offset"])
        self.off_spin.blockSignals(False)
        self.win_img.setImage(vals, autoLevels=False, levels=(0, 1))
        self.win_img.setRect(QRectF(w["offset"] - 0.5, -0.5, max(cnt, 1), vals.shape[0]))
        self.win_w.setXRange(w["offset"] - 0.5, w["offset"] + int(self.count.currentData()) - 0.5, padding=0)
        self.win_info.setText(f"samples {w['offset']:,}–{w['offset'] + cnt - 1:,} of {w['total']:,} selected"
                              if cnt else f"offset beyond end ({w['total']:,} selected)")
        self._draw_highlight()

    def _draw_highlight(self):
        for it in self.hl_items:
            self.win_w.removeItem(it)
        self.hl_items = []
        col = QColorA(T["accent"], 70)
        for b in self.highlight:
            it = pg.LinearRegionItem((b - 0.5, b + 0.5), orientation="horizontal", movable=False,
                                     brush=pg.mkBrush(col), pen=pg.mkPen(T["accent"], width=1))
            it.setZValue(10)
            self.win_w.addItem(it)
            self.hl_items.append(it)

    def _got_plane(self, p):
        self.plane = p
        img = p["img"]
        self.bi_img.setImage(img, autoLevels=False, levels=(0, 1))
        self.bi_vb.autoRange(padding=0.01)
        self.bi_info.setText(f"{p['n']:,} of {p['total']:,} samples · {img.shape[1]}×{img.shape[0]}")

    # ---------------------------------------------------------------- hover / click
    @staticmethod
    def _vpos(w, pos):
        vb = w.getViewBox() if hasattr(w, "getViewBox") else None
        if vb is None or not vb.sceneBoundingRect().contains(pos):
            return None
        return vb.mapSceneToView(pos)

    def _bar_hover(self, w, pos):
        p = self._vpos(w, pos)
        if p is None or not self.stats:
            return
        b = int(round(p.x()))
        if 0 <= b < self.stats["nbits"]:
            show_tip(f"bit {b}{' (LSB)' if b == 0 else ''}\nones   {self.stats['ones'][b] * 100:.2f}%\n"
                     f"flips  {self.stats['transitions'][b] * 100:.2f}%", w)

    def _heat_cell(self, pos):
        p = self._vpos(self.heat_w, pos)
        if p is None or not self.stats:
            return None
        st = self.stats
        k = st["heat"].shape[1]
        j = int(p.x() / max(1, st["n"]) * k)
        b = int(round(p.y()))
        if 0 <= j < k and 0 <= b < st["nbits"]:
            return j, b
        return None

    def _heat_hover(self, w, pos):
        c = self._heat_cell(pos)
        if c:
            j, b = c
            st = self.stats
            s0 = st["chunk_starts"][j]
            s1 = (st["chunk_starts"][j + 1] if j + 1 < len(st["chunk_starts"]) else st["n"]) - 1
            show_tip(f"bit {b}  chunk {j}\nsamples {s0:,}–{s1:,}\nones {st['heat'][b, j] * 100:.1f}%", w)

    def _heat_click(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        c = self._heat_cell(ev.scenePos())
        if c:
            self.set_offset(self.stats["chunk_starts"][c[0]])
            if "viewer" in self.windows:
                self.windows["viewer"].raise_()
            else:
                self.show_view("viewer")

    def _win_hover(self, w, pos):
        p = self._vpos(w, pos)
        if p is None or not self.win or not self.audio:
            return
        i = int(round(p.x())) - self.win["offset"]
        b = int(round(p.y()))
        vals = self.win["values"]
        if not (0 <= i < len(vals) and 0 <= b < self.audio.nbits):
            return
        v, nb = int(vals[i]), self.audio.nbits
        idx = self.win["offset"] + i
        lines = [f"sample {idx:,}"]
        if not self.m.sel.reverse:
            lines.append(f"source idx {selected_index(self.m.sel, idx):,}")
        lines.append(f"raw  0x{v:0{nb // 4}x}")
        if not self.audio.is_float and nb <= 32:
            lines.append(f"int  {v - 128 if nb == 8 else (v - (1 << nb) if v >= 1 << (nb - 1) else v)}")
        lines.append(f"bit {b} = {(v >> b) & 1}")
        bs = f"{v:0{nb}b}"
        lines.append(" ".join(bs[k:k + 4] for k in range(0, nb, 4)))
        show_tip("\n".join(lines), w)

    def _plane_hover(self, w, pos):
        if not self.plane or not self.bi_vb.sceneBoundingRect().contains(pos):
            return
        p = self.bi_vb.mapSceneToView(pos)
        x, y = int(p.x()), int(p.y())
        width = self.plane["img"].shape[1]
        i = y * width + x
        if 0 <= x < width and 0 <= i < self.plane["n"]:
            show_tip(f"x {x}  y {y}\nselected sample {i:,}", w)

    def _save_plane(self):
        if not self.plane:
            return
        name = f"bitplane_b{self.bi_bit.currentData()}_w{self.plane['img'].shape[1]}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save bit plane image", name, "PNG (*.png)")
        if path:
            self.bi_img.save(path)


def QColorA(hex_color, alpha):  # noqa: N802 - small factory
    from PySide6.QtGui import QColor
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c
