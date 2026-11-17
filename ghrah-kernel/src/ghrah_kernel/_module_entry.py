# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Windows 受信模块启动桥；在同一服务进程内还原参数，不创建额外子进程。"""

import json
import runpy
import sys
from pathlib import Path


def main() -> None:
    """读取内核预先核定的参数并立即删除文件，之后执行原有模块入口。"""
    if len(sys.argv) != 2:
        raise ValueError("受信启动桥只接受一个参数文件路径")
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    if (
        not isinstance(data, dict)
        or set(data) != {"module", "arguments"}
        or not isinstance(data["module"], str)
        or not isinstance(data["arguments"], list)
        or not all(isinstance(value, str) for value in data["arguments"])
    ):
        raise ValueError("受信模块启动参数非法")
    sys.argv = [data["module"], *data["arguments"]]
    runpy.run_module(data["module"], run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
