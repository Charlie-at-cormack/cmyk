#!/usr/bin/env bash
# Create/refresh the virtualenv and install dependencies. Safe to re-run after `git pull`.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.12+ (e.g. 'brew install python@3.12')." >&2
  exit 1
fi
"$PY" - <<'PYCHK'
import sys
if sys.version_info < (3, 11):
    sys.exit("Python 3.11+ required (found %s)" % sys.version.split()[0])
PYCHK

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
if [ -f requirements.lock ]; then
  .venv/bin/python -m pip install --quiet -r requirements.lock
else
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
echo "CMYK installed. Start it with ./run.sh"
