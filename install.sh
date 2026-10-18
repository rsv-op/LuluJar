#!/usr/bin/env bash
# LuluJar installer - prepares an optional virtual environment and checks
# that everything needed is in place.  LuluJar itself has no hard dependency
# on a package manager, so this script is a convenience, not a requirement.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.8+ is required." >&2
  exit 1
fi

echo "==> creating the virtual environment (.venv)"
"$PYTHON_BIN" -m venv .venv

echo "==> installing LuluJar in editable mode"
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -e .

cat <<'EOF'

Setup complete.

  Start the browser interface :  ./run.sh serve
  Extract an archive           :  ./run.sh extract your-file.rar -o ./output
  Check the available engines  :  ./run.sh doctor

EOF
