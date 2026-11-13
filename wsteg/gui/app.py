"""Main window and application entry point."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStyle,
    QTabWidget,
    QToolBar,
)

from .. import __version__
from ..wavio import load_file
from .bitplanes import BitPlanesTab
from .common import Runner, apply_theme, fmt_time
from .extract_tab import ExtractTab
from .overview import OverviewTab
from .selection import SelectionModel
from .spectrogram import SpectrogramTab

FILE_FILTER = "Audio (*.wav *.wave *.flac *.mp3 *.ogg *.opus *.m4a *.aif *.aiff);;All files (*)"

# Qt >= 6.10 prefers its native PipeWire backend on Linux, which segfaults when no
# PipeWire client config exists (e.g. WSL). The PulseAudio API is available on both
# PulseAudio and PipeWire (pipewire-pulse) desktops, so default to it.
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_AUDIO_BACKEND", "pulseaudio")

QMediaPlayer = None
if not os.environ.get("WSTEG_NO_AUDIO"):
    try:  # playback is optional (e.g. missing system audio libs)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except Exception:  # noqa: BLE001
        QMediaPlayer = None


def _load(path):
    audio = load_file(path)
    info = audio.info()  # precomputes float channels / stats off the UI thread
    return audio, info


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("wsteg — audio stego workbench")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("wsteg", "wsteg")
        self.runner = Runner(self)
        self.runner.error.connect(self.error)
        self.files: list[tuple] = []  # (audio, info)
        self.audio = None

        self.sel = SelectionModel()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.overview = OverviewTab()
        self.spec = SpectrogramTab(self.runner)
        self.bitplanes = BitPlanesTab(self.runner, self.sel)
        self.extract = ExtractTab(self.runner, self.sel)
        self.tabs.addTab(self.overview, "Overview")
        self.tabs.addTab(self.spec, "Spectrogram")
        self.tabs.addTab(self.bitplanes, "Bit planes")
        self.tabs.addTab(self.extract, "Extract")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.empty = QLabel("<h2>No file loaded</h2><p>Open a WAV file (Ctrl+O) or drop it onto this window.</p>"
                           "<p style='color:gray'>Raw stored sample bits are kept exactly (8/16/24/32-bit PCM, float, "
                           "extensible, RF64). Other formats are decoded through ffmpeg if installed.</p>")
        self.empty.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(self.empty)

        self.extract.bitsChanged.connect(self.bitplanes.set_highlight_bits)
        for w in (self.overview, self.spec):
            w.seek.connect(self.seek)
        for w in (self.spec, self.extract):
            w.status.connect(lambda m: self.statusBar().showMessage(m, 8000))

        self._build_actions()
        self._build_player()
        self.file_label = QLabel()
        self.statusBar().addPermanentWidget(self.file_label)

    # ---------------------------------------------------------------- UI
    def _build_actions(self):
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        a_open = QAction("&Open…", self, shortcut=QKeySequence.Open, triggered=self.open_dialog)
        fm.addAction(a_open)
        self.recent_menu = fm.addMenu("Open &recent")
        self._fill_recent()
        fm.addAction(QAction("&Close file", self, shortcut=QKeySequence.Close, triggered=self.close_file))
        fm.addSeparator()
        fm.addAction(QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close))
        vm = mb.addMenu("&View")
        for i, name in enumerate(("Overview", "Spectrogram", "Bit planes", "Extract")):
            vm.addAction(QAction(name, self, shortcut=f"Ctrl+{i + 1}", triggered=lambda _=False, i=i: self.tabs.setCurrentIndex(i)))
        vm.addSeparator()
        theme = vm.addMenu("Theme")
        for t in ("dark", "light"):
            theme.addAction(QAction(t.title(), self, triggered=lambda _=False, t=t: self._set_theme(t)))
        hm = mb.addMenu("&Help")
        hm.addAction(QAction("&About", self, triggered=self._about))

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        tb.addAction(self.style().standardIcon(QStyle.SP_DialogOpenButton), "Open", self.open_dialog)
        self.file_combo = QComboBox()
        self.file_combo.setMinimumWidth(240)
        self.file_combo.setToolTip("Loaded files")
        self.file_combo.currentIndexChanged.connect(self._combo_changed)
        tb.addWidget(self.file_combo)
        tb.addSeparator()
        self.play_act = tb.addAction("▶  Play", self.toggle_play)
        self.play_act.setToolTip("Play / pause (Space)")
        self.stop_act = tb.addAction("■  Stop", self.stop)
        self.time_label = QLabel(" 0:00.000 / 0:00.000 ")
        self.time_label.setStyleSheet("font-family: monospace")
        tb.addWidget(self.time_label)
        sp = QAction(self, shortcut=Qt.Key_Space, triggered=self.toggle_play)
        sp.setShortcutContext(Qt.WindowShortcut)
        self.addAction(sp)

    def _build_player(self):
        self.player = None
        if QMediaPlayer is None:
            self.play_act.setEnabled(False)
            self.play_act.setToolTip("Playback unavailable (QtMultimedia failed to load)")
            return
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.playbackStateChanged.connect(self._play_state)
        self.player.errorOccurred.connect(lambda *_: self.statusBar().showMessage(
            f"playback: {self.player.errorString()}", 8000))
        self.ph_timer = QTimer(self, interval=33)
        self.ph_timer.timeout.connect(self._update_playhead)

    # ---------------------------------------------------------------- files
    def open_dialog(self):
        start = self.settings.value("last_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(self, "Open audio", start, FILE_FILTER)
        for p in paths:
            self.open_path(p)

    def open_path(self, path):
        self.settings.setValue("last_dir", os.path.dirname(os.path.abspath(path)))
        self.statusBar().showMessage(f"loading {path}…")
        self.runner.submit(f"load:{path}", _load, path, on_ok=self._loaded,
                           on_err=lambda e, p=path: self.error(f"Could not open {p}:\n{e}"))

    def _loaded(self, res):
        audio, info = res
        self.files.append(res)
        self._add_recent(audio.path)
        self.file_combo.blockSignals(True)
        self.file_combo.addItem(audio.name, len(self.files) - 1)
        self.file_combo.setCurrentIndex(self.file_combo.count() - 1)
        self.file_combo.blockSignals(False)
        self.select(len(self.files) - 1)
        self.statusBar().showMessage(f"loaded {audio.path}", 5000)

    def _combo_changed(self, i):
        if i >= 0:
            self.select(self.file_combo.itemData(i))

    def select(self, idx):
        audio, info = self.files[idx]
        self.audio = audio
        if self.centralWidget() is not self.tabs:
            self.takeCentralWidget()
            self.setCentralWidget(self.tabs)
        self.stop()
        self.sel.set_audio(audio)
        self.overview.set_audio(audio, info)
        self.spec.set_audio(audio)
        self.bitplanes.set_audio(audio)
        self.extract.set_audio(audio)
        ch = {1: "mono", 2: "stereo"}.get(audio.channels, f"{audio.channels} ch")
        depth = f"{'float' if audio.is_float else 'int'} {audio.bits_per_sample}-bit"
        if audio.nbits != audio.bits_per_sample:
            depth += f" ({audio.nbits}-bit container)"
        self.file_label.setText(f"{audio.name} · {ch} · {audio.sample_rate} Hz · {depth} · "
                                f"{fmt_time(audio.duration)} · {audio.frames:,} frames")
        self.setWindowTitle(f"{audio.name} — wsteg")
        if self.player:
            src = audio.path if audio.source == "wav" or audio.path else None
            self.player.setSource(QUrl.fromLocalFile(src) if src else QUrl())
        self._tab_changed(self.tabs.currentIndex())

    def close_file(self):
        i = self.file_combo.currentIndex()
        if i < 0:
            return
        self.stop()
        idx = self.file_combo.itemData(i)
        self.files[idx] = None
        self.file_combo.removeItem(i)
        if self.file_combo.count():
            self.select(self.file_combo.currentData())
        else:
            self.audio = None
            self.takeCentralWidget()
            self.setCentralWidget(self.empty)
            self.file_label.setText("")
            self.setWindowTitle("wsteg — audio stego workbench")

    def _tab_changed(self, i):
        w = self.tabs.widget(i)
        if hasattr(w, "refresh_if_dirty"):
            w.refresh_if_dirty()

    def _fill_recent(self):
        self.recent_menu.clear()
        rec = [p for p in (self.settings.value("recent", []) or []) if isinstance(p, str)]
        for p in rec:
            self.recent_menu.addAction(p, lambda p=p: self.open_path(p))
        self.recent_menu.setEnabled(bool(rec))

    def _add_recent(self, path):
        if not path:
            return
        rec = [p for p in (self.settings.value("recent", []) or []) if isinstance(p, str) and p != path]
        self.settings.setValue("recent", [path] + rec[:9])
        self._fill_recent()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.open_path(u.toLocalFile())

    # ---------------------------------------------------------------- playback
    def toggle_play(self):
        if not self.player or not self.audio:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        if self.player:
            self.player.stop()
            self._update_playhead()

    def seek(self, t):
        if self.player and self.audio:
            self.player.setPosition(int(t * 1000))
            self._update_playhead(force_visible=True)

    def _play_state(self, st):
        playing = st == QMediaPlayer.PlayingState
        self.play_act.setText("❚❚  Pause" if playing else "▶  Play")
        if playing:
            self.ph_timer.start()
        else:
            self.ph_timer.stop()
            self._update_playhead()

    def _update_playhead(self, force_visible=False):
        if not self.player or not self.audio:
            return
        t = self.player.position() / 1000
        vis = force_visible or t > 0 or self.player.playbackState() == QMediaPlayer.PlayingState
        self.spec.set_playhead(t, vis)
        self.overview.set_playhead(t, vis)
        self.time_label.setText(f" {fmt_time(t)} / {fmt_time(self.audio.duration)} ")

    # ---------------------------------------------------------------- misc
    def error(self, msg):
        self.statusBar().showMessage(msg.splitlines()[0], 10000)
        QMessageBox.warning(self, "wsteg", msg)

    def _set_theme(self, t):
        self.settings.setValue("theme", t)
        QMessageBox.information(self, "wsteg", "The theme will be applied the next time wsteg starts.")

    def _about(self):
        QMessageBox.about(self, "About wsteg",
                          f"<b>wsteg {__version__}</b><br>Audio steganography workbench for CTFs.<br><br>"
                          "Bit numbering: bit 0 = LSB of the stored sample container.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("wsteg")
    app.setOrganizationName("wsteg")
    app.setDesktopFileName("wsteg")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), "wsteg.png")))
    apply_theme(app, QSettings("wsteg", "wsteg").value("theme", "dark"))
    w = MainWindow()
    w.show()
    for p in argv:
        if not p.startswith("-"):
            w.open_path(p)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
