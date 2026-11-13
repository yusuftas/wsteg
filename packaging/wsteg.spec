# PyInstaller spec — run via `python scripts/build.py` (works on Windows and Linux).
# Produces dist/wsteg/ containing:
#   wsteg-gui(.exe)  windowed desktop app
#   wsteg(.exe)      console CLI (info / extract); with no arguments it also opens the GUI
# Set WSTEG_ONEFILE=1 for a single self-extracting GUI executable instead.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ONEFILE = os.environ.get("WSTEG_ONEFILE") == "1"
ICON = os.path.join(ROOT, "packaging", "wsteg.ico" if os.name == "nt" else "wsteg.png")
ICON = ICON if os.path.exists(ICON) else None

EXCLUDES = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "PyQt5", "PyQt6", "PySide2",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtHttpServer", "PySide6.QtWebSockets", "PySide6.QtLocation",
]


# Qt >= 6.5's xcb plugin needs libxcb-cursor.so.0, which many distros don't install by
# default. Bundle it when available (system copy, or dropped into packaging/linux-libs/).
EXTRA_BINARIES = []
if sys.platform.startswith("linux"):
    for d in (os.path.join(ROOT, "packaging", "linux-libs"), "/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib"):
        lib = os.path.join(d, "libxcb-cursor.so.0")
        if os.path.exists(lib):
            EXTRA_BINARIES.append((lib, "."))
            print("bundling", lib)
            break
    else:
        print("WARNING: libxcb-cursor.so.0 not found; users will need libxcb-cursor0 installed")


def analysis(script):
    return Analysis([os.path.join(ROOT, "packaging", script)], pathex=[ROOT], excludes=EXCLUDES,
                    datas=[(os.path.join(ROOT, "wsteg", "gui", "wsteg.png"), "wsteg/gui")],
                    binaries=EXTRA_BINARIES,
                    hiddenimports=["PySide6.QtMultimedia"], noarchive=False)


gui_a = analysis("gui_entry.py")
gui_pyz = PYZ(gui_a.pure)

if ONEFILE:
    exe = EXE(gui_pyz, gui_a.scripts, gui_a.binaries, gui_a.datas, [], name="wsteg-gui",
              console=False, upx=False, icon=ICON)
else:
    cli_a = analysis("cli_entry.py")
    cli_pyz = PYZ(cli_a.pure)
    gui_exe = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True, name="wsteg-gui",
                  console=False, upx=False, icon=ICON)
    cli_exe = EXE(cli_pyz, cli_a.scripts, [], exclude_binaries=True, name="wsteg",
                  console=True, upx=False, icon=ICON)
    COLLECT(gui_exe, gui_a.binaries, gui_a.datas, cli_exe, cli_a.binaries, cli_a.datas,
            name="wsteg", upx=False)
