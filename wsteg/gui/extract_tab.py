# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction tab: choose samples + bits, try every combination, then refine, analyze and save one."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..extract import ExtractOptions, analyze_payload, extract, try_combinations
from .common import T, badge, code_view, hexdump, hint, labeled, mono_font, panel, row
from .selection import SelectionBar, channel_name

HEX_PREVIEW = 64 * 1024
COMBO_COLS = ["Source", "Bits", "Pack", "Findings", "Printable", "Text", "Hex"]
START_HINT = ("Pick the bits above and press <b>Try combinations</b> (Ctrl+Enter) to preview every source, "
              "bit order and packing. Click a result to open its settings and the full output.")


def _printable(data: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 else "·" for c in data)


def _run(audio, sel, opts):
    data, bits = extract(audio, sel, opts)
    return {"data": data, "bits": bits[:4096].copy(), "total_bits": int(len(bits)),
            "analysis": analyze_payload(data), "hex": hexdump(data[:HEX_PREVIEW])}


class ExtractTab(QWidget):
    bitsChanged = Signal(list)
    status = Signal(str)

    def __init__(self, runner, sel_model, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.m = sel_model
        self.audio = None
        self.result = None
        self.chips = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(SelectionBar(sel_model))

        p, pl = panel("Bits to extract from each selected sample", "bit 0 = LSB · click to toggle")
        self.chip_grid = QGridLayout()
        self.chip_grid.setHorizontalSpacing(4)
        self.chip_grid.setVerticalSpacing(4)
        pl.addLayout(self.chip_grid)
        self.order = QComboBox()
        self.order.addItem("high → low (b1,b0)", "desc")
        self.order.addItem("low → high (b0,b1)", "asc")
        self.order.addItem("custom (edit the list)", "custom")
        self.bits_edit = QLineEdit("0")
        self.bits_edit.setToolTip("Comma-separated bit indices, emitted in exactly this order")
        self.bits_edit.setMaximumWidth(180)
        self.pack = QComboBox()
        self.pack.addItem("MSB first", "msb")
        self.pack.addItem("LSB first", "lsb")
        self.pack.setToolTip("MSB first: the first extracted bit becomes bit 7 of the first byte")
        self.invert = QCheckBox("Invert bits")
        self.max_bytes = QSpinBox()
        self.max_bytes.setRange(0, 2_000_000_000)
        self.max_bytes.setSpecialValueText("all")
        self.max_bytes.setToolTip("Stop after this many output bytes (0 = all)")
        self.go = QPushButton("Extract")
        self.go.setToolTip("Run the extraction with exactly these settings (Ctrl+Enter)")
        self.save = QPushButton("Save .bin…")
        self.save.setEnabled(False)
        self.brute = QPushButton("Try combinations")
        self.brute.setObjectName("primary")
        self.brute.setToolTip("Keep the sample range and the chosen bits, and preview the start of the output for\n"
                              "every channel / interleaved × bit order × MSB/LSB packing combination")
        self.manual = QPushButton("Manual settings")
        self.manual.setCheckable(True)
        self.manual.setToolTip("Show bit order, packing, inversion and length settings for a single extraction")
        pl.addLayout(row(self.brute, self.manual))
        # one extraction's settings: hidden until a combination is picked or "Manual settings" is on
        self.settings = QWidget()
        sl = QVBoxLayout(self.settings)
        sl.setContentsMargins(0, 4, 0, 0)
        sl.addLayout(row(labeled("Order", self.order), labeled("Bit list", self.bits_edit),
                         labeled("Pack bytes", self.pack), self.invert, labeled("Max bytes", self.max_bytes),
                         self.go, self.save))
        self.explain = hint("")
        sl.addWidget(self.explain)
        self.settings.hide()
        pl.addWidget(self.settings)
        lay.addWidget(p)

        p, pl = panel("Result")
        self.summary = QLabel(START_HINT)
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl.addWidget(self.summary)
        self.views = QTabWidget()
        self.hex_v, self.text_v, self.bits_v, self.str_v = code_view(), code_view(True), code_view(True), code_view()
        for w, n in ((self.hex_v, "Hex"), (self.text_v, "Text"), (self.bits_v, "Bits"), (self.str_v, "Strings")):
            self.views.addTab(w, n)
        self.combo_info = hint("")
        self.combo_info.hide()
        pl.addWidget(self.combo_info)
        self.combo_table = QTableWidget(0, len(COMBO_COLS))
        self.combo_table.setHorizontalHeaderLabels(COMBO_COLS)
        self.combo_table.setFont(mono_font(10))
        self.combo_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.combo_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.combo_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.combo_table.setWordWrap(False)
        self.combo_table.verticalHeader().setVisible(False)
        self.combo_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.combo_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        # combinations on top, the full output of the picked / manual extraction below
        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.combo_table)
        self.split.addWidget(self.views)
        self.combo_table.hide()
        self.views.hide()
        pl.addWidget(self.split, 1)
        self.combos = []
        self.combo_key = None
        self.combo_text = ""
        self._picked = None
        lay.addWidget(p, 1)

        self.order.currentIndexChanged.connect(self._chips_to_text)
        self.bits_edit.textEdited.connect(self._text_changed)
        self.pack.currentIndexChanged.connect(self._explain)
        self.invert.toggled.connect(self._explain)
        self.max_bytes.valueChanged.connect(self._explain)
        self.m.changed.connect(self._explain)
        self.go.clicked.connect(self.run)
        self.brute.clicked.connect(self.run_combinations)
        self.manual.toggled.connect(self.settings.setVisible)
        self.combo_table.cellPressed.connect(lambda r, _c: self._pick(r))
        self.combo_table.currentCellChanged.connect(lambda r, *_: self._pick(r))
        QShortcut(QKeySequence("Ctrl+Return"), self, self._shortcut)
        self.save.clicked.connect(self._save)

    # ---------------------------------------------------------------- bits
    def bits(self) -> list[int]:
        return [int(x) for x in re.split(r"[\s,]+", self.bits_edit.text().strip()) if x.isdigit()]

    def set_audio(self, audio):
        self.audio = audio
        self.result = None
        self.save.setEnabled(False)
        self.summary.setText(START_HINT)
        self.views.hide()
        self._clear_combos()
        for b in self.chips.values():
            b.deleteLater()
        self.chips = {}
        n = audio.nbits
        per_row = 16
        for i, b in enumerate(range(n - 1, -1, -1)):
            btn = QPushButton(f"b{b}")
            btn.setCheckable(True)
            btn.setFixedWidth(46)
            btn.setToolTip(f"bit {b}" + (" (LSB)" if b == 0 else " (MSB)" if b == n - 1 else ""))
            btn.toggled.connect(self._chips_to_text)
            r, c = divmod(i, per_row)
            self.chip_grid.addWidget(btn, r, c + (c // 8))  # gap column every 8 bits
            self.chips[b] = btn
        for c in range(per_row + per_row // 8):
            self.chip_grid.setColumnStretch(c, 0)
        self.chip_grid.setColumnStretch(per_row + per_row // 8, 1)
        bits = [b for b in self.bits() if b < n] or [0]
        self.bits_edit.setText(",".join(map(str, bits)))
        self._text_changed()

    def _sync_chips(self):
        on = set(self.bits())
        for b, btn in self.chips.items():
            btn.blockSignals(True)
            btn.setChecked(b in on)
            btn.blockSignals(False)

    def _chips_to_text(self):
        on = [b for b, btn in self.chips.items() if btn.isChecked()]
        mode = self.order.currentData()
        if mode == "custom":
            cur = [b for b in self.bits() if b in on]
            cur += [b for b in on if b not in cur]
        else:
            cur = sorted(on, reverse=mode == "desc")
        self.bits_edit.setText(",".join(map(str, cur)))
        self._emit_bits()

    def _text_changed(self):
        if self.audio and any(b >= self.audio.nbits for b in self.bits()):
            self.bits_edit.setStyleSheet(f"color: {T['bad']}")
            return
        self.bits_edit.setStyleSheet("")
        self._sync_chips()
        self._emit_bits()

    def _emit_bits(self):
        self.bitsChanged.emit(self.bits())
        self._explain()

    def _explain(self):
        if not self.audio:
            return
        bits = self.bits()
        n = self.m.count()
        total = n * len(bits) // 8
        mb = self.max_bytes.value()
        self.explain.setText(
            f"From {self.m.describe()}: emit bit{'s' if len(bits) != 1 else ''} {', '.join(map(str, bits)) or '–'} "
            f"of each sample, pack {self.pack.currentData().upper()}-first"
            f"{', inverted' if self.invert.isChecked() else ''}. ≈ {total:,} bytes"
            f"{f' (limited to {mb:,})' if mb and mb < total else ''}.")
        if self.combos:
            stale = self._combo_key() != self.combo_key
            self.combo_info.setText(self.combo_text + (
                f' <span style="color:{T["warn"]}">The sample range or bits changed since — '
                f'press Try combinations again.</span>' if stale else ""))

    # ---------------------------------------------------------------- run
    def options(self) -> ExtractOptions:
        return ExtractOptions.from_dict({"bits": self.bits(), "pack": self.pack.currentData(),
                                         "invert": self.invert.isChecked(),
                                         "max_bytes": self.max_bytes.value() or None}, self.audio.nbits)

    def _shortcut(self):
        """Ctrl+Enter: the single extraction once its settings are open, else the combinations."""
        (self.run if self.settings.isVisibleTo(self) else self.run_combinations)()

    def run(self):
        if not self.audio:
            return
        try:
            opts = self.options()
        except ValueError as e:
            self.summary.setText(badge(str(e), "bad"))
            return
        self.go.setEnabled(False)
        self.go.setText("Extracting…")
        self.runner.submit("extract", _run, self.audio, self.m.sel, opts, on_ok=self._got, on_err=self._err)

    def _err(self, e):
        self.go.setEnabled(True)
        self.go.setText("Extract")
        self.summary.setText(badge(f"{type(e).__name__}: {e}", "bad"))

    def _got(self, r):
        self.go.setEnabled(True)
        self.go.setText("Extract")
        self.result = r
        self.views.show()
        self.save.setEnabled(bool(r["data"]))
        a, data = r["analysis"], r["data"]
        parts = [badge(f"{r['total_bits']:,} bits → {len(data):,} bytes")]
        parts += [badge(f"⚑ {f}", "good") for f in a["flags"]]
        parts += [badge(f"file: {m}", "good") for m in a["magic"]]
        parts += [badge(f"{m['type']} @ 0x{m['offset']:x}", "warn") for m in a["magic_later"][:5]]
        parts.append(badge(f"printable {a['printable_ratio'] * 100:.0f}%", "good" if a["printable_ratio"] > 0.9 else ""))
        parts.append(badge(f"entropy {a['entropy']:.2f} bits/byte"))
        parts += [badge(f"length prefix? {l['endian']} {l['value']:,}") for l in a["length_prefix"]]
        self.summary.setText("".join(parts))
        self.hex_v.setPlainText(r["hex"] + (f"\n… preview limited to {HEX_PREVIEW:,} bytes; save for the rest"
                                            if len(data) > HEX_PREVIEW else ""))
        txt = data[:256 * 1024].decode("latin-1")
        self.text_v.setPlainText("".join(c if (32 <= ord(c) < 127 or c in "\n\t") else "·" for c in txt if c != "\r"))
        bits = "".join("1" if b else "0" for b in r["bits"])
        groups = [bits[i:i + 8] for i in range(0, len(bits), 8)]
        self.bits_v.setPlainText("\n".join(" ".join(groups[i:i + 8]) for i in range(0, len(groups), 8)) +
                                 (f"\n… first {len(bits):,} of {r['total_bits']:,} bits" if r["total_bits"] > len(bits) else ""))
        self.str_v.setPlainText("\n".join(a["strings"]) or "(no printable runs ≥ 6 chars)")
        if a["flags"]:
            self.status.emit(f"flag found: {a['flags'][0]}")

    # ---------------------------------------------------------------- combinations
    def _source_name(self, src):
        return "interleaved" if src == "interleaved" else channel_name(int(src[2:]), self.audio.channels)

    def _combo_key(self):
        """What a combination run depends on (everything except source, bit order and packing)."""
        s = self.m.sel
        return (s.start, s.end, s.step, s.take, s.reverse, frozenset(self.bits()),
                self.invert.isChecked(), self.max_bytes.value())

    def _clear_combos(self):
        self.combos, self.combo_key, self._picked = [], None, None
        self.combo_table.blockSignals(True)
        self.combo_table.setRowCount(0)
        self.combo_table.blockSignals(False)
        self.combo_table.hide()
        self.combo_info.hide()

    def run_combinations(self):
        if not self.audio:
            return
        try:
            opts = self.options()
        except ValueError as e:
            self.summary.setText(badge(str(e), "bad"))
            return
        self.brute.setEnabled(False)
        self.brute.setText("Trying…")
        self._pending_key = self._combo_key()
        self.runner.submit("combos", try_combinations, self.audio, self.m.sel, opts,
                           on_ok=self._got_combos, on_err=self._combos_err)

    def _combos_err(self, e):
        self.brute.setEnabled(True)
        self.brute.setText("Try combinations")
        self.summary.setText(badge(f"{type(e).__name__}: {e}", "bad"))

    def _got_combos(self, rows):
        self.brute.setEnabled(True)
        self.brute.setText("Try combinations")
        self._clear_combos()
        self.combos, self.combo_key = rows, self._pending_key
        t = self.combo_table
        t.blockSignals(True)
        t.setRowCount(len(rows))
        hits = 0
        for i, r in enumerate(rows):
            found = [f"⚑ {f}" for f in r["flags"][:3]] + [f"file: {m}" for m in r["magic"]]
            found += [f"{m['type']} @ 0x{m['offset']:x}" for m in r["magic_later"][:2]]
            hit = bool(r["flags"] or r["magic"])
            hits += hit
            cells = [self._source_name(r["source"]), ",".join(map(str, r["bits"])), r["pack"].upper() + " first",
                     "  ".join(found), f"{r['printable'] * 100:.0f}%", _printable(r["head"]), r["head"].hex(" ")]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if hit:
                    it.setForeground(QColor(T["good"]))
                elif c == 3 and found:
                    it.setForeground(QColor(T["warn"]))
                t.setItem(i, c, it)
        head = len(rows[0]["head"]) if rows else 0
        s = self.m.sel
        rng = f"from sample {s.start}" + (f" to {s.end}" if s.end is not None else "")
        pick = "" if s.step == 1 else f", {s.take} of every {s.step}"
        t.setCurrentCell(-1, -1)
        t.blockSignals(False)
        t.show()
        self.combo_text = (
            f"{len(rows)} combinations of source × bit order × packing, {rng}{pick}"
            f"{', reversed' if s.reverse else ''}{', inverted' if self.invert.isChecked() else ''}. "
            f"First {head} bytes shown; flags and file signatures are searched in the first "
            f"{max((r['bytes'] for r in rows), default=0):,} bytes. Best matches first. "
            "Click a row to open its settings and run that extraction in full.")
        self.combo_info.setTextFormat(Qt.RichText)
        self.combo_info.show()
        self._explain()
        self.summary.setText(badge(f"{len(rows)} combinations tried") +
                             badge(f"{hits} with a flag or file signature" if hits else "no flag or file signature",
                                   "good" if hits else ""))
        best = next((r for r in rows if r["flags"]), None)
        if best:
            self.status.emit(f"flag found: {best['flags'][0]}")

    def _pick(self, i):
        # a click fires both cellPressed and currentCellChanged: apply once, after the event
        if i >= 0 and self._picked is None:
            QTimer.singleShot(0, self._apply_picked)
        self._picked = i if i >= 0 else self._picked

    def _apply_picked(self):
        i, self._picked = self._picked, None
        if i is not None:
            self.apply_combination(i)

    def apply_combination(self, i):
        """Open the settings with combination row `i` loaded and run the full extraction."""
        if not 0 <= i < len(self.combos):
            return
        r = self.combos[i]
        bits = r["bits"]
        mode = "desc" if bits == sorted(bits, reverse=True) else "asc" if bits == sorted(bits) else "custom"
        for w, v in ((self.order, mode), (self.pack, r["pack"])):
            w.blockSignals(True)
            w.setCurrentIndex(w.findData(v))
            w.blockSignals(False)
        self.bits_edit.setText(",".join(map(str, bits)))
        self._text_changed()
        self.m.update(source=r["source"])
        self.manual.setChecked(True)
        self.run()

    def _save(self):
        if not self.result:
            return
        s, o = self.m.sel, self.options()
        base = self.audio.name.rsplit(".", 1)[0]
        name = f"{base}_{s.source}_s{s.start}_st{s.step}t{s.take}_b{''.join(map(str, o.bits))}_{o.pack}.bin"
        path, _ = QFileDialog.getSaveFileName(self, "Save extracted bytes", name)
        if path:
            with open(path, "wb") as f:
                f.write(self.result["data"])
            self.status.emit(f"saved {len(self.result['data']):,} bytes to {path}")
