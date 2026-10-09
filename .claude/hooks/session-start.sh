#!/bin/bash
# Installs Python dependencies at the start of a Claude Code cloud session.
# Does nothing on a local machine, where you manage your own virtual environment.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Fall back to a user install if the system Python refuses a global one.
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt \
  || python3 -m pip install --quiet --disable-pip-version-check --user -r requirements.txt
