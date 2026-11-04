"""独立 Kernel 宿主入口；只解释系统管理会话，不装配业务场景。"""

import argparse
import asyncio

from .kernel_main import run_kernel


def main() -> int:
    parser = argparse.ArgumentParser(description="ghrah 应用微内核宿主")
    subcommands = parser.add_subparsers(dest="command", required=True)
    kernel = subcommands.add_parser("_kernel", help=argparse.SUPPRESS)
    for name in ("control-fd", "observer-fd", "peer-fd"):
        kernel.add_argument(f"--{name}", type=int, required=True)
    for name in ("control-token", "observer-token", "peer-token", "report"):
        kernel.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    if args.command == "_kernel":
        asyncio.run(
            run_kernel(
                args.control_fd,
                args.control_token,
                args.observer_fd,
                args.observer_token,
                args.report,
                args.peer_fd,
                args.peer_token,
            )
        )
        return 0
    parser.error("未知子命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
