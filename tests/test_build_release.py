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
