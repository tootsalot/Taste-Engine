"""API key storage per profile.

Lookup order:
1. The OS credential store via `keyring` (Windows Credential Manager, macOS Keychain).
2. Where no credential store exists (some bare Linux setups), a per-profile file
   `data/profiles/<profile_id>.keys.json`. It's unencrypted, so the app warns about it.
3. Environment variables / .env, so cloud sessions and the Phase 1 setup keep working.

Values are never logged, never written to a profile's SQLite file, and never sent
back to the browser.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from taste.profiles import profiles_dir, validate_id

SERVICE = "taste-engine"
KEY_NAMES = ("MAL_CLIENT_ID", "LASTFM_API_KEY")
KEY_LABELS = {"MAL_CLIENT_ID": "MyAnimeList client ID", "LASTFM_API_KEY": "Last.fm API key"}


class SecretStoreError(RuntimeError):
    """The credential store failed. The message never includes a key value."""


def _default_backend() -> Any | None:
    """The OS keyring, or None if there isn't a usable one."""
    try:
        import keyring
        from keyring.backends import fail, null
    except ImportError:
        return None
    backend = keyring.get_keyring()
    if isinstance(backend, (fail.Keyring, null.Keyring)):
        return None
    return backend


class SecretStore:
    def __init__(self, root: Path | None = None, backend: Any | None = ...) -> None:
        """`backend` is anything with keyring's get/set/delete_password methods.

        Leave it out to detect the OS keyring. Pass None to force the file fallback.
        """
        self.root = root or profiles_dir()
        self.backend = _default_backend() if backend is ... else backend

    @property
    def secure(self) -> bool:
        """True if keys go to an OS credential store, False if they'd go to a plain file."""
        return self.backend is not None

    # -- public API ---------------------------------------------------------

    def get(self, profile_id: str, name: str) -> str | None:
        value, _ = self.get_with_source(profile_id, name)
        return value

    def get_with_source(self, profile_id: str, name: str) -> tuple[str | None, str | None]:
        """Return (value, where it came from): 'keyring', 'file', 'env', or (None, None)."""
        self._check(profile_id, name)
        stored = self._get_stored(profile_id, name)
        if stored:
            return stored, ("keyring" if self.secure else "file")
        env = os.environ.get(name, "").strip()
        if env:
            return env, "env"
        return None, None

    def has_stored(self, profile_id: str, name: str) -> bool:
        self._check(profile_id, name)
        return bool(self._get_stored(profile_id, name))

    def set(self, profile_id: str, name: str, value: str) -> None:
        self._check(profile_id, name)
        value = value.strip()
        if not value:
            raise SecretStoreError("The key is empty.")
        if self.secure:
            try:
                self.backend.set_password(SERVICE, _entry(profile_id, name), value)
            except Exception as exc:
                raise SecretStoreError(
                    f"Couldn't save to the credential store ({type(exc).__name__})."
                ) from None
        else:
            data = self._read_file(profile_id)
            data[name] = value
            self._write_file(profile_id, data)

    def delete(self, profile_id: str, name: str) -> None:
        self._check(profile_id, name)
        if self.secure:
            # Raises when nothing is stored, which is fine.
            with contextlib.suppress(Exception):
                self.backend.delete_password(SERVICE, _entry(profile_id, name))
        data = self._read_file(profile_id)
        if name in data:
            del data[name]
            self._write_file(profile_id, data)

    def delete_all(self, profile_id: str) -> None:
        validate_id(profile_id)
        for name in KEY_NAMES:
            self.delete(profile_id, name)
        self._file(profile_id).unlink(missing_ok=True)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _check(profile_id: str, name: str) -> None:
        validate_id(profile_id)
        if name not in KEY_NAMES:
            raise SecretStoreError(f"Unknown key name: {name}")

    def _get_stored(self, profile_id: str, name: str) -> str | None:
        if self.secure:
            try:
                return self.backend.get_password(SERVICE, _entry(profile_id, name))
            except Exception as exc:
                raise SecretStoreError(
                    f"Couldn't read the credential store ({type(exc).__name__})."
                ) from None
        return self._read_file(profile_id).get(name)

    def _file(self, profile_id: str) -> Path:
        return self.root / f"{profile_id}.keys.json"

    def _read_file(self, profile_id: str) -> dict[str, str]:
        path = self._file(profile_id)
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SecretStoreError(f"The key file for {profile_id!r} is unreadable.") from None

    def _write_file(self, profile_id: str, data: dict[str, str]) -> None:
        path = self._file(profile_id)
        if not data:
            path.unlink(missing_ok=True)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        # Create with owner-only permissions where the OS supports it.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)


def _entry(profile_id: str, name: str) -> str:
    return f"{profile_id}:{name}"
