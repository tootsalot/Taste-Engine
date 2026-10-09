# PyInstaller build spec for the desktop app.
# Build with: python scripts/build_release.py   (or: pyinstaller packaging/taste-engine.spec)
# Produces a one-folder app: dist/taste-engine/taste-engine(.exe), windowed (no console).

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)

datas = [
    (str(ROOT / "taste" / "sql"), "taste/sql"),
    (str(ROOT / "taste" / "desktop" / "assets"), "taste/desktop/assets"),
]
# IANA time zone data (Windows has none built in) and keyring's backend registry.
datas += collect_data_files("tzdata")
datas += copy_metadata("keyring")

hiddenimports = collect_submodules("keyring.backends") + collect_submodules("taste")

# Qt modules the app never uses. Leaving them out keeps the download smaller.
excludes = [
    "tkinter",
    "pytest",
    "pytestqt",
    "ruff",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtBluetooth",
    "PySide6.QtSerialPort",
    "PySide6.QtDesigner",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="taste-engine",
    console=False,  # a real windowed app: no console behind it
    icon=str(ROOT / "packaging" / "taste-engine.ico"),
    upx=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="taste-engine",
    upx=False,
)
