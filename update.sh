#!/usr/bin/env bash
# Pull the latest code (or a given tag) and re-sync dependencies.
#   ./update.sh          -> latest main
#   ./update.sh v0.2.0   -> a specific release tag
set -euo pipefail
cd "$(dirname "$0")"
git fetch --tags --quiet
if [ "${1:-}" != "" ]; then
  git checkout "$1"
else
  git checkout main --quiet
  git pull --ff-only
fi
./install.sh
echo "Now at: $(git describe --tags --always)"
