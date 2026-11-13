# SPDX-License-Identifier: GPL-3.0-or-later
"""Overview tab: file facts, channel stats, waveform, RIFF chunks, trailing data."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import analysis
from .common import T, code_view, fmt_time, hexdump, mono_font, panel
from .selection import channel_name

RAW_PLOT_LIMIT = 4_000_000  # plot raw samples (with peak downsampling) up to this many per channel


def _num(v, is_float):
    return f"{v:.6g}" if is_float else str(int(v))


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.horizontalHeader().setStretchLastSection(True)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    return t


def _fill(t: QTableWidget, rows, mono_cols=()):
    t.setRowCount(len(rows))
    for r, vals in enumerate(rows):
        for c, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            if c in mono_cols:
                it.setFont(mono_font(9))
            t.setItem(r, c, it)
    t.resizeColumnsToContents()
    t.setFixedHeight(t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(t.rowCount())) + 6)


class OverviewTab(QWidget):
    seek = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        p, pl = panel("File")
        self.info = _table(["Property", "Value"])
        self.info.horizontalHeader().setVisible(False)
        pl.addWidget(self.info)
        pl.addStretch(1)
        top.addWidget(p, 1)
        p, pl = panel("Channels")
        self.chans = _table(["Ch", "Min", "Max", "RMS", "DC", "Unique values"])
        pl.addWidget(self.chans)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.RichText)
        pl.addWidget(self.warnings)
        pl.addStretch(1)
        top.addWidget(p, 1)
        lay.addLayout(top)

        p, pl = panel("Waveform", "drag/wheel to zoom · click to seek")
        self.wave = pg.GraphicsLayoutWidget()
        self.wave.setMinimumHeight(240)
        self.wave.scene().sigMouseClicked.connect(self._clicked)
        pl.addWidget(self.wave)
        lay.addWidget(p)

        p, pl = panel("RIFF chunks")
        self.chunks = _table(["ID", "Offset", "Size", "First bytes", "ASCII"])
        pl.addWidget(self.chunks)
        lay.addWidget(p)

        self.trail_panel, pl = panel("Data after RIFF end")
        self.trail_label = QLabel()
        self.trail_save = QPushButton("Save trailing bytes…")
        self.trail_save.clicked.connect(self._save_trailing)
        h = QHBoxLayout(); h.addWidget(self.trail_label); h.addStretch(1); h.addWidget(self.trail_save)
        pl.addLayout(h)
        self.trail_hex = code_view()
        self.trail_hex.setMaximumHeight(220)
        pl.addWidget(self.trail_hex)
        lay.addWidget(self.trail_panel)
        lay.addStretch(1)
        self.playheads = []

    def set_audio(self, audio, info):
        self.audio = audio
        I = info
        rows = [
            ("Name", I["name"]), ("Path", audio.path or ""), ("Decoder", I["source"]),
            ("File size", f"{I['file_size']:,} bytes"),
            ("Format", I["format"] + ("" if I["is_float"] else " (integer)")),
            ("Sample rate", f"{I['sample_rate']} Hz"), ("Channels", I["channels"]),
            ("Bits / sample", f"{I['bits_per_sample']} declared, {I['nbits']}-bit container"),
            ("Frames", f"{I['frames']:,}"), ("Duration", fmt_time(I["duration"])),
            ("Audio data offset", f"0x{I['data_offset']:x} ({I['data_offset']})"),
        ]
        if I["channels"] >= 2:
            rows.append(("Ch1 == Ch2", "IDENTICAL" if I["channels_identical"] else "different"))
        _fill(self.info, rows)
        _fill(self.chans, [(
            channel_name(c["index"], I["channels"]), _num(c["min"], I["is_float"]), _num(c["max"], I["is_float"]),
            "silent" if c["rms_dbfs"] is None else f"{c['rms_dbfs']:.1f} dBFS",
            f"{c['dc']:.2e}", c["unique_values"] if c["unique_values"] is not None else "–",
        ) for c in I["channel_stats"]], mono_cols=(1, 2, 3, 4, 5))
        self.warnings.setText("<br>".join(f'<span style="color:{T["warn"]}">⚠ {w}</span>' for w in I["warnings"]))
        _fill(self.chunks, [(
            c["id"], f"0x{c['offset']:x}",
            f"{c['size']:,}" + (f"  (only {c['available']:,} present)" if c["available"] < c["size"] else ""),
            "" if c["id"] == "data" else " ".join(c["preview_hex"][i:i + 2] for i in range(0, min(64, len(c["preview_hex"])), 2)),
            "(audio)" if c["id"] == "data" else c["preview_ascii"],
        ) for c in I["chunks"]], mono_cols=(0, 1, 2, 3, 4))
        self.trail_panel.setVisible(bool(I["trailing_size"]))
        if I["trailing_size"]:
            self.trail_label.setText(f'<span style="color:{T["warn"]}"><b>{I["trailing_size"]:,} bytes</b></span> '
                                     f'after the end of the RIFF data')
            self.trail_hex.setPlainText(hexdump(audio.trailing[:64 * 1024]) +
                                        ("\n…" if len(audio.trailing) > 64 * 1024 else ""))
        self._plot_wave()

    def _plot_wave(self):
        a = self.audio
        self.wave.clear()
        self.playheads = []
        first = None
        sr = a.sample_rate
        for c in range(a.channels):
            p = self.wave.addPlot(row=c, col=0)
            p.setLabel("left", channel_name(c, a.channels))
            p.setMouseEnabled(x=True, y=False)
            p.setYRange(-1, 1, padding=0.05)
            p.getViewBox().setLimits(xMin=0, xMax=max(a.duration, 1e-3))
            x = a.channel_float(c)
            pen = pg.mkPen(T["accent"], width=1)
            if len(x) <= RAW_PLOT_LIMIT:
                t = np.arange(len(x), dtype=np.float64) / sr
                curve = p.plot(t, x, pen=pen)
                curve.setDownsampling(auto=True, method="peak")
                curve.setClipToView(True)
            else:
                mn, mx = analysis.waveform(x, 20000)
                t = np.linspace(0, a.duration, len(mn))
                c1, c2 = p.plot(t, mn, pen=pen), p.plot(t, mx, pen=pen)
                p.addItem(pg.FillBetweenItem(c1, c2, brush=pg.mkBrush(T["accent"])))
            if first is None:
                first = p
            else:
                p.setXLink(first)
            ph = pg.InfiniteLine(0, angle=90, pen=pg.mkPen(T["text"], width=1))
            ph.setVisible(False)
            p.addItem(ph)
            self.playheads.append(ph)
        if first is not None:
            first.setXRange(0, a.duration, padding=0)
            last = self.wave.getItem(a.channels - 1, 0)
            last.setLabel("bottom", "time", units="s")
        self.wave.setMinimumHeight(110 * a.channels + 40)

    def _clicked(self, ev):
        if ev.button() != Qt.LeftButton or ev.double():
            return
        for c in range(self.audio.channels if self.audio else 0):
            p = self.wave.getItem(c, 0)
            if p is not None and p.sceneBoundingRect().contains(ev.scenePos()):
                t = p.getViewBox().mapSceneToView(ev.scenePos()).x()
                self.seek.emit(max(0.0, t))
                return

    def set_playhead(self, t: float, visible: bool):
        for ph in self.playheads:
            ph.setValue(t)
            ph.setVisible(visible)

    def _save_trailing(self):
        if not self.audio:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save trailing bytes", self.audio.name + ".trailing.bin")
        if path:
            with open(path, "wb") as f:
                f.write(self.audio.trailing)
