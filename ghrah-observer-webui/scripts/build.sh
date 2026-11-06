#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Building @ghrah/protocol ==="
(cd "$ROOT_DIR/packages/protocol" && pnpm build)

echo "=== Building @ghrah/plugin-registry ==="
(cd "$ROOT_DIR/packages/plugin-registry" && pnpm build)

echo "=== Building @ghrah/observer-core ==="
(cd "$ROOT_DIR/packages/observer-core" && pnpm build)

echo "=== Building @ghrah/mock-server ==="
(cd "$ROOT_DIR/packages/mock-server" && pnpm build)

echo "=== Building @ghrah/observer-web (SPA) ==="
(cd "$ROOT_DIR/packages/observer-web" && pnpm build)

echo "=== Building VSCode Extension ==="
(cd "$ROOT_DIR/vscode-extension" && pnpm build)

echo "=== Packaging VSCode Extension ==="
(cd "$ROOT_DIR/vscode-extension" && npx vsce package --no-dependencies)

echo "=== Build complete ==="
