"""Extraction tab: choose samples + bits, pack, analyze and save the payload."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..extract import ExtractOptions, analyze_payload, extract
from .common import T, badge, code_view, hexdump, hint, labeled, panel, row
from .selection import SelectionBar

HEX_PREVIEW = 64 * 1024


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
        self.go.setObjectName("primary")
        self.go.setShortcut("Ctrl+Return")
        self.save = QPushButton("Save .bin…")
        self.save.setEnabled(False)
        pl.addLayout(row(labeled("Order", self.order), labeled("Bit list", self.bits_edit),
                         labeled("Pack bytes", self.pack), self.invert, labeled("Max bytes", self.max_bytes),
                         self.go, self.save))
        self.explain = hint("")
        pl.addWidget(self.explain)
        lay.addWidget(p)

        p, pl = panel("Result")
        self.summary = QLabel("Run an extraction (Ctrl+Enter).")
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl.addWidget(self.summary)
        self.views = QTabWidget()
        self.hex_v, self.text_v, self.bits_v, self.str_v = code_view(), code_view(True), code_view(True), code_view()
        for w, n in ((self.hex_v, "Hex"), (self.text_v, "Text"), (self.bits_v, "Bits"), (self.str_v, "Strings")):
            self.views.addTab(w, n)
        pl.addWidget(self.views, 1)
        lay.addWidget(p, 1)

        self.order.currentIndexChanged.connect(self._chips_to_text)
        self.bits_edit.textEdited.connect(self._text_changed)
        self.pack.currentIndexChanged.connect(self._explain)
        self.invert.toggled.connect(self._explain)
        self.max_bytes.valueChanged.connect(self._explain)
        self.m.changed.connect(self._explain)
        self.go.clicked.connect(self.run)
        self.save.clicked.connect(self._save)

    # ---------------------------------------------------------------- bits
    def bits(self) -> list[int]:
        return [int(x) for x in re.split(r"[\s,]+", self.bits_edit.text().strip()) if x.isdigit()]

    def set_audio(self, audio):
        self.audio = audio
        self.result = None
        self.save.setEnabled(False)
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

    # ---------------------------------------------------------------- run
    def options(self) -> ExtractOptions:
        return ExtractOptions.from_dict({"bits": self.bits(), "pack": self.pack.currentData(),
                                         "invert": self.invert.isChecked(),
                                         "max_bytes": self.max_bytes.value() or None}, self.audio.nbits)

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
