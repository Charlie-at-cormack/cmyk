#!/usr/bin/env bash
# Start the local CMYK service (127.0.0.1 only) and open the browser.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || ./install.sh
exec .venv/bin/python -m cmyk "$@"
