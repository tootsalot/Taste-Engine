"""Install, repair, and uninstall the built setup silently, checking each step. Windows only.

    python scripts/test_installer.py dist/taste-engine-<version>-windows-x64-setup.exe

The Release workflow runs this on a GitHub Windows runner before anything is published.
It installs the way anyone would (per user, %LOCALAPPDATA%\\Programs\\Taste Engine), so it
refuses to run where Taste Engine is already installed or has a data folder: it would
replace that install, and its uninstall step would remove it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import winreg  # Windows only, like the installer
from pathlib import Path

import build_release  # scripts/build_release.py: the version and the smoke test

APP_GUID = "3C5D937C-337E-45F7-AB56-E60E3A811924"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{APP_GUID}}}_is1"
LOCAL = Path(os.environ["LOCALAPPDATA"])
INSTALL_DIR = LOCAL / "Programs" / "Taste Engine"
EXE = INSTALL_DIR / "taste-engine.exe"
DATA_DIR = LOCAL / "taste-engine"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
SHORTCUT = START_MENU / "Taste Engine.lnk"
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


def step(text: str) -> None:
    print(f"installer test: {text}", flush=True)


def fail(text: str) -> None:
    sys.exit(f"installer test FAILED: {text}")


def uninstall_entry() -> dict[str, str]:
    """The values Windows shows in Settings > Apps, or {} when it isn't installed."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            names = ("DisplayName", "DisplayVersion", "Publisher", "UninstallString")
            return {name: winreg.QueryValueEx(key, name)[0] for name in names}
    except OSError:
        return {}


def run_setup(setup: Path, log: Path) -> None:
    result = subprocess.run([str(setup), *SILENT, f"/LOG={log}"], timeout=600)
    if result.returncode != 0:
        print(log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")
        fail(f"setup exited with {result.returncode}")


def wait_until(check, what: str, seconds: int = 180) -> None:
    """The uninstaller restarts itself from a temp copy and returns at once; wait for it."""
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            fail(f"timed out waiting for {what}")
        time.sleep(1)


def check_installed(version: str) -> None:
    files = ("_internal", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt")
    for path in (EXE, SHORTCUT, *(INSTALL_DIR / name for name in files)):
        if not path.exists():
            fail(f"{path} is missing")
    entry = uninstall_entry()
    expected = {
        "DisplayName": "Taste Engine",
        "DisplayVersion": version,
        "Publisher": "Taste Engine",
    }
    for name, value in expected.items():
        if entry.get(name) != value:
            fail(f"uninstall entry {name} is {entry.get(name)!r}, expected {value!r}")


def check_delete_all_data() -> None:
    """The flag the uninstaller runs when "Also delete my profiles..." is ticked."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "data"
        (folder / "profiles").mkdir(parents=True)
        (folder / "profiles" / "ci.db").write_bytes(b"")
        (folder / "ui_state.json").write_text("{}", encoding="utf-8")
        env = {**os.environ, "TASTE_DATA_DIR": str(folder)}
        result = subprocess.run([str(EXE), "--delete-all-data"], env=env, timeout=120)
        if result.returncode != 0 or folder.exists():
            left = "left the folder" if folder.exists() else "deleted the folder"
            fail(f"--delete-all-data exited with {result.returncode} and {left}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    setup = Path(sys.argv[1]).resolve()
    if not setup.is_file():
        fail(f"no setup at {setup}")
    if uninstall_entry() or INSTALL_DIR.exists() or DATA_DIR.exists():
        fail("Taste Engine is already installed or has data here; use a clean machine")
    version = build_release.version()
    logs = Path(tempfile.mkdtemp(prefix="taste-installer-"))

    step(f"installing {setup.name}")
    run_setup(setup, logs / "install.log")
    check_installed(version)
    build_release.smoke_test(EXE, version)
    check_delete_all_data()
    step("installed, smoke test passed, --delete-all-data works")

    stale = INSTALL_DIR / "_internal" / "left-by-an-older-version.txt"
    stale.write_text("stale", encoding="utf-8")
    step("running setup again (repair)")
    run_setup(setup, logs / "repair.log")
    check_installed(version)
    if stale.exists():
        fail("repair left a file from the old bundle behind")
    build_release.smoke_test(EXE, version)
    step("repaired, old bundle cleared, smoke test passed")

    marker = DATA_DIR / "profiles" / "keep-me.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("a profile would be here", encoding="utf-8")
    uninstaller = uninstall_entry()["UninstallString"].strip('"')
    step("uninstalling")
    subprocess.run([uninstaller, *SILENT, f"/LOG={logs / 'uninstall.log'}"], timeout=600)
    wait_until(lambda: not uninstall_entry(), "the uninstall entry to go")
    wait_until(lambda: not INSTALL_DIR.exists(), "the install folder to go")
    if SHORTCUT.exists():
        fail("the Start menu shortcut is still there")
    if not marker.is_file():
        fail("a silent uninstall deleted the data folder")
    marker.unlink()
    for folder in (marker.parent, DATA_DIR):
        folder.rmdir()
    step("uninstalled; data folder kept, as a silent uninstall should")
    print(f"installer test: all steps passed for {setup.name}")


if __name__ == "__main__":
    main()
