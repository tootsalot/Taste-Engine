"""Build the Windows installer for the standalone app.

    python scripts/build_release.py                 lint, test, build, smoke test, installer
    python scripts/build_release.py --skip-checks   skip lint and tests (CI runs them first)
    python scripts/build_release.py --no-installer  stop after the smoke test (any OS)
    python scripts/build_release.py --check-tag v1.0.0   fail unless the tag matches __version__
    python scripts/build_release.py --notes v1.0.0       print that version's CHANGELOG section

Output goes to dist/: taste-engine-<version>-windows-x64-setup.exe plus a .sha256 file.
The app folder inside it carries LICENSE.txt and THIRD_PARTY_NOTICES.txt
(scripts/notices.py). The installer is made by Inno Setup 6.7 or later from
packaging/taste-engine.iss; set ISCC to its ISCC.exe if it isn't found on its own.
Windows releases are built on GitHub Actions. Needs: pip install -r requirements-build.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import notices  # scripts/notices.py

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_NAME = "taste-engine"
ISS = ROOT / "packaging" / "taste-engine.iss"


def version() -> str:
    text = (ROOT / "taste" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("Couldn't find __version__ in taste/__init__.py")
    return match.group(1)


def platform_tag() -> str:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    return f"{system}-{arch}"


def changelog_section(tag: str) -> str:
    wanted = tag.removeprefix("v")
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## \[?{re.escape(wanted)}\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if not match:
        sys.exit(f"CHANGELOG.md has no section for {wanted}")
    return match.group(1).strip()


def run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def build() -> Path:
    shutil.rmtree(BUILD, ignore_errors=True)
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "packaging/taste-engine.spec",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(BUILD / "dist"),
        "--workpath",
        str(BUILD / "work"),
    )
    app_dir = BUILD / "dist" / APP_NAME
    exe = app_dir / (APP_NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.is_file():
        sys.exit(f"Build finished but {exe} is missing")
    return exe


def smoke_test(exe: Path, expected_version: str) -> None:
    """Start the built app for real (offscreen) and check what it loaded."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "smoke.txt"
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",  # no screen needed, even on CI
            "TASTE_DATA_DIR": str(Path(tmp) / "data"),  # never touch real data
        }
        subprocess.run(
            [str(exe), "--smoke-test", str(report_path)], env=env, check=True, timeout=120
        )
        if not report_path.is_file():
            sys.exit("The built app ran but wrote no smoke test report")
        report = dict(
            line.split("=", 1) for line in report_path.read_text(encoding="utf-8").splitlines()
        )
    expected = {
        "version": expected_version,
        "title": "Taste Engine",
        "heading_font": "True",
        "body_font": "True",
        "icon": "True",
        "pages": "5",
        "webp": "True",
        "jpeg": "True",
        "gif": "True",
    }
    problems = {k: (report.get(k), v) for k, v in expected.items() if report.get(k) != v}
    if problems:
        sys.exit(f"Smoke test failed (got, expected): {problems}")
    print(f"smoke test: window built, fonts and icon loaded, version {expected_version}")


def installer_name(ver: str) -> str:
    """The setup file's name without .exe: taste-engine-1.0.0-windows-x64-setup."""
    return f"{APP_NAME}-{ver}-{platform_tag()}-setup"


def find_iscc() -> Path | None:
    """Inno Setup's compiler: $ISCC, then PATH, then where its installer puts it."""
    candidates = [os.environ.get("ISCC", ""), shutil.which("iscc") or ""]
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(base)
        if root:
            programs = Path(root) / ("Programs" if base == "LOCALAPPDATA" else "")
            candidates.append(str(programs / "Inno Setup 6" / "ISCC.exe"))
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def iscc_command(iscc: Path, ver: str, app_dir: Path, out_dir: Path) -> list[str]:
    return [
        str(iscc),
        f"/DAppVersion={ver}",
        f"/DAppFolder={app_dir}",
        f"/DOutputDir={out_dir}",
        f"/DOutputName={installer_name(ver)}",
        str(ISS),
    ]


def write_checksum(path: Path) -> Path:
    """<file>.sha256 next to it, in the format sha256sum -c reads."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = path.with_name(path.name + ".sha256")
    target.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return target


def package(exe: Path, ver: str) -> Path:
    """The installer, from the app folder with the license files added."""
    app_dir = exe.parent
    notices.write(app_dir, ver)  # THIRD_PARTY_NOTICES.txt and LICENSE.txt next to the exe
    iscc = find_iscc()
    if iscc is None:
        sys.exit(
            "Inno Setup 6.7 or later is needed to build the installer: get it from "
            "jrsoftware.org, or set ISCC to its ISCC.exe. --no-installer skips this step."
        )
    DIST.mkdir(exist_ok=True)
    run(*iscc_command(iscc, ver, app_dir, DIST))
    setup = DIST / f"{installer_name(ver)}.exe"
    if not setup.is_file():
        sys.exit(f"Inno Setup finished but {setup} is missing")
    write_checksum(setup)
    return setup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-checks", action="store_true", help="skip ruff and pytest")
    parser.add_argument(
        "--no-installer", action="store_true", help="stop after the smoke test (no Inno Setup)"
    )
    parser.add_argument("--check-tag", metavar="TAG", help="fail unless TAG is v<version>")
    parser.add_argument("--notes", metavar="TAG", help="print the CHANGELOG section for TAG")
    args = parser.parse_args()
    ver = version()

    if args.check_tag:
        if args.check_tag != f"v{ver}":
            sys.exit(f"Tag {args.check_tag} doesn't match __version__ {ver} (expected v{ver})")
        changelog_section(args.check_tag)  # also require release notes
        print(f"Tag {args.check_tag} matches version {ver}")
        return
    if args.notes:
        print(changelog_section(args.notes))
        return

    if not args.skip_checks:
        run(sys.executable, "-m", "ruff", "check", ".")
        run(sys.executable, "-m", "ruff", "format", "--check", ".")
        run(sys.executable, "-m", "pytest", "-q")
    exe = build()
    smoke_test(exe, ver)
    if args.no_installer:
        print(f"Built {exe.parent.relative_to(ROOT)} (no installer)")
        return
    setup = package(exe, ver)
    size_mb = setup.stat().st_size / 1_000_000
    print(f"Built {setup.relative_to(ROOT)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
