#!/usr/bin/env bash
# Run the full suite. No network, no third-party packages.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
