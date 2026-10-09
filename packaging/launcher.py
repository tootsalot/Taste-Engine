"""Entry point for the packaged app (taste-engine.exe): opens the desktop window."""

import sys

from taste.desktop import main

if __name__ == "__main__":
    sys.exit(main())
