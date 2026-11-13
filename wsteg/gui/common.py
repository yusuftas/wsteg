"""Shared GUI helpers: theme, colormaps, background workers, small widgets."""
from __future__ import annotations

import itertools
import traceback

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

# ------------------------------------------------------------------ theme

DARK = {
    "bg": "#0f1115", "surface": "#171a21", "surface2": "#1e222b", "border": "#2a2f3a",
    "text": "#e6e8ec", "text2": "#a9afbb", "muted": "#6f7684", "accent": "#4ea1ff",
    "warn": "#f0b429", "bad": "#ef5a5a", "good": "#3ecf8e",
    "bit1": "#e8eaed", "bit0": "#262a33",
    "heat_lo": "#3b7dd8", "heat_mid": "#3a3f4a", "heat_hi": "#e8833a",
}
LIGHT = {
    "bg": "#f5f6f8", "surface": "#ffffff", "surface2": "#eef0f3", "border": "#d8dce3",
    "text": "#1b1f27", "text2": "#4a5160", "muted": "#7c8392", "accent": "#1f6fd1",
    "warn": "#b7791f", "bad": "#c53030", "good": "#2f855a",
    "bit1": "#1b1f27", "bit0": "#e4e7ec",
    "heat_lo": "#2f6cc4", "heat_mid": "#d9dce2", "heat_hi": "#d26a1e",
}
T = dict(DARK)  # active theme tokens


def apply_theme(app: QApplication, name: str = "dark") -> None:
    T.clear()
    T.update(LIGHT if name == "light" else DARK)
    app.setStyle("Fusion")
    p = QPalette()
    c = lambda k: QColor(T[k])  # noqa: E731
    p.setColor(QPalette.Window, c("bg"))
    p.setColor(QPalette.WindowText, c("text"))
    p.setColor(QPalette.Base, c("surface"))
    p.setColor(QPalette.AlternateBase, c("surface2"))
    p.setColor(QPalette.Text, c("text"))
    p.setColor(QPalette.Button, c("surface2"))
    p.setColor(QPalette.ButtonText, c("text"))
    p.setColor(QPalette.ToolTipBase, c("surface2"))
    p.setColor(QPalette.ToolTipText, c("text"))
    p.setColor(QPalette.Highlight, c("accent"))
    p.setColor(QPalette.HighlightedText, QColor("#06121f" if name != "light" else "#ffffff"))
    p.setColor(QPalette.PlaceholderText, c("muted"))
    p.setColor(QPalette.Link, c("accent"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c("muted"))
    app.setPalette(p)
    app.setStyleSheet(f"""
        QToolTip {{ background: {T['surface2']}; color: {T['text']}; border: 1px solid {T['border']};
                    font-family: monospace; padding: 4px; }}
        QGroupBox {{ border: 1px solid {T['border']}; border-radius: 6px; margin-top: 14px; padding: 8px 8px 6px 8px;
                     background: {T['surface']}; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QTabWidget::pane {{ border: 0; }}
        QTabBar::tab {{ padding: 7px 16px; background: transparent; color: {T['text2']}; border: 0;
                        border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ color: {T['text']}; border-bottom: 2px solid {T['accent']}; }}
        QPushButton:checked {{ background: {T['accent']}; color: {'#06121f' if name != 'light' else '#fff'}; }}
        QPushButton#primary {{ background: {T['accent']}; color: {'#06121f' if name != 'light' else '#fff'};
                               font-weight: 600; padding: 4px 14px; }}
        QLabel#hint {{ color: {T['muted']}; }}
        QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {T['muted']}; border-radius: 3px;
                                background: {T['surface2']}; }}
        QCheckBox::indicator:checked {{ background: {T['accent']}; border-color: {T['accent']}; }}
        QToolBar QToolButton {{ padding: 3px 8px; }}
        QStatusBar {{ color: {T['text2']}; }}
    """)
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True,
                        background=T["surface"], foreground=T["text2"])


def mono_font(size: int = 9) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(size)
    return f


# ------------------------------------------------------------------ colormaps

def _stops(hexes):
    return pg.ColorMap(np.linspace(0, 1, len(hexes)), [QColor(h) for h in hexes])


def colormap(name: str) -> pg.ColorMap:
    if name == "gray":
        return _stops(["#000000", "#ffffff"])
    if name == "jet":
        return _stops(["#00007f", "#0000ff", "#007fff", "#00ffff", "#7fff7f", "#ffff00", "#ff7f00", "#ff0000", "#7f0000"])
    return pg.colormap.get(name)


SPEC_CMAPS = ["magma", "inferno", "viridis", "plasma", "turbo", "gray", "jet"]


