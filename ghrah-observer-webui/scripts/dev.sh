#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Mock mode: ./scripts/dev.sh --mock [--scenario demo] [--port 4113]
# Starts only the protocol-level mock server + SPA; the SPA connects to the
# mock via VITE_GHRAH_SUBJECT_WS_URL. No Core/Subject needed.
if [[ "${1:-}" == "--mock" ]]; then
  shift
  SCENARIO="demo"
  PORT="4113"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scenario) SCENARIO="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done

  echo "=== Starting Mock Server (scenario: $SCENARIO, port: $PORT) ==="
  (cd "$ROOT_DIR/packages/mock-server" && pnpm build >/dev/null && node dist/cli.js --port "$PORT" --scenario "$SCENARIO") &
  MOCK_PID=$!

  echo "=== Starting Observer SPA dev server (WS -> mock) ==="
  (cd "$ROOT_DIR/packages/observer-web" && VITE_GHRAH_SUBJECT_WS_URL="ws://localhost:$PORT/ws" pnpm dev) &
  SPA_PID=$!

  echo "=== Mock mode started ==="
  echo "  Mock server PID: $MOCK_PID (ws://localhost:$PORT/ws, scenario: $SCENARIO)"
  echo "  SPA PID: $SPA_PID"
  echo ""
  echo "Press Ctrl+C to stop all services."

  cleanup_mock() {
    echo "Stopping services..."
    kill $MOCK_PID $SPA_PID 2>/dev/null || true
    wait
    echo "All services stopped."
  }
  trap cleanup_mock EXIT INT TERM
  wait
fi

# Full-stack mode: the local dev session is owned by ghrah-toolchain.
# ``ghrah dev run`` 默认打开浏览器；保留本脚本旧语义（默认不打开，--open 才打开）。
# 端口等实例参数由 ghrah dev 的 distro 配置决定，不再从本脚本透传。
if ! command -v ghrah >/dev/null 2>&1; then
  echo "未找到 ghrah CLI：全栈启动已由 ghrah-toolchain 接管。" >&2
  echo "请先安装 ghrah-toolchain（uv tool install --editable <path>），" >&2
  echo "或使用 ./scripts/dev.sh --mock 仅起前端 demo。" >&2
  exit 1
fi

OPEN_BROWSER=0
NO_BROWSER=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --open) OPEN_BROWSER=1 ;;
    --no-browser) NO_BROWSER=1; ARGS+=("$arg") ;;
    *) ARGS+=("$arg") ;;
  esac
done

echo "=== Delegating full-stack startup to ghrah dev run ==="
echo "    (use --mock for frontend-only demo mode)"
if [[ "$OPEN_BROWSER" -eq 1 || "$NO_BROWSER" -eq 1 ]]; then
  exec ghrah dev run ${ARGS[@]+"${ARGS[@]}"}
else
  exec ghrah dev run --no-browser ${ARGS[@]+"${ARGS[@]}"}
fi
