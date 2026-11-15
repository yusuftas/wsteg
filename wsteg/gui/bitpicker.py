# SPDX-License-Identifier: GPL-3.0-or-later
"""Bit chips + order + bit list: the widgets that choose which sample bits to use, in which order."""
from __future__ import annotations

import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QLineEdit, QPushButton

from .common import T

PER_ROW = 16


class BitPicker(QObject):
    """Owns three pieces a tab places where it likes: `grid` (toggle chips, MSB first),
    `order` (high→low / low→high / custom) and `edit` (the bit list, in emit order)."""
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nbits = 0
        self.chips = {}
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(4)
        self.order = QComboBox()
        self.order.addItem("high → low (b1,b0)", "desc")
        self.order.addItem("low → high (b0,b1)", "asc")
        self.order.addItem("custom (edit the list)", "custom")
        self.edit = QLineEdit("0")
        self.edit.setToolTip("Comma-separated bit indices, used in exactly this order")
        self.edit.setMaximumWidth(180)
        self.order.currentIndexChanged.connect(self._chips_to_text)
        self.edit.textEdited.connect(self.text_changed)

    def bits(self) -> list[int]:
        return [int(x) for x in re.split(r"[\s,]+", self.edit.text().strip()) if x.isdigit()]

    def valid(self) -> bool:
        b = self.bits()
        return bool(b) and all(x < self.nbits for x in b)

    def set_nbits(self, n: int):
        """Rebuild the chips for an `n`-bit sample container, keeping the bits that still exist."""
        for b in self.chips.values():
            b.deleteLater()
        self.chips = {}
        self.nbits = n
        for i, b in enumerate(range(n - 1, -1, -1)):
            btn = QPushButton(f"b{b}")
            btn.setCheckable(True)
            btn.setFixedWidth(46)
            btn.setToolTip(f"bit {b}" + (" (LSB)" if b == 0 else " (MSB)" if b == n - 1 else ""))
            btn.toggled.connect(self._chips_to_text)
            r, c = divmod(i, PER_ROW)
            self.grid.addWidget(btn, r, c + (c // 8))  # gap column every 8 bits
            self.chips[b] = btn
        for c in range(PER_ROW + PER_ROW // 8):
            self.grid.setColumnStretch(c, 0)
        self.grid.setColumnStretch(PER_ROW + PER_ROW // 8, 1)
        self.edit.setText(",".join(map(str, [b for b in self.bits() if b < n] or [0])))
        self.text_changed()

    def set_bits(self, bits: list[int]):
        """Load an exact bit list and pick the order mode that describes it."""
        mode = "desc" if bits == sorted(bits, reverse=True) else "asc" if bits == sorted(bits) else "custom"
        self.order.blockSignals(True)
        self.order.setCurrentIndex(self.order.findData(mode))
        self.order.blockSignals(False)
        self.edit.setText(",".join(map(str, bits)))
        self.text_changed()

    def _chips_to_text(self):
        on = [b for b, btn in self.chips.items() if btn.isChecked()]
        mode = self.order.currentData()
        if mode == "custom":
            cur = [b for b in self.bits() if b in on]
            cur += [b for b in on if b not in cur]
        else:
            cur = sorted(on, reverse=mode == "desc")
        self.edit.setText(",".join(map(str, cur)))
        self.edit.setStyleSheet("")
        self.changed.emit(self.bits())

    def text_changed(self):
        """The bit list was edited: sync the chips (unless a bit doesn't exist)."""
        if any(b >= self.nbits for b in self.bits()):
            self.edit.setStyleSheet(f"color: {T['bad']}")
            self.changed.emit(self.bits())
            return
        self.edit.setStyleSheet("")
        on = set(self.bits())
        for b, btn in self.chips.items():
            btn.blockSignals(True)
            btn.setChecked(b in on)
            btn.blockSignals(False)
        self.changed.emit(self.bits())
