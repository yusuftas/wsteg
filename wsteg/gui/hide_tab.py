# SPDX-License-Identifier: GPL-3.0-or-later
"""Hide tab: write a file or text into chosen sample bits and save the result as a new file."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..embed import hide
from ..extract import ExtractOptions
from .bitpicker import BitPicker
from .common import badge, hint, labeled, panel, row, seg_buttons
from .selection import SelectionBar


def _run(audio, sel, opts, payload, path):
    """`payload` is bytes, or the path of the file to hide."""
    if isinstance(payload, str):
        with open(payload, "rb") as f:
            payload = f.read()
    data, stats = hide(audio, sel, opts, payload)
    with open(path, "wb") as f:
        f.write(data)
    return {**stats, "path": path, "sel": sel, "opts": opts}


def _size(n: int) -> str:
    return f"{n:,} byte{'s' if n != 1 else ''}"


class HideTab(QWidget):
    status = Signal(str)
    saved = Signal(str, bool)  # output path, open it afterwards

    def __init__(self, runner, sel_model, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.m = sel_model
        self.audio = None
        self.mode = "file"
        self.payload_path = ""
        self.last = None
        self.picker = BitPicker(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(SelectionBar(sel_model))

        # --- payload
        p, pl = panel("Data to hide", "a file of any type, or a short text such as a flag")
        switch, self.mode_btns = seg_buttons([("file", "File"), ("text", "Text")], self.mode, self._set_mode)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("no file chosen")
        self.browse = QPushButton("Choose file…")
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("text to hide, stored as UTF-8")
        self.text_edit.hide()
        self.payload_info = hint("", wrap=False)
        r = row(switch, self.path_edit, self.browse, self.text_edit, self.payload_info, stretch=False)
        r.setStretchFactor(self.path_edit, 1)
        r.setStretchFactor(self.text_edit, 1)
        pl.addLayout(r)
        lay.addWidget(p)

        # --- bits
        p, pl = panel("Bits to overwrite in each selected sample",
                      "bit 0 = LSB · click to toggle · low bits are inaudible, high bits are not")
        pl.addLayout(self.picker.grid)
        self.pack = QComboBox()
        self.pack.addItem("MSB first", "msb")
        self.pack.addItem("LSB first", "lsb")
        self.pack.setToolTip("MSB first: bit 7 of each payload byte is written first")
        self.invert = QCheckBox("Invert bits")
        self.invert.setToolTip("Store every payload bit flipped")
        pl.addLayout(row(labeled("Order", self.picker.order), labeled("Bit list", self.picker.edit),
                         labeled("Pack bytes", self.pack), self.invert))
        lay.addWidget(p)

        # --- capacity + action
        p, pl = panel("Capacity")
        self.capacity = QLabel()
        self.capacity.setTextFormat(Qt.RichText)
        self.capacity.setWordWrap(True)
        pl.addWidget(self.capacity)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        pl.addWidget(self.bar)
        self.go = QPushButton("Hide and save as…")
        self.go.setObjectName("primary")
        self.go.setToolTip("Write the data into a copy of this file. The loaded file is never modified.")
        self.open_after = QCheckBox("Open the result afterwards")
        self.open_after.setChecked(True)
        pl.addLayout(row(self.go, self.open_after))
        lay.addWidget(p)

        p, pl = panel("Result")
        self.result = QLabel("Nothing hidden yet.")
        self.result.setTextFormat(Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl.addWidget(self.result)
        lay.addWidget(p)
        lay.addStretch(1)

        self.browse.clicked.connect(self._browse)
        self.text_edit.textChanged.connect(self._update)
        self.picker.changed.connect(self._update)
        self.m.changed.connect(self._update)
        self.go.clicked.connect(self.run)

    # ---------------------------------------------------------------- payload
    def _set_mode(self, mode):
        self.mode = mode
        self.mode_btns[mode].setChecked(True)
        for w in (self.path_edit, self.browse):
            w.setVisible(mode == "file")
        self.text_edit.setVisible(mode == "text")
        self._update()

    def _browse(self):
        start = os.path.dirname(self.payload_path) if self.payload_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Choose the file to hide", start)
        if path:
            self.set_payload_file(path)

    def set_payload_file(self, path):
        self.payload_path = path
        self.path_edit.setText(path)
        self._set_mode("file")

    def set_payload_text(self, text):
        self.text_edit.setText(text)
        self._set_mode("text")

    def payload(self):
        """bytes for text, the path for a file, None when nothing is chosen."""
        if self.mode == "text":
            return self.text_edit.text().encode("utf-8") or None
        return self.payload_path or None

    def payload_size(self) -> int | None:
        p = self.payload()
        if p is None:
            return None
        if isinstance(p, bytes):
            return len(p)
        try:
            return os.path.getsize(p)
        except OSError:
            return None

    # ---------------------------------------------------------------- capacity
    def set_audio(self, audio):
        self.audio = audio
        self.picker.set_nbits(audio.nbits)
        self._update()

    def fits(self) -> bool:
        size = self.payload_size()
        return bool(self.audio and size and self.picker.valid()
                    and len(set(self.picker.bits())) == len(self.picker.bits())
                    and size * 8 <= self.m.count() * len(self.picker.bits()))

    def _update(self, *_):
        if not self.audio:
            return
        bits, size = self.picker.bits(), self.payload_size()
        samples = self.m.count()
        cap = samples * len(bits)
        self.payload_info.setText("" if size is None else f"{_size(size)} = {size * 8:,} bits")
        holds = f"{samples:,} selected samples × {len(bits)} bit{'s' if len(bits) != 1 else ''} hold " \
                f"{cap:,} bits = {_size(cap // 8)}"
        used = 0.0
        if not self.picker.valid() or len(set(bits)) != len(bits):
            why = ("choose at least one bit" if not bits else "each bit can be listed only once"
                   if self.picker.valid() else f"this file has bits 0–{self.audio.nbits - 1}")
            text = badge("Bit list problem", "bad") + why + "."
        elif self.mode == "file" and self.payload_path and size is None:
            text = badge("Cannot read the file", "bad") + self.payload_path
        elif not size:
            text = badge("No data yet") + f"Choose a file or type a text. {holds}."
        else:
            need = size * 8
            used = need / cap if cap else 2.0
            if need <= cap:
                text = (badge("Fits", "good") + f"The data needs {need:,} bits, which fills {-(-need // len(bits)):,} "
                        f"samples ({used * 100:.2f}% of the room). {holds}.")
            else:
                text = (badge("Does not fit", "bad") + f"The data needs {need:,} bits but {holds}, "
                        f"{_size(-(-(need - cap) // 8))} short. Use more bits per sample, a wider sample range, "
                        "or all channels.")
        self.capacity.setText(text)
        self.bar.setValue(int(min(used, 1.0) * 1000))
        self.go.setEnabled(self.fits())

    # ---------------------------------------------------------------- run
    def options(self) -> ExtractOptions:
        return ExtractOptions.from_dict({"bits": self.picker.bits(), "pack": self.pack.currentData(),
                                         "invert": self.invert.isChecked()}, self.audio.nbits)

    def run(self):
        if not self.fits():
            return
        base = os.path.splitext(self.audio.name)[0] + "_stego.wav"
        start = os.path.join(os.path.dirname(self.audio.path), base) if self.audio.path else base
        path, _ = QFileDialog.getSaveFileName(self, "Save the file with hidden data", start, "WAV (*.wav)")
        if path:
            self.hide_to(path)

    def hide_to(self, path):
        if not self.fits():
            return
        self.go.setEnabled(False)
        self.go.setText("Hiding…")
        self.runner.submit("hide", _run, self.audio, self.m.sel, self.options(), self.payload(), path,
                           on_ok=self._done, on_err=self._err)

    def _reset_button(self):
        self.go.setText("Hide and save as…")
        self.go.setEnabled(self.fits())

    def _err(self, e):
        self._reset_button()
        self.result.setText(badge(f"{type(e).__name__}: {e}", "bad"))

    def _done(self, r):
        self._reset_button()
        self.last = r
        s, o = r["sel"], r["opts"]
        cmd = f'wsteg extract "{os.path.basename(r["path"])}" --source {s.source}'
        cmd += f" --start {s.start}" if s.start else ""
        cmd += f" --end {s.end}" if s.end is not None else ""
        cmd += f" --step {s.step}" if s.step > 1 else ""
        cmd += f" --take {s.take}" if s.take > 1 else ""
        cmd += " --reverse" if s.reverse else ""
        cmd += f" --bits {','.join(map(str, o.bits))} --pack {o.pack}{' --invert' if o.invert else ''}"
        cmd += f" --max-bytes {r['payload_bytes']}"
        self.result.setText(
            badge(f"{_size(r['payload_bytes'])} hidden", "good") + badge(f"{r['samples_changed']:,} of "
            f"{r['samples_used']:,} used samples changed") + badge("read back and verified", "good") +
            f"<br>Saved to {r['path']}<br>To get it back: Extract with the same sample selection, bits "
            f"{', '.join(map(str, o.bits))}, {o.pack.upper()} first{', inverted' if o.invert else ''}; the data is the "
            f"first {_size(r['payload_bytes'])}. From the command line:<br><code>{cmd}</code>")
        self.status.emit(f"hid {_size(r['payload_bytes'])} in {r['path']}")
        self.saved.emit(r["path"], self.open_after.isChecked())
