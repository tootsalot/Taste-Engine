"""Entry point for the packaged app (taste-engine.exe).

Double-clicked with no arguments, it starts the web app and opens the browser.
With arguments it behaves exactly like `python -m taste ...`.
"""

import sys

from taste.__main__ import main


def run() -> int:
    args = sys.argv[1:] or ["app"]
    try:
        return main(args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code and not sys.argv[1:]:
            print(exc.code if isinstance(exc.code, str) else "")
            input("Press Enter to close this window.")
        return code
    except Exception:
        import traceback

        traceback.print_exc()
        if not sys.argv[1:]:
            # A double-clicked console window would vanish before anyone could read the error.
            input("Taste Engine hit an error (shown above). Press Enter to close this window.")
        return 1


if __name__ == "__main__":
    sys.exit(run())
