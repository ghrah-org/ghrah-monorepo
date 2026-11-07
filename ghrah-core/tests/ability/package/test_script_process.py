# SPDX-License-Identifier: Apache-2.0
"""真实双向管道验证：预算覆盖 stdin、输出超限与取消均能回收子进程。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ghrah.ability.scripts.process import run_script_process, script_environment


@pytest.mark.parametrize("ending", ["timeout", "event", "task"])
async def test_blocked_stdin_is_bounded_and_child_is_reaped(tmp_path: Path, ending: str):
    script = tmp_path / "blocked.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    spawned = asyncio.Event()
    children = []
    cancel = asyncio.Event()

    async def spawn(*args, **kwargs):
        child = await asyncio.create_subprocess_exec(*args, **kwargs)
        children.append(child)
        spawned.set()
        return child

    before = asyncio.all_tasks()
    task = asyncio.create_task(
        run_script_process(
            script_path=script,
            cwd=tmp_path,
            env=script_environment(),
            envelope=b"x" * (512 * 1024),
            deadline_s=0.15 if ending == "timeout" else 30,
            grace_s=0.1,
            spawner=spawn,
            cancel=cancel,
        )
    )
    try:
        await asyncio.wait_for(spawned.wait(), 3)
        if ending == "event":
            cancel.set()
        elif ending == "task":
            task.cancel()
        if ending == "task":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        else:
            result = await asyncio.wait_for(task, 5)
            assert result.kind == ("timeout" if ending == "timeout" else "cancelled")
        assert children and all(child.returncode is not None for child in children)
        assert not {t for t in asyncio.all_tasks() - before if not t.done()}
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()


@pytest.mark.parametrize("scenario", ["duplex", "overflow", "early_exit"])
async def test_output_is_drained_while_input_is_backpressured(tmp_path: Path, scenario: str):
    scripts = {
        "duplex": (
            "import sys,json\n"
            'prefix = \'{"ok":true,"data":{"padding":"\'\n'
            "sys.stdout.write(prefix + 'x' * (512 * 1024)); sys.stdout.flush()\n"
            "sys.stderr.write('x' * (256 * 1024)); sys.stderr.flush()\n"
            "request=json.load(sys.stdin)\n"
            "sys.stdout.write('\\\",\\\"bytes\\\":' + str(len(request['payload'])) + '}}')\n"
        ),
        "overflow": (
            "import sys,time\n"
            "sys.stdout.write('x' * (5 * 1024 * 1024));sys.stdout.flush()\n"
            "time.sleep(30)\n"
        ),
        "early_exit": "import sys\nprint('invalid reply')\nsys.exit(1)\n",
    }
    script = tmp_path / "exchange.py"
    script.write_text(scripts[scenario], encoding="utf-8")
    children = []

    async def spawn(*args, **kwargs):
        child = await asyncio.create_subprocess_exec(*args, **kwargs)
        children.append(child)
        return child

    envelope = b'{"payload":"' + b"x" * (512 * 1024) + b'"}'
    try:
        result = await asyncio.wait_for(
            run_script_process(
                script_path=script,
                cwd=tmp_path,
                env=script_environment(),
                envelope=envelope,
                deadline_s=3,
                grace_s=0.1,
                spawner=spawn,
            ),
            6,
        )
        assert all(child.returncode is not None for child in children)
        if scenario == "duplex":
            assert result.ok and result.data["bytes"] == 512 * 1024
            assert len(result.data["padding"]) == 512 * 1024
            assert len(result.stderr) == 64 * 1024
        else:
            assert not result.ok
            assert result.kind == ("result_too_large" if scenario == "overflow" else "internal")
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()
