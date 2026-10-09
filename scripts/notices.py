"""Write THIRD_PARTY_NOTICES.txt for a release: credits and every bundled license.

Used by build_release.py. What's included:
- Python itself (its LICENSE.txt, which covers the parts Python bundles too)
- every Python package the app ships: requirements.txt's runtime section and
  everything those packages depend on, with the license files from each package
- Qt for Python (PySide6, Shiboken6) and Qt, under the LGPL 3.0, plus Qt's own
  third-party components (packaging/notices/qt-third-party.txt)
- the fonts (SIL Open Font License)
- PyInstaller's bootloader, when PyInstaller is installed (it is for a build)
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
NOTICES = ROOT / "packaging" / "notices"
FONTS = ROOT / "taste" / "desktop" / "assets" / "fonts"
LICENSE_NAME = re.compile(r"(licen[cs]e|copying|notice|authors)", re.IGNORECASE)
RULE = "\n\n" + "=" * 78 + "\n"
QT_PREFIXES = ("pyside6", "shiboken6")  # PySide6, its Essentials/Addons/Pdf/WebEngine parts


class NoticeError(RuntimeError):
    """Something a complete notice needs is missing."""


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_requirements() -> list[str]:
    """Package names from requirements.txt, up to the development section."""
    names = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("# development"):
            break
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.append(Requirement(line).name)
    return names


def bundled_distributions() -> list:
    """The runtime packages and everything they need on this platform, sorted by name."""
    env = default_environment()
    env["extra"] = ""
    found: dict[str, object] = {}
    todo = runtime_requirements()
    while todo:
        name = todo.pop()
        if _key(name) in found:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            raise NoticeError(f"{name} is required but not installed") from None
        found[_key(name)] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate(env):
                todo.append(req.name)
    return sorted(found.values(), key=lambda d: _key(d.metadata["Name"]))


def license_texts(dist) -> list[tuple[str, str]]:
    """(file name, text) for each license-like file in a package's metadata."""
    texts = []
    for file in dist.files or []:
        parts = Path(str(file)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        if LICENSE_NAME.search(file.name) or "licenses" in parts:
            path = Path(dist.locate_file(file))
            if path.is_file():
                texts.append(
                    (file.name, path.read_text(encoding="utf-8", errors="replace").strip())
                )
    return texts


def license_label(dist) -> str:
    meta = dist.metadata
    label = meta.get("License-Expression") or meta.get("License") or ""
    if not label or len(label) > 80:  # some packages paste the whole license here
        classifiers = [
            c.split("::")[-1].strip()
            for c in meta.get_all("Classifier") or []
            if c.startswith("License ::")
        ]
        label = ", ".join(classifiers) or "see the license text below"
    return label


def python_license() -> str:
    base = Path(sys.base_prefix)
    major_minor = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for path in (
        base / "LICENSE.txt",
        base / "LICENSE",
        base / "lib" / major_minor / "LICENSE.txt",
    ):
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    raise NoticeError(f"Python's LICENSE file wasn't found under {base}")


def build(version: str) -> str:
    """The whole notices file as text."""
    dists = bundled_distributions()
    qt = [d for d in dists if _key(d.metadata["Name"]).startswith(QT_PREFIXES)]
    others = [d for d in dists if d not in qt]
    qt_version = distribution("PySide6").version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    fonts = sorted(FONTS.glob("*-OFL.txt"))
    try:
        pyinstaller = distribution("pyinstaller")
    except PackageNotFoundError:
        pyinstaller = None

    contents = [
        f"Python {py}: PSF License",
        "Qt for Python ("
        + ", ".join(f"{d.metadata['Name']} {d.version}" for d in qt)
        + "): LGPL-3.0",
        f"Qt {qt_version}: LGPL-3.0, and the third-party components Qt includes",
        *[f"{d.metadata['Name']} {d.version}: {license_label(d)}" for d in others],
        *[f"Font: {f.name.removesuffix('-OFL.txt')}: SIL Open Font License 1.1" for f in fonts],
    ]
    if pyinstaller:
        contents.append(
            f"PyInstaller {pyinstaller.version} bootloader: GPL-2.0 with the bootloader exception"
        )

    out = [
        f"Taste Engine {version}\n"
        "Copyright (c) 2026 Tootsalot. Released under the MIT License (see LICENSE.txt).\n\n"
        "Anime data comes from MyAnimeList (myanimelist.net) and music data from Last.fm\n"
        "(last.fm), through their public APIs. Taste Engine isn't affiliated with or\n"
        "endorsed by either.\n\n"
        "Taste Engine includes the software below. Thank you to everyone who made it.\n\n"
        + "\n".join(f"  - {line}" for line in contents)
    ]

    out.append(
        "Python\n\n"
        f"This app includes the Python {py} runtime. https://www.python.org/\n\n" + python_license()
    )

    out.append(
        f"Qt for Python (PySide6, Shiboken6) and Qt {qt_version}\n\n"
        "Used under the GNU Lesser General Public License, version 3 (LGPL-3.0), whose\n"
        "full text follows, together with the GNU General Public License, version 3,\n"
        "that it builds on.\n\n"
        "Qt and PySide6 are dynamically linked. Their libraries are separate files in\n"
        "this folder (the Qt6*.dll files and the PySide6 and shiboken6 folders under\n"
        "_internal) and can be replaced with modified, compatible versions.\n\n"
        "Source code:\n"
        "  Qt: https://code.qt.io/ and https://download.qt.io/official_releases/qt/\n"
        "  Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/ and\n"
        "  https://download.qt.io/official_releases/QtForPython/\n\n"
        "Copyright (C) The Qt Company Ltd. and other contributors. (The PySide6 packages'\n"
        "only license file is Qt's commercial-license notice, which doesn't apply here.)\n\n"
        + (NOTICES / "LGPL-3.0.txt").read_text(encoding="utf-8").strip()
        + "\n\n"
        + (NOTICES / "GPL-3.0.txt").read_text(encoding="utf-8").strip()
    )
    out.append((NOTICES / "qt-third-party.txt").read_text(encoding="utf-8").strip())

    for dist in others:
        name = dist.metadata["Name"]
        texts = license_texts(dist)
        if not texts:
            raise NoticeError(f"{name} has no license file in its package metadata")
        out.append(
            f"{name} {dist.version}\n"
            f"License: {license_label(dist)}\n"
            f"Source: https://pypi.org/project/{name}/{dist.version}/\n\n"
            + "\n\n".join(f"[{file}]\n{text}" for file, text in texts)
        )

    for font in fonts:
        out.append(
            f"Font: {font.name.removesuffix('-OFL.txt')}\n\n"
            + font.read_text(encoding="utf-8").strip()
        )

    if pyinstaller:
        texts = license_texts(pyinstaller)
        out.append(
            f"PyInstaller {pyinstaller.version}\n\n"
            "The program that starts this app (the bootloader) comes from PyInstaller. It's\n"
            "licensed under the GPL 2.0 with an exception that allows distributing it with\n"
            "programs under any license. https://pyinstaller.org/\n\n"
            + "\n\n".join(f"[{file}]\n{text}" for file, text in texts)
        )
    return RULE.join(out) + "\n"


def write(folder: Path, version: str) -> Path:
    """Write THIRD_PARTY_NOTICES.txt and LICENSE.txt into a release folder."""
    path = folder / "THIRD_PARTY_NOTICES.txt"
    path.write_text(build(version), encoding="utf-8")
    (folder / "LICENSE.txt").write_text(
        (ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return path
