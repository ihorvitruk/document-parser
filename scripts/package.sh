#!/usr/bin/env bash
# Builds the zip to send: the source code without .venv, node_modules, .git, .env
# (which holds API keys) and other generated files.
#
#   scripts/package.sh [output.zip]      (default: ../document-parser.zip)
set -euo pipefail

cd "$(dirname "$0")/.."
out="${1:-../document-parser.zip}"
rm -f "$out"

zip -r -q "$out" . \
  -x '.git/*' '.claude/*' '.env' \
  -x '*/.venv/*' '.venv/*' '*/node_modules/*' 'node_modules/*' 'frontend/dist/*' \
  -x '*/__pycache__/*' '*.pyc' '*/.pytest_cache/*' '.pytest_cache/*' '.coverage' 'htmlcov/*' \
  -x '*.egg-info/*' '*/*.egg-info/*' '.DS_Store' '*/.DS_Store' '.tmp-*/*'

echo "wrote $out ($(du -h "$out" | cut -f1), $(unzip -Z1 "$out" | wc -l | tr -d ' ') entries)"
if unzip -Z1 "$out" | grep -qE '(^|/)\.env$'; then
  echo "ERROR: .env is inside the archive" >&2
  exit 1
fi
