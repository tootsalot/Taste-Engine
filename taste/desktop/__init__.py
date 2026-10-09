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
        connection = server.nextPendingConnection()
        if connection is None:
            return
        connection.readyRead.connect(lambda: on_show())
        connection.disconnected.connect(connection.deleteLater)

    server.newConnection.connect(accept)
    return server


def smoke_test(app: QApplication, out_path: str) -> int:
    """Build the real window and report what loaded. Used to check packaged builds."""
    from PySide6.QtGui import QFontDatabase

    from taste.desktop import theme
    from taste.desktop.main_window import MainWindow

    theme.apply(app)
    window = MainWindow()
    window.show()
    app.processEvents()
    families = QFontDatabase.families()
    report = {
        "version": __version__,
        "title": window.windowTitle(),
        "syne": "Syne" in families,
        "manrope": "Manrope" in families,
        "icon": not window.windowIcon().isNull(),
        "pages": window.stack.count(),
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

    load_env()
    theme.apply(app)
    profiles.migrate_legacy()
    window = MainWindow()
    server = listen_for_others(name, window.bring_to_front)
    window.show()
    code = app.exec()
    server.close()
    return code
