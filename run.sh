#!/usr/bin/env bash
# LuluJar launcher - works on macOS and Linux with nothing but Python 3.
#
#   ./run.sh                      start the browser interface
#   ./run.sh extract file.rar     extract an archive
#   ./run.sh doctor               show the available engines
#   ./run.sh --help               every command
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "LuluJar needs Python 3.8 or newer." >&2
  echo "  macOS : brew install python3" >&2
  echo "  Debian: sudo apt install python3" >&2
  echo "  Windows: install Python from https://python.org" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "LuluJar needs Python 3.8 or newer (found $("$PYTHON_BIN" -V 2>&1))." >&2
  exit 1
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m lulujar "$@"
