# PyInstaller build spec for the standalone app.
# Build with: python scripts/build_release.py   (or: pyinstaller packaging/taste-engine.spec)
# Produces a one-folder app: dist/taste-engine/taste-engine(.exe)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)

datas = [
    (str(ROOT / "taste" / "sql"), "taste/sql"),
    (str(ROOT / "taste" / "web" / "templates"), "taste/web/templates"),
    (str(ROOT / "taste" / "web" / "static"), "taste/web/static"),
]
# IANA time zone data (Windows has none built in) and keyring's backend registry.
datas += collect_data_files("tzdata")
datas += copy_metadata("keyring")

hiddenimports = collect_submodules("keyring.backends") + collect_submodules("taste")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="taste-engine",
    console=True,  # the console window shows the URL and closing it stops the app
    upx=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="taste-engine",
    upx=False,
)
