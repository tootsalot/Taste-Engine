#!/bin/bash
# Sets up a virtual environment with requirements.txt at the start of a Claude Code
# cloud session. Does nothing on a local machine, where you manage your own venv.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Qt (the desktop app and its tests) needs these system libraries, even offscreen.
if ! ldconfig -p 2>/dev/null | grep -q libEGL.so.1; then
  (apt-get install -y -qq libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3 \
    || (apt-get update -qq && apt-get install -y -qq libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3)) \
    >/dev/null 2>&1 || echo "Couldn't install Qt system libraries; desktop tests may fail." >&2
fi
echo 'export QT_QPA_PLATFORM=offscreen' >> "${CLAUDE_ENV_FILE:-/dev/null}"

# A venv avoids clashing with Debian-managed system packages (pip can't replace them).
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

# Put the venv first on PATH for the rest of the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$PWD/.venv\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
