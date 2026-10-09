"""The release's third-party notices: every bundled package and its license text."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import notices  # noqa: E402


@pytest.fixture(scope="module")
def text():
    return notices.build("9.9.9")


def test_every_runtime_package_and_its_dependencies_are_credited(text):
    names = {d.metadata["Name"].lower() for d in notices.bundled_distributions()}
    assert {"requests", "keyring", "python-dotenv", "tzdata", "pyside6"} <= names
    assert {"urllib3", "idna", "certifi", "charset-normalizer", "shiboken6"} <= names
    assert "pytest" not in names and "ruff" not in names  # development tools don't ship
    for dist in notices.bundled_distributions():
        assert f"{dist.metadata['Name']} {dist.version}" in text


def test_license_texts_are_included_not_just_named(text):
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in text
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Apache License" in text  # requests
    assert "Mozilla Public License" in text  # certifi
    assert "SIL OPEN FONT LICENSE" in text.upper()
    assert "PYTHON SOFTWARE FOUNDATION LICENSE" in text.upper()
    assert "WebP (libwebp)" in text and "LibJPEG-turbo" in text  # Qt's own components


def test_credits_and_source_links(text):
    assert text.startswith("Taste Engine 9.9.9")
    assert "MyAnimeList" in text and "Last.fm" in text and "isn't affiliated" in text
    assert "can be replaced with modified, compatible versions" in text  # LGPL relinking
    assert "https://download.qt.io/official_releases/QtForPython/" in text
    assert "https://pypi.org/project/requests/" in text
    for font in ("Inter", "SpaceGrotesk"):
        assert f"Font: {font}" in text


def test_write_puts_both_files_in_the_folder(tmp_path):
    path = notices.write(tmp_path, "9.9.9")
    assert path.name == "THIRD_PARTY_NOTICES.txt" and path.stat().st_size > 100_000
    assert (tmp_path / "LICENSE.txt").read_text(encoding="utf-8").startswith("MIT License")


def test_qt_is_credited_under_the_lgpl_not_the_commercial_license(text):
    assert "Licensees holding valid commercial Qt licenses" not in text
