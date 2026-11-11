# PyInstaller build spec for the desktop app.
# Build with: python scripts/build_release.py   (or: pyinstaller packaging/taste-engine.spec)
# Produces a one-folder app: dist/taste-engine/taste-engine(.exe), windowed (no console).

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)
APP_NAME = "Taste Engine"  # as people read it
EXE_NAME = "taste-engine"  # the program and its folder; Windows adds .exe
VERSION = re.search(
    r'^__version__ = "([^"]+)"',
    (ROOT / "taste" / "__init__.py").read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)

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

# Files PyInstaller's Qt hook brings along that the app never loads:
# - Qt's PDF image plugin, which drags in Qt PDF (Chromium's PDFium). No PDFs are read.
# - opengl32sw.dll, a 20 MB software OpenGL for apps that draw with OpenGL on a PC
#   without a graphics driver. The app draws with Qt's own painter, never OpenGL.
UNUSED_BINARIES = ("qt6pdf", "qpdf", "opengl32sw")


def unused_binary(entry) -> bool:
    return Path(entry[0]).name.lower().startswith(UNUSED_BINARIES)


def qt_translation(entry) -> bool:
    """Qt's own words for its dialogs in other languages. The app is in English and
    installs no translator, so they are never read."""
    path = Path(entry[0])
    return path.suffix.lower() == ".qm" and "translations" in path.parts


# Windows keeps a file's version details per language: these are US English, as Unicode.
LANGUAGE_ID, CODE_PAGE = 0x0409, 1200
STRING_TABLE = f"{LANGUAGE_ID:04X}{CODE_PAGE:04X}"
VERSION_PARTS = 4  # a Windows file version is always four numbers: 4.0.1 becomes 4.0.1.0


def version_file() -> str:
    """What Windows shows for the exe (Properties > Details, Task Manager's name, a
    firewall prompt): written in the text form PyInstaller's `version` reads."""
    numbers = [int(part) for part in re.findall(r"\d+", VERSION)[:VERSION_PARTS]]
    numbers += [0] * (VERSION_PARTS - len(numbers))
    strings = {
        "CompanyName": APP_NAME,
        "FileDescription": APP_NAME,
        "FileVersion": VERSION,
        "InternalName": EXE_NAME,
        "OriginalFilename": f"{EXE_NAME}.exe",
        "ProductName": APP_NAME,
        "ProductVersion": VERSION,
    }
    rows = ", ".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    text = (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={tuple(numbers)}, prodvers={tuple(numbers)}, mask=0x3f,\n"
        "    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        f"    StringFileInfo([StringTable({STRING_TABLE!r}, [{rows}])]),\n"
        f"    VarFileInfo([VarStruct('Translation', [{LANGUAGE_ID}, {CODE_PAGE}])]),\n"
        "  ],\n"
        ")\n"
    )
    folder = Path(workpath)  # noqa: F821 (workpath is provided by PyInstaller)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "version_info.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries if not unused_binary(entry)]
a.datas = [entry for entry in a.datas if not qt_translation(entry)]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    console=False,  # a real windowed app: no console behind it
    icon=str(ROOT / "packaging" / "taste-engine.ico"),
    version=version_file(),  # used on Windows only
    upx=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name=EXE_NAME,
    upx=False,
)
