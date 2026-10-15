"""Desktop app (PySide6). Start with `python -m taste.desktop` or the packaged exe.

All data stays on this computer: one SQLite file per profile in the data folder,
API keys in Windows Credential Manager.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from taste import __version__

SHOW_MESSAGE = b"show"
# Held while the app is open, so the uninstaller can ask you to close it first.
# packaging/taste-engine.iss checks for the same name.
APP_MUTEX = "TasteEngineRunning-3C5D937C-337E-45F7-AB56-E60E3A811924"


def hold_app_mutex(name: str = APP_MUTEX) -> int | None:
    """Windows: create the named mutex and return its handle, or None elsewhere.

    The handle stays open until the process ends; Windows closes it then, even after a crash.
    """
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    return kernel32.CreateMutexW(None, False, name) or None


def instance_name() -> str:
    """One app per user account; other accounts on the same PC get their own."""
    user = "".join(c for c in getpass.getuser() if c.isalnum()) or "user"
    return f"taste-engine-{user}"


def notify_running_instance(name: str) -> bool:
    """If the app is already open, ask it to come to the front. True if it answered."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    socket.write(SHOW_MESSAGE)
    socket.flush()
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def listen_for_others(name: str, on_show: Callable[[], None]) -> QLocalServer:
    """Accept "show" requests from later launches of the app."""
    server = QLocalServer()
    QLocalServer.removeServer(name)  # clears a stale socket left by a crash (Unix only)
    server.listen(name)

    def accept() -> None:
        # The connection itself is the signal. Waiting for its bytes is unreliable on
        # Windows named pipes: they can arrive before readyRead is connected, and then
        # readyRead never fires.
        while (connection := server.nextPendingConnection()) is not None:
            connection.disconnected.connect(connection.deleteLater)
            connection.disconnectFromServer()
            on_show()

    server.newConnection.connect(accept)
    return server


def smoke_test(app: QApplication, out_path: str) -> int:
    """Build the real window and report what loaded. Used to check packaged builds."""
    from PySide6.QtGui import QFontDatabase, QImageReader

    from taste.desktop import theme
    from taste.desktop.main_window import MainWindow

    theme.apply(app)
    window = MainWindow()
    window.show()
    app.processEvents()
    families = QFontDatabase.families()
    formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
    report = {
        "version": __version__,
        "title": window.windowTitle(),
        "heading_font": theme.HEADING_FAMILY in families,
        "body_font": theme.BODY_FAMILY in families,
        "icon": not window.windowIcon().isNull(),
        "pages": window.stack.count(),
        "webp": "webp" in formats,  # MAL posters are often .webp
        "jpeg": "jpeg" in formats,
        "gif": "gif" in formats,  # some Last.fm covers are GIFs
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in report.items())
    window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv[1:]:
        print(f"taste-engine {__version__}")  # no-op in the windowed exe, which has no console
        return 0
    if "--delete-all-data" in argv[1:]:
        # Run by the uninstaller when "Also delete my profiles..." is ticked. No window.
        from taste import profiles

        return 0 if profiles.delete_all_data() is not None else 1

    from taste import profiles
    from taste.config import load_env
    from taste.desktop import theme
    from taste.desktop.main_window import MainWindow

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Taste Engine")
    app.setApplicationVersion(__version__)
    if "--smoke-test" in argv[1:]:
        return smoke_test(app, argv[argv.index("--smoke-test") + 1])
    name = instance_name()
    if notify_running_instance(name):
        return 0

    hold_app_mutex()
    load_env()
    theme.apply(app)
    profiles.migrate_legacy()
    window = MainWindow()
    server = listen_for_others(name, window.bring_to_front)
    window.show()
    code = app.exec()
    server.close()
    return code