def diverging_cmap() -> pg.ColorMap:
    return _stops([T["heat_lo"], T["heat_mid"], T["heat_hi"]])


def bits_cmap() -> pg.ColorMap:
    return _stops([T["bit0"], T["bit1"]])


# ------------------------------------------------------------------ workers

class _Signals(QObject):
    done = Signal(int, bool, object)


class _Task(QRunnable):
    def __init__(self, token, fn, args, kwargs, sig):
        super().__init__()
        self.token, self.fn, self.args, self.kwargs, self.sig = token, fn, args, kwargs, sig

    def run(self):
        try:
            res, ok = self.fn(*self.args, **self.kwargs), True
        except Exception as e:  # noqa: BLE001 - reported to the UI
            e.trace = traceback.format_exc()
            res, ok = e, False
        self.sig.done.emit(self.token, ok, res)


class Runner(QObject):
    """Run functions on the global thread pool; for each `key` only the most
    recent submission's result is delivered (older ones are dropped)."""
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sig = _Signals()
        self._sig.done.connect(self._done)
        self._ids = itertools.count(1)
        self._latest: dict[str, int] = {}
        self._cbs: dict[int, tuple] = {}

    def submit(self, key, fn, *args, on_ok=None, on_err=None, **kwargs):
        token = next(self._ids)
        self._latest[key] = token
        self._cbs[token] = (key, on_ok, on_err)
        QThreadPool.globalInstance().start(_Task(token, fn, args, kwargs, self._sig))
        return token

    def _done(self, token, ok, res):
        key, on_ok, on_err = self._cbs.pop(token, (None, None, None))
        if key is None or self._latest.get(key) != token:
            return
        if ok:
            if on_ok:
                on_ok(res)
        elif on_err:
            on_err(res)
        else:
            self.error.emit(f"{type(res).__name__}: {res}")


# ------------------------------------------------------------------ small widgets

def hint(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(wrap)
    return lab


def row(*widgets, stretch=True, spacing=8) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, (QHBoxLayout, QVBoxLayout)):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    if stretch:
        lay.addStretch(1)
    return lay


def labeled(text: str, w: QWidget) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(4)
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {T['text2']}")
    lay.addWidget(lab)
    lay.addWidget(w)
    return lay


def code_view(wrap=False) -> QPlainTextEdit:
    v = QPlainTextEdit()
    v.setReadOnly(True)
    v.setFont(mono_font(10))
    v.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap else QPlainTextEdit.NoWrap)
    return v


def badge(text: str, kind: str = "") -> str:
    col = {"warn": T["warn"], "good": T["good"], "bad": T["bad"]}.get(kind, T["text2"])
    return (f'<span style="background:{T["surface2"]}; color:{col}; padding:2px 6px;">'
            f'&nbsp;{text}&nbsp;</span>&nbsp;')


def seg_buttons(options, current=None, on_change=None) -> tuple[QWidget, dict]:
    """Segmented exclusive buttons. options: list of (value, label)."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    btns = {}
    for v, label in options:
        b = QPushButton(label)
        b.setCheckable(True)
        b.setChecked(v == current)
        b.setAutoExclusive(True)
        if on_change:
            b.clicked.connect(lambda _=False, v=v: on_change(v))
        lay.addWidget(b)
        btns[v] = b
    return w, btns


def show_tip(text: str, widget=None):
    from PySide6.QtGui import QCursor
    QToolTip.showText(QCursor.pos(), text, widget)


def panel(title: str, *hints: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setStyleSheet(f"QFrame#panel {{ background: {T['surface']}; border: 1px solid {T['border']}; border-radius: 8px; }}")
    f.setObjectName("panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 12)
    head = QHBoxLayout()
    t = QLabel(f"<b>{title}</b>")
    head.addWidget(t)
    for h in hints:
        head.addWidget(hint(h), 1)
    if not hints:
        head.addStretch(1)
    lay.addLayout(head)
    return f, lay


def hexdump(data: bytes, base: int = 0) -> str:
    out = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        h = " ".join(f"{b:02x}" for b in chunk[:8])
        h2 = " ".join(f"{b:02x}" for b in chunk[8:])
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{base + i:08x}  {h:<23}  {h2:<23}  |{asc}|")
    return "\n".join(out)


def fmt_time(t: float) -> str:
    if t >= 60:
        return f"{int(t // 60)}:{t % 60:06.3f}"
    return f"{t:.3f}s"


def fmt_hz(f: float) -> str:
    return f"{f / 1000:.2f} kHz" if f >= 1000 else f"{f:.0f} Hz"


class SilentButton(QPushButton):
    """QPushButton that doesn't steal keyboard focus."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.NoFocus)
