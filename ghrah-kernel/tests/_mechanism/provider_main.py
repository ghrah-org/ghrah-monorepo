"""机制回归专用：消息往返替身的受管入口。"""

import argparse
import asyncio

from .mocks.message_provider import run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.fd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
