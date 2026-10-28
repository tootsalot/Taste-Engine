"""Portable profile archives, without credentials. Restore treats every member as untrusted."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from taste import __version__, db, profiles
from taste.config import data_dir

FORMAT = 1
MAX_UNPACKED_BYTES = 1 << 30
MAX_MEMBERS = 10_000
MAX_MANIFEST_BYTES = 64 << 10


class BackupError(ValueError):
    """An unsupported, damaged, or unsafe profile archive."""


def create(
    profile_id: str,
    target: str | Path,
    *,
    database: Path | None = None,
    recap_root: Path | None = None,
    picture: Path | None = None,
) -> Path:
    """Copy a live database consistently, then package only its recaps and picture."""
    path = database or profiles.db_path(profile_id)
    if not path.is_file():
        raise profiles.ProfileError("The profile no longer exists.")
    target = Path(target)
    recaps = recap_root or data_dir() / "recaps" / profile_id
    picture = picture or profiles.picture_path(profile_id)
    # Never replace profile content through a mistaken file-picker path.
    protected = profiles.profiles_dir().resolve(), recaps.resolve()
    if any(target.resolve().is_relative_to(p) for p in protected):
        raise BackupError("Choose a backup outside the profile and recap folders.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="taste-backup-") as temporary:
        snapshot = Path(temporary) / "profile.db"
        source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        dest = sqlite3.connect(snapshot)
        try:
            source.backup(dest)
            row = dest.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?", ("display_name",)
            ).fetchone()
        finally:
            dest.close()
            source.close()
        manifest = {
            "format": FORMAT,
            "app_version": __version__,
            "profile_id": profile_id,
            "display_name": (row[0] if row else "") or profile_id,
            "created_at": db.utc_now(),
        }
        handle, temporary_name = tempfile.mkstemp(prefix=".taste-backup-", dir=target.parent)
        os.close(handle)
        pending = Path(temporary_name)
        try:
            with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                archive.write(snapshot, "profile.db")
                if picture.is_file() and not picture.is_symlink():
                    archive.write(picture, "picture.png")
                if recaps.is_dir() and not recaps.is_symlink():
                    for item in sorted(recaps.rglob("*")):
                        if item.is_symlink():
                            raise BackupError("Recap links cannot be backed up.")
                        if item.is_file():
                            archive.write(item, "recaps/" + item.relative_to(recaps).as_posix())
            os.replace(pending, target)
        finally:
            pending.unlink(missing_ok=True)
    return target


def _members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = {}
    total = 0
    for member in archive.infolist():
        name = member.filename
        parts = PurePosixPath(name).parts
        mode = member.external_attr >> 16
        if (
            not name
            or "\\" in name
            or ":" in name
            or name.startswith("/")
            or any(p in ("", ".", "..") for p in name.rstrip("/").split("/"))
            or any(ord(c) < 32 for c in name)
            or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
        ):
            raise BackupError(
                "Archive members must be regular files with safe relative names, never links."
            )
        expected = name in ("manifest.json", "profile.db", "picture.png") or (
            parts and parts[0] == "recaps" and (len(parts) > 1 or member.is_dir())
        )
        if not expected or name in members:
            raise BackupError("The archive has an unexpected or duplicate member.")
        if member.flag_bits & 1:
            raise BackupError("Encrypted archives are not supported.")
        total += member.file_size
        members[name] = member
        if total > MAX_UNPACKED_BYTES or len(members) > MAX_MEMBERS:
            raise BackupError("The archive's unpacked size is too large.")
    if not {"manifest.json", "profile.db"} <= members.keys():
        raise BackupError("The archive needs a manifest and a database.")
    if members["manifest.json"].file_size > MAX_MANIFEST_BYTES:
        raise BackupError("The manifest is too large.")
    return members


def _version(text: object) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return None


def _manifest(archive: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (ValueError, RecursionError):  # deeply nested JSON exhausts the parser's stack
        raise BackupError("This file isn't a profile backup, or it's damaged.") from None
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("format")) is not int
        or manifest["format"] != FORMAT
    ):
        raise BackupError("This backup format is not supported.")
    made_by = _version(manifest.get("app_version"))
    if made_by and made_by > _version(__version__):
        raise BackupError(
            "This backup is from a newer version of Taste Engine. Update the app, then restore it."
        )
    return manifest


def read_manifest(path: str | Path) -> dict:
    """An archive's checked manifest, to offer its profile id before restoring."""
    try:
        with zipfile.ZipFile(path) as archive:
            _members(archive)
            return _manifest(archive)
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        raise BackupError("This file isn't a profile backup, or it's damaged.") from None


def id_in_use(profile_id: str, root: Path | None = None, recap_root: Path | None = None) -> bool:
    """A profile, its picture, or a retained recap folder already has this id."""
    root = root or profiles.profiles_dir()
    recap_root = recap_root or data_dir() / "recaps"
    paths = (
        profiles.db_path(profile_id, root),
        profiles.picture_path(profile_id, root),
        recap_root / profile_id,
    )
    return any(p.exists() or p.is_symlink() for p in paths)


def _check_database(path: Path) -> None:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BackupError("The backup database is damaged.")
        unsafe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
            "OR lower(sql) LIKE '%create virtual table%'"
        ).fetchone()
        if unsafe or conn.execute("SELECT 1 FROM app_settings LIMIT 1").description is None:
            raise BackupError("The backup database has an unsupported schema.")
    finally:
        conn.close()


def restore(
    path: str | Path,
    profile_id: str | None = None,
    *,
    root: Path | None = None,
    recap_root: Path | None = None,
) -> profiles.Profile:
    """Validate and stage an archive, upgrade its database, then install a new profile."""
    root = root or profiles.profiles_dir()
    recap_root = recap_root or data_dir() / "recaps"
    root.mkdir(parents=True, exist_ok=True)
    installed = []
    completed = False
    try:
        with zipfile.ZipFile(path) as archive:
            members = _members(archive)
            manifest = _manifest(archive)
            pid = profiles.validate_id(
                profile_id if profile_id is not None else manifest.get("profile_id")
            )
            database = profiles.db_path(pid, root)
            picture = profiles.picture_path(pid, root)
            recaps = recap_root / pid
            if id_in_use(pid, root, recap_root):
                raise profiles.ProfileError(
                    "That profile id already exists or has retained files. Choose a new id."
                )
            with tempfile.TemporaryDirectory(prefix=".taste-restore-", dir=root) as temporary:
                stage = Path(temporary)
                actual = 0
                for name, member in members.items():
                    if member.is_dir():
                        continue
                    dest = stage / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, dest.open("xb") as out:
                        while chunk := source.read(1 << 20):
                            actual += len(chunk)
                            if actual > MAX_UNPACKED_BYTES:
                                raise BackupError("The archive's unpacked size is too large.")
                            out.write(chunk)
                _check_database(stage / "profile.db")
                conn = db.connect(stage / "profile.db")
                conn.close()
                # Each destination is created exclusively. Retained recaps always survive.
                if (stage / "recaps").exists():
                    recap_root.mkdir(parents=True, exist_ok=True)
                    recaps.mkdir()
                    installed.append(recaps)
                    shutil.copytree(stage / "recaps", recaps, dirs_exist_ok=True)
                if (stage / "picture.png").exists():
                    with picture.open("xb") as out:
                        installed.append(picture)
                        out.write((stage / "picture.png").read_bytes())
                # A hard link installs the completed database atomically without overwriting.
                os.link(stage / "profile.db", database)
                installed.append(database)
            result = profiles.Profile(pid, profiles.display_name(pid, root), database)
            completed = True
            return result
    except profiles.ProfileError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        zipfile.BadZipFile,
        RuntimeError,
    ) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError(
            "The backup could not be restored. It may be damaged or use an unsupported format."
        ) from None
    finally:
        # Only roll back paths created by this attempt, never another profile's files.
        if not completed:
            for item in reversed(installed):
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink(missing_ok=True)
