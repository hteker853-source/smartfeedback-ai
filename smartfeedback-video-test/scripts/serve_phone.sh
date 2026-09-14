#!/usr/bin/env bash
# Serve the video to a phone via a Cloudflare quick tunnel (no inbound ports).
set -e
cd "$(dirname "$0")/.."

PORT="${1:-8765}"
LOG=/tmp/sfvideo_cloudflared.log
rm -f "$LOG"

# 1) local-only HTTP server (bound to 127.0.0.1 -> nothing exposed publicly)
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory output \
  >/tmp/sfvideo_http.log 2>&1 &
HTTP_PID=$!

cleanup() { kill "$HTTP_PID" 2>/dev/null; }
trap cleanup EXIT

# 2) quick tunnel (outbound only)
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate 2>&1 | tee "$LOG" &
CF_PID=$!

echo "Waiting for tunnel URL..."
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  if [ -n "$URL" ]; then
    echo
    echo "======================================================"
    echo "  Telefondan açacağın adres:"
    echo "  $URL"
    echo "======================================================"
    echo
    echo "(Durdurmak için Ctrl+C)"
    wait "$CF_PID"
    exit 0
  fi
  sleep 1
done

echo "URL alınamadı. Log:"; cat "$LOG"; exit 1
