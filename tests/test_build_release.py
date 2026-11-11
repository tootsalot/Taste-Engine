"""The installer half of scripts/build_release.py, and the Inno Setup script it feeds.

Building the installer itself needs Windows and Inno Setup, so CI does that
(scripts/test_installer.py). These check the pieces that run anywhere.
"""

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_release  # noqa: E402

# Updates, repairs, and uninstall find the installed copy by this. It must never change.
APP_GUID = "3C5D937C-337E-45F7-AB56-E60E3A811924"


def test_installer_name(monkeypatch):
    monkeypatch.setattr(build_release, "platform_tag", lambda: "windows-x64")
    assert build_release.installer_name("0.2.0") == "taste-engine-0.2.0-windows-x64-setup"


def test_the_compiler_comes_from_iscc_or_the_install_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(build_release.shutil, "which", lambda name: None)
    for name in ("ISCC", "ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    assert build_release.find_iscc() is None

    installed = tmp_path / "Programs" / "Inno Setup 6" / "ISCC.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # a per-user Inno Setup install
    assert build_release.find_iscc() == installed

    chosen = tmp_path / "my-iscc.exe"
    chosen.write_bytes(b"")
    monkeypatch.setenv("ISCC", str(chosen))
    assert build_release.find_iscc() == chosen


def test_the_compiler_gets_every_value_the_script_needs(monkeypatch, tmp_path):
    monkeypatch.setattr(build_release, "platform_tag", lambda: "windows-x64")
    command = build_release.iscc_command(Path("ISCC.exe"), "1.2.3", tmp_path / "app", tmp_path)
    assert command[-1] == str(build_release.ISS)
    defines = dict(arg[2:].split("=", 1) for arg in command if arg.startswith("/D"))
    assert defines == {
        "AppVersion": "1.2.3",
        "AppFolder": str(tmp_path / "app"),
        "OutputDir": str(tmp_path),
        "OutputName": "taste-engine-1.2.3-windows-x64-setup",
    }
    script = build_release.ISS.read_text(encoding="utf-8")
    assert set(re.findall(r"\{#(App\w+|Output\w+)\}", script)) >= set(defines)


def test_the_installer_keeps_its_identity_and_installs_per_user():
    script = build_release.ISS.read_text(encoding="utf-8")
    assert f'#define AppGuid "{APP_GUID}"' in script
    assert "AppId={{{#AppGuid}}" in script
    assert "PrivilegesRequired=lowest" in script
    assert "AppPublisher=Taste Engine" in script
    # Desktop shortcut offered but off; the old bundle is cleared before an update.
    assert re.search(r'Name: "desktopicon".*Flags: unchecked', script)
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in script
    assert "--delete-all-data" in script  # the uninstaller's data question uses the app


def test_checksum_file_matches_sha256sum(tmp_path):
    setup = tmp_path / "taste-engine-0.2.0-windows-x64-setup.exe"
    setup.write_bytes(b"not really an installer")
    line = build_release.write_checksum(setup).read_text(encoding="utf-8")
    assert line == f"{hashlib.sha256(setup.read_bytes()).hexdigest()}  {setup.name}\n"


def test_the_uninstaller_looks_for_the_mutex_the_app_holds():
    from taste.desktop import APP_MUTEX, hold_app_mutex

    script = build_release.ISS.read_text(encoding="utf-8")
    assert f'#define AppMutex "{APP_MUTEX}"' in script
    assert "CheckForMutexes('{#AppMutex}')" in script
    installer_test = (ROOT / "scripts" / "test_installer.py").read_text(encoding="utf-8")
    assert f'APP_MUTEX = "{APP_MUTEX}"' in installer_test

    name = "TasteEngineTest-mutex-check"
    handle = hold_app_mutex(name)
    if sys.platform != "win32":
        assert handle is None
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    synchronize = 0x00100000
    found = kernel32.OpenMutexW(synchronize, False, name)
    assert handle and found  # what Inno's CheckForMutexes does
    kernel32.CloseHandle(found)
    kernel32.CloseHandle(handle)
    assert not kernel32.OpenMutexW(synchronize, False, name)  # gone once the app closes it


def test_the_wizard_follows_windows_and_its_images_exist():
    script = build_release.ISS.read_text(encoding="utf-8")
    assert re.search(r"^WizardStyle=modern dynamic\b", script, re.MULTILINE)
    patterns = re.findall(r"^Wizard\w*ImageFile\w*=(.+)$", script, re.MULTILINE)
    assert len(patterns) == 4  # side and corner, light and dark
    for pattern in patterns:
        found = list(build_release.ISS.parent.glob(pattern.strip().replace("\\", "/")))
        assert found, f"{pattern} matches no files (run scripts/make_wizard_images.py)"


def test_the_smoke_test_expects_the_pages_the_window_really_has(qtbot):
    """The packaged app's smoke test checks the page count; a page added without updating
    it failed the 2.0.0 release build at its last step."""
    from taste.desktop.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    assert window.stack.count() == build_release.SMOKE_PAGES


def test_the_installer_names_itself_and_refuses_a_windows_too_old_for_qt():
    script = build_release.ISS.read_text(encoding="utf-8")
    for line in (
        "VersionInfoProductVersion={#AppVersion}",
        "VersionInfoProductName={#AppName}",
        "VersionInfoDescription={#AppName} Setup",
        '#define MinWindows "10.0.17763"',  # Windows 10 version 1809, the oldest Qt 6 runs on
        "MinVersion={#MinWindows}",
        "SetupMutex=TasteEngineSetup-{#AppGuid}",  # one setup at a time
    ):
        assert re.search(rf"^{re.escape(line)}$", script, re.MULTILINE), line


def run_spec(monkeypatch, tmp_path, binaries, datas) -> dict:
    """Run packaging/taste-engine.spec with stand-ins for PyInstaller (a development
    install doesn't have it). Returns what it handed to EXE and COLLECT."""
    import types

    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = hooks.collect_submodules = hooks.copy_metadata = lambda name: []
    for name, module in (
        ("PyInstaller", types.ModuleType("PyInstaller")),
        ("PyInstaller.utils", types.ModuleType("PyInstaller.utils")),
        ("PyInstaller.utils.hooks", hooks),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    made = {}

    class Analysis:
        def __init__(self, scripts, **kwargs):
            self.scripts, self.pure = scripts, []
            self.binaries, self.datas = list(binaries), list(datas)

    def exe(*args, **kwargs):
        made["exe"] = kwargs

    def collect(_exe, *tocs, **kwargs):
        made["binaries"], made["datas"] = (list(toc) for toc in tocs)

    spec = ROOT / "packaging" / "taste-engine.spec"
    scope = {
        "SPECPATH": str(spec.parent),
        "workpath": str(tmp_path / "work"),
        "Analysis": Analysis,
        "PYZ": lambda pure: None,
        "EXE": exe,
        "COLLECT": collect,
    }
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), scope)
    return made


