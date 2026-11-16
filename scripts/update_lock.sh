#!/usr/bin/env bash
# Regenerate requirements.lock (exact versions + hashes of every runtime dependency) for the Docker image's Python.
# requirements.txt keeps the allowed ranges; this file is what the image installs. Run it after changing
# requirements.txt or to pick up new releases, then run the tests and rebuild the image.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install --quiet uv
uv pip compile requirements.txt --python-version 3.11 --python-platform linux --generate-hashes --no-header \
  --quiet -o requirements.lock
echo "wrote requirements.lock ($(grep -c '^[A-Za-z]' requirements.lock) packages)"
