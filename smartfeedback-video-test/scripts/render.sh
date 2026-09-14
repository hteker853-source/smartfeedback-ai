#!/usr/bin/env bash
# Full render: synthesize audio + render frames -> MP4 (H.264 + AAC)
set -e
cd "$(dirname "$0")/.."
exec python3 -m src.render "$@"
