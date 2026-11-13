# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared sample-selection model and the bar widget that edits it."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QWidget

from ..extract import Selection
from .common import T, labeled

INT_MAX = 2_147_483_647


def channel_name(i: int, channels: int) -> str:
    if channels == 2:
        return "Ch 1 (L)" if i == 0 else "Ch 2 (R)"
    return f"Ch {i + 1}"


class SelectionModel(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.sel = Selection()
        self.channels = 1
        self.frames = 0

    def set_audio(self, audio):
        self.channels, self.frames = audio.channels, audio.frames
        if self.sel.source.startswith("ch") and int(self.sel.source[2:]) >= self.channels:
            self.sel = replace(self.sel, source="ch0")
        self.changed.emit()

    def update(self, **kw):
        new = replace(self.sel, **kw)
        new.step = max(1, new.step)
        new.take = max(1, min(new.take, new.step))
        if new != self.sel:
            self.sel = new
            self.changed.emit()

    def stream_len(self) -> int:
        return self.frames * (self.channels if self.sel.source in ("interleaved", "sequential") else 1)

    def count(self) -> int:
        s = self.sel
        total = self.stream_len()
        end = total if s.end is None else min(total, s.end)
        n = max(0, end - s.start)
        take = min(s.take, s.step)
        return n // s.step * take + min(take, n % s.step)

    def describe(self) -> str:
        s = self.sel
        src = {"interleaved": "all channels interleaved", "sequential": "all channels one after another"}.get(
            s.source) or channel_name(int(s.source[2:]), self.channels)
        pick = ("every sample" if s.step == 1 else
                f"1 of every {s.step} samples" if s.take == 1 else f"{s.take} of every {s.step} samples")
        rng = f"from sample {s.start}" + (f" to {s.end}" if s.end is not None else "")
        return f"{src}, {rng}, {pick}{' (reversed)' if s.reverse else ''}"


PRESETS = [("custom", "custom"), ("all", "all samples"), ("even", "even (0,2,4…)"), ("odd", "odd (1,3,5…)")]


class SelectionBar(QWidget):
    """Source / preset / start / end / every / take / reverse — bound to a SelectionModel."""

    def __init__(self, model: SelectionModel, parent=None):
        super().__init__(parent)
        self.m = model
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        title = QLabel("<b>Samples</b>")
        self.source = QComboBox()
        self.preset = QComboBox()
        for v, l in PRESETS:
            self.preset.addItem(l, v)
        self.start = QSpinBox(); self.start.setRange(0, INT_MAX); self.start.setAccelerated(True)
        self.end = QSpinBox(); self.end.setRange(0, INT_MAX); self.end.setSpecialValueText("end"); self.end.setAccelerated(True)
        self.end.setToolTip("0 / 'end' = to the end of the stream")
        self.step = QSpinBox(); self.step.setRange(1, 1_000_000)
        self.step.setToolTip("Samples are processed in groups of this size")
        self.take = QSpinBox(); self.take.setRange(1, 1_000_000)
        self.take.setToolTip("How many consecutive samples to keep from the start of each group")
        self.reverse = QCheckBox("Reverse")
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {T['muted']}; font-family: monospace")
        for w in (title,):
            lay.addWidget(w)
        lay.addLayout(labeled("Source", self.source))
        lay.addLayout(labeled("Preset", self.preset))
        lay.addLayout(labeled("Start", self.start))
        lay.addLayout(labeled("End", self.end))
        lay.addLayout(labeled("Every", self.step))
        lay.addLayout(labeled("take", self.take))
        lay.addWidget(self.reverse)
        lay.addStretch(1)
        lay.addWidget(self.count)

        self.source.currentIndexChanged.connect(lambda: self.m.update(source=self.source.currentData()))
        self.preset.currentIndexChanged.connect(self._preset)
        self.start.valueChanged.connect(lambda v: self.m.update(start=v))
        self.end.valueChanged.connect(lambda v: self.m.update(end=v or None))
        self.step.valueChanged.connect(lambda v: self.m.update(step=v))
        self.take.valueChanged.connect(lambda v: self.m.update(take=v))
        self.reverse.toggled.connect(lambda v: self.m.update(reverse=v))
        self.m.changed.connect(self.sync)
        self.sync()

    def _preset(self):
        v = self.preset.currentData()
        base = dict(end=None, reverse=False, take=1)
        if v == "all":
            self.m.update(start=0, step=1, **base)
        elif v == "even":
            self.m.update(start=0, step=2, **base)
        elif v == "odd":
            self.m.update(start=1, step=2, **base)

    def _preset_of(self) -> str:
        s = self.m.sel
        if s.end is not None or s.reverse or s.take != 1:
            return "custom"
        if s.step == 1 and s.start == 0:
            return "all"
        if s.step == 2 and s.start in (0, 1):
            return "even" if s.start == 0 else "odd"
        return "custom"

    def sync(self):
        s = self.m.sel
        widgets = (self.source, self.preset, self.start, self.end, self.step, self.take, self.reverse)
        for w in widgets:
            w.blockSignals(True)
        self.source.clear()
        for i in range(self.m.channels):
            self.source.addItem(channel_name(i, self.m.channels), f"ch{i}")
        if self.m.channels > 1:
            self.source.addItem("All, interleaved (L R L R…)", "interleaved")
            self.source.addItem("All, sequential (L… then R…)", "sequential")
        self.source.setCurrentIndex(max(0, self.source.findData(s.source)))
        self.preset.setCurrentIndex(self.preset.findData(self._preset_of()))
        self.start.setValue(s.start)
        self.end.setValue(s.end or 0)
        self.step.setValue(s.step)
        self.take.setRange(1, s.step)
        self.take.setValue(s.take)
        self.reverse.setChecked(s.reverse)
        for w in widgets:
            w.blockSignals(False)
        self.count.setText(f"{self.m.count():,} samples selected")