def test_the_bundle_leaves_out_what_the_app_never_loads(monkeypatch, tmp_path):
    kept_binaries = [
        ("PySide6/Qt6Core.dll", "x", "BINARY"),
        ("PySide6/plugins/imageformats/qwebp.dll", "x", "BINARY"),
    ]
    kept_datas = [
        ("tzdata/zoneinfo/Europe/Paris", "x", "DATA"),
        ("taste/desktop/assets/logo.svg", "x", "DATA"),
    ]
    dropped_binaries = [
        ("PySide6/opengl32sw.dll", "x", "BINARY"),  # 20 MB of software OpenGL
        ("PySide6/Qt6Pdf.dll", "x", "BINARY"),
        ("PySide6/plugins/imageformats/qpdf.dll", "x", "BINARY"),
    ]
    dropped_datas = [("PySide6/translations/qtbase_de.qm", "x", "DATA")]
    made = run_spec(
        monkeypatch, tmp_path, kept_binaries + dropped_binaries, kept_datas + dropped_datas
    )
    assert made["binaries"] == kept_binaries
    assert made["datas"] == kept_datas


def test_the_exe_carries_its_name_and_version(monkeypatch, tmp_path):
    """Without them Windows shows "taste-engine.exe" with no product or version in
    Properties, Task Manager, and firewall prompts."""
    made = run_spec(monkeypatch, tmp_path, [], [])
    text = Path(made["exe"]["version"]).read_text(encoding="utf-8")
    found = {}

    def record(kind):
        return lambda *args, **kwargs: found.setdefault(kind, []).append((args, kwargs)) or kind

    names = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "VarFileInfo")
    scope = {name: record(name) for name in (*names, "StringStruct", "VarStruct")}
    eval(text, scope)  # the form PyInstaller reads: one VSVersionInfo(...) expression
    version = build_release.version()
    numbers = tuple(int(part) for part in version.split("."))
    fixed = found["FixedFileInfo"][0][1]
    assert fixed["filevers"][: len(numbers)] == numbers and len(fixed["filevers"]) == 4
    assert fixed["prodvers"] == fixed["filevers"]
    strings = dict(args for args, _ in found["StringStruct"])
    assert strings["ProductName"] == strings["FileDescription"] == "Taste Engine"
    assert strings["ProductVersion"] == strings["FileVersion"] == version
    assert strings["OriginalFilename"] == "taste-engine.exe"
