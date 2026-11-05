#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDS=()

cleanup() {
  echo ""
  echo "=== Stopping all services ==="
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "=== All services stopped ==="
}
trap cleanup EXIT INT TERM

echo "=== Building dependencies ==="
(cd "$ROOT_DIR/packages/protocol" && pnpm build)
(cd "$ROOT_DIR/packages/observer-core" && pnpm build)
(cd "$ROOT_DIR/packages/observer-web" && pnpm build)

echo "=== Building VSCode Extension (initial) ==="
(cd "$ROOT_DIR/vscode-extension" && pnpm build)

echo "=== Starting Core server ==="
(cd "$ROOT_DIR/../ghrah-core" && uv run python -m ghrah.core.server) &
PIDS+=($!)

echo "=== Waiting for Core server to be healthy ==="
for i in $(seq 1 30); do
  if curl -sf http://localhost:4111/health >/dev/null 2>&1; then
    echo "  Core server is healthy (attempt $i)"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "  WARNING: Core server not healthy after 30s, continuing anyway"
  fi
  sleep 1
done

echo "=== Starting Subject ==="
(cd "$ROOT_DIR/../ghrah-subject" && uv run ghrah-subject) &
PIDS+=($!)

echo "=== Starting Observer SPA dev server ==="
(cd "$ROOT_DIR/packages/observer-web" && pnpm dev) &
PIDS+=($!)

echo "=== Starting extension esbuild watch ==="
(cd "$ROOT_DIR/vscode-extension" && pnpm watch) &
PIDS+=($!)

echo ""
echo "=== All services started ==="
for pid in "${PIDS[@]}"; do
  echo "  PID: $pid"
done
echo ""
echo "To debug the extension in VSCode:"
echo "  1. Open this project in VSCode"
echo "  2. Set environment: GHRAH_DEV=1 GHRAH_PROJECT_ROOT=$ROOT_DIR/.."
echo "  3. Press F5 to launch Extension Development Host"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

wait