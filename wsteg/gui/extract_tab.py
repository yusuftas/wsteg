# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction tab: choose samples + bits, try every combination, then refine, analyze and save one."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..extract import ExtractOptions, analyze_payload, count_combinations, extract, try_combinations
from .bitpicker import BitPicker
from .common import T, badge, code_view, hexdump, hint, labeled, mono_font, panel, row
from .selection import SelectionBar, channel_name

HEX_PREVIEW = 64 * 1024
COMBO_COLS = ["Source", "Bits", "Pack", "Findings", "Printable", "Text", "Hex"]
COMBO_KEEP = 300  # best rows listed after a run
COMBO_WARN = 100_000  # ask before trying more than this many
COMBO_MAX = 3_000_000  # refuse above this
COMBO_RATE = 10_000  # rough combinations per second, for the time estimate
START_HINT = ("Pick the candidate bits above and press <b>Try combinations</b> (Ctrl+Enter) to preview every source, "
              "selection of those bits, bit order and packing. Click a result to open its settings and the full output.")


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
        self.picker = BitPicker(self)
        self.order, self.bits_edit = self.picker.order, self.picker.edit

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(SelectionBar(sel_model))

        p, pl = panel("Bits to extract from each selected sample", "bit 0 = LSB · click to toggle")
        pl.addLayout(self.picker.grid)
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
        self.brute.setToolTip("Keep the sample range and preview the start of the output for every combination of\n"
                              "channel / interleaved × selection of the chosen bits × bit order × MSB/LSB packing.\n"
                              "Choose several bits to test each of them alone and together.")
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
        self._applied = None
        lay.addWidget(p, 1)

        self.picker.changed.connect(self._bits_changed)
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
    @property
    def chips(self):
        return self.picker.chips

    def bits(self) -> list[int]:
        return self.picker.bits()

    def _text_changed(self):
        self.picker.text_changed()

    def set_audio(self, audio):
        self.audio = audio
        self.result = None
        self.save.setEnabled(False)
        self.summary.setText(START_HINT)
        self.views.hide()
        self._clear_combos()
        self.picker.set_nbits(audio.nbits)

    def _bits_changed(self, bits):
        if self.picker.valid() or not bits:
            self.bitsChanged.emit(bits)
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
            # picking a row narrows the chips to that row's bits; that alone doesn't outdate the table
            keys = {self._combo_key()}
            if self._applied == frozenset(bits):
                keys.add(self._combo_key(self.combo_key[5]))
            stale = self.combo_key not in keys
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

    def max_subset(self) -> int | None:
        """Every selection of the chosen bits is tried, from one bit up to all of them."""
        return len(set(self.bits())) or None

    def combination_count(self) -> int:
        return count_combinations(self.audio.channels, self.bits(), self.max_subset()) if self.audio else 0

    @staticmethod
    def _eta(n):
        sec = n / COMBO_RATE
        return f"{sec:.0f} seconds" if sec < 90 else f"{sec / 60:.0f} minutes"

    def _combo_key(self, bits=None):
        """What a combination run depends on (everything except source, bit order and packing)."""
        s = self.m.sel
        return (s.start, s.end, s.step, s.take, s.reverse, frozenset(self.bits() if bits is None else bits),
                self.invert.isChecked(), self.max_bytes.value())

    def _clear_combos(self):
        self.combos, self.combo_key, self._picked, self._applied = [], None, None, None
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
        n = self.combination_count()
        if n > COMBO_MAX:
            self.summary.setText(badge(f"{len(self.bits())} chosen bits give {n:,} combinations, which is too many "
                                       f"(limit {COMBO_MAX:,}). Choose fewer bits.", "bad"))
            return
        if n > COMBO_WARN and not self.confirm_big(n):
            return
        self.brute.setEnabled(False)
        self.brute.setText("Trying…")
        self._pending_key, self._pending_total = self._combo_key(), n
        self.runner.submit("combos", try_combinations, self.audio, self.m.sel, opts, max_bits=self.max_subset(),
                           keep=COMBO_KEEP, on_ok=self._got_combos, on_err=self._combos_err)

    def confirm_big(self, n) -> bool:
        return QMessageBox.question(
            self, "Try combinations",
            f"Every selection of the {len(self.bits())} chosen bits gives {n:,} combinations.\n"
            f"That takes about {self._eta(n)} and cannot be cancelled.\n\nTry them all?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

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
        total = self._pending_total
        self.combo_text = (
            f"{total:,} combinations of source × {'bit selection × ' if len(self.combo_key[5]) > 1 else ''}bit order × "
            f"packing{f' (the best {len(rows)} are listed)' if total > len(rows) else ''}, {rng}{pick}"
            f"{', reversed' if s.reverse else ''}{', inverted' if self.invert.isChecked() else ''}. "
            f"First {head} bytes shown; flags and file signatures are searched in the first "
            f"{max((r['bytes'] for r in rows), default=0):,} bytes. Best matches first. "
            "Click a row to open its settings and run that extraction in full.")
        self.combo_info.setTextFormat(Qt.RichText)
        self.combo_info.show()
        self._explain()
        self.summary.setText(badge(f"{total:,} combinations tried") +
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
        self.pack.blockSignals(True)
        self.pack.setCurrentIndex(self.pack.findData(r["pack"]))
        self.pack.blockSignals(False)
        self._applied = frozenset(r["bits"])
        self.picker.set_bits(r["bits"])
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
