"""Build a release package of the standalone app.

    python scripts/build_release.py                 lint, test, build, smoke test, zip
    python scripts/build_release.py --skip-checks   skip lint and tests (CI runs them first)
    python scripts/build_release.py --check-tag v0.2.0   fail unless the tag matches __version__
    python scripts/build_release.py --notes v0.2.0       print that version's CHANGELOG section

Output goes to dist/: taste-engine-<version>-<platform>.zip plus a .sha256 file.
The build is for the OS it runs on. Windows releases are built on GitHub Actions.
Needs: pip install -r requirements-build.txt
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
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_NAME = "taste-engine"


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
        "syne": "True",
        "manrope": "True",
        "icon": "True",
        "pages": "4",
        "webp": "True",
        "jpeg": "True",
    }
    problems = {k: (report.get(k), v) for k, v in expected.items() if report.get(k) != v}
    if problems:
        sys.exit(f"Smoke test failed (got, expected): {problems}")
    print(f"smoke test: window built, fonts and icon loaded, version {expected_version}")


def package(exe: Path, ver: str) -> Path:
    DIST.mkdir(exist_ok=True)
    zip_path = DIST / f"{APP_NAME}-{ver}-{platform_tag()}.zip"
    app_dir = exe.parent
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(app_dir))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_suffix(".zip.sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-checks", action="store_true", help="skip ruff and pytest")
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
    zip_path = package(exe, ver)
    size_mb = zip_path.stat().st_size / 1_000_000
    print(f"Built {zip_path.relative_to(ROOT)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
