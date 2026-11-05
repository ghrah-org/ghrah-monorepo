#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Building dependencies ==="
(cd "$ROOT_DIR/packages/protocol" && pnpm build)
(cd "$ROOT_DIR/packages/observer-core" && pnpm build)

echo "=== Type-checking VSCode Extension ==="
(cd "$ROOT_DIR/vscode-extension" && pnpm type-check)

echo "=== Linting VSCode Extension ==="
(cd "$ROOT_DIR/vscode-extension" && pnpm lint)

echo "=== Running extension tests ==="
# TODO: add vitest or extension test runner when tests are written
# (cd "$ROOT_DIR/vscode-extension" && pnpm test)

echo "=== All checks passed ==="