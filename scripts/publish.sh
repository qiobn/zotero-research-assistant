#!/usr/bin/env bash
#
# Build and publish the package to PyPI.
#
# Credentials are NEVER passed on the command line. twine reads them from, in
# order of precedence:
#   1. TWINE_USERNAME / TWINE_PASSWORD environment variables, or
#   2. ~/.pypirc  (recommended — keep it chmod 600)
#
# Example ~/.pypirc:
#   [pypi]
#     username = __token__
#     password = pypi-XXXXXXXX...           # your PyPI API token
#
#   [testpypi]
#     username = __token__
#     password = pypi-YYYYYYYY...
#
# Usage:
#   scripts/publish.sh              # build + check + upload to PyPI
#   scripts/publish.sh --test       # upload to TestPyPI instead
#   scripts/publish.sh --check-only # build + twine check, no upload
#
set -euo pipefail

cd "$(dirname "$0")/.."

REPO="pypi"
DO_UPLOAD=1
for arg in "$@"; do
  case "$arg" in
    --test) REPO="testpypi" ;;
    --check-only) DO_UPLOAD=0 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Prefer the project venv if present, else fall back to whatever python is on PATH.
# Check both POSIX (.venv/bin) and Windows (.venv/Scripts) venv layouts.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
else
  PY="$(command -v python3 || command -v python)"
fi
echo "Using interpreter: $PY"

VERSION="$("$PY" - <<'PYEOF'
import tomllib, pathlib
data = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
PYEOF
)"
echo "Package version: $VERSION"

echo "==> Cleaning previous build artifacts"
rm -rf dist build

echo "==> Building sdist + wheel"
"$PY" -m build

echo "==> Validating metadata (twine check)"
"$PY" -m twine check dist/*

if [ "$DO_UPLOAD" -eq 0 ]; then
  echo "==> --check-only: skipping upload. Artifacts in ./dist:"
  ls -1 dist/
  exit 0
fi

# Preflight: make sure credentials are discoverable so we fail fast with a
# helpful message instead of an opaque 403 from PyPI.
if [ -z "${TWINE_PASSWORD:-}" ] && [ ! -f "$HOME/.pypirc" ]; then
  cat >&2 <<MSG
ERROR: No credentials found.
  - Set TWINE_USERNAME=__token__ and TWINE_PASSWORD=pypi-... in your environment, OR
  - Create ~/.pypirc (chmod 600) with your PyPI API token (see header of this script).
Get a token at: https://pypi.org/manage/account/token/
MSG
  exit 1
fi

echo "==> Uploading $VERSION to $REPO"
"$PY" -m twine upload --repository "$REPO" --skip-existing dist/*

echo "==> Done. View at:"
if [ "$REPO" = "testpypi" ]; then
  echo "   https://test.pypi.org/project/zotero-research-assistant/$VERSION/"
else
  echo "   https://pypi.org/project/zotero-research-assistant/$VERSION/"
fi
