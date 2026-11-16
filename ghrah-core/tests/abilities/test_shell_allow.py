# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目脚本放行的纯函数契约，覆盖包装、复合命令与记忆边界。"""

import pytest

from ghrah.ability.permissions.bash_classify_frontend import classify_command_expression
from ghrah.ability.permissions.shell_allow import (
    build_shell_remember_options,
    extract_shell_class,
    irreversible_reason,
    is_builtin_shell_expression,
    is_shell_expression_allowed,
    matches_shell_allow,
    normalize_shell_command,
)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest",
        "uv run pytest -q",
        "uv run --directory . pytest",
        "uv --directory . run pytest -q",
        "uv run --with pytest pytest -q",
        "uv run ruff check .",
        "uv run ruff format .",
        "pnpm test",
        "pnpm t",
        "pnpm -r test",
        "pnpm --recursive test",
        "pnpm --filter foo test",
        "pnpm run test",
        "pnpm -w --dir . run test",
        "uv run pytest -q && uv run ruff check .",
    ],
)
def test_builtin_scripts(command: str) -> None:
    assert is_builtin_shell_expression(classify_command_expression(command))


@pytest.mark.parametrize(
    "command",
    [
        "python script.py",
        "python -m pytest",
        "node script.js",
        "pnpm build",
        "npm test",
        "npm run build",
        "pnpm run lint",
        "pnpm run t",
        "uv run python script.py",
        "git",
        "uv run ruff unknown .",
        "pnpm exec test",
        "pnpm dlx test",
        "pnpm --unknown test",
        "pnpm test -- --watch",
        "pnpm test && pnpm build",
        "uv run pytest > output.txt",
        "python script.py > output.txt",
        "bash -lc 'python script.py > output.txt'",
    ],
)
def test_other_scripts_are_not_builtin(command: str) -> None:
    expression = classify_command_expression(command)
    assert not is_builtin_shell_expression(expression)
    # 脚本入口与真正危险命令分开，由后续检查器决定是否审批。
    assert irreversible_reason(expression) is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /tmp/x",
        "sudo ls",
        "git clean -fd",
        "git reset --hard",
        "git push --force",
        "git push --force-with-lease",
        "npm publish",
        "docker rmi image",
        "docker rm container",
        "docker system prune",
        "kubectl delete pod x",
        "uv run rm -rf /tmp/x",
        "uv run --directory . rm -rf /tmp/x",
        "uv run --with foo rm -rf /tmp/x",
        "uv run --directory . --with foo rm -rf /tmp/x",
        "uv run --directory=. --with=foo rm -rf /tmp/x",
        "pnpm exec rm -rf /tmp/x",
        "bash -lc 'rm -rf /tmp/x'",
        "echo hi > /etc/passwd",
        "python script.py > /etc/passwd",
        "python script.py > /usr/bin/abc",
        "bash -lc 'python script.py > /etc/passwd'",
        "sh -c 'echo hi > /etc/passwd'",
        "uv run pytest && rm -rf /tmp/x",
    ],
)
def test_irreversible_check_precedes_memory_matching(command: str) -> None:
    expression = classify_command_expression(command)
    entries = [{"kind": "exact", "key": normalize_shell_command(command)}]
    assert irreversible_reason(expression) is not None
    assert not is_builtin_shell_expression(expression)
    # 项目记忆匹配本身只负责匹配，不重复执行危险判断。
    assert matches_shell_allow(expression, entries, command=command)
    assert build_shell_remember_options(expression, command=command) is not None


@pytest.mark.parametrize(
    ("command", "key"),
    [
        ("uv run pytest -q", "uv:run:pytest"),
        ("pnpm -r --filter foo lint", "pnpm:script:lint"),
        ("npm run build", "npm:script:build"),
        ("pnpm run t", "pnpm:script:t"),
        ("pnpm t", "pnpm:script:test"),
        ("npm run t", "npm:script:t"),
        ("pnpm run lint && pnpm --filter foo lint", "pnpm:script:lint"),
        ("python script.py", None),
        ("uv run python -c 'print(1)'", None),
        ("uv run python3.15 script.py", None),
        ("pnpm exec lint", None),
        ("pnpm lint && pnpm build", None),
    ],
)
def test_closed_class(command: str, key: str | None) -> None:
    assert extract_shell_class(classify_command_expression(command)) == key


def test_exact_normalization_keeps_arguments() -> None:
    assert normalize_shell_command(" pnpm   run lint ") == " pnpm   run lint "
    assert normalize_shell_command('python "a b.py"') == 'python "a b.py"'
    entries = [{"kind": "exact", "key": "pnpm run lint --fix"}]
    assert matches_shell_allow(
        classify_command_expression("pnpm run lint --fix"), entries, command="pnpm run lint --fix"
    )
    assert not matches_shell_allow(
        classify_command_expression("pnpm run lint"), entries, command="pnpm run lint"
    )
    assert normalize_shell_command("echo '&&' touch file.txt") != normalize_shell_command(
        "echo && touch file.txt"
    )
    assert normalize_shell_command("echo '$HOME'") != normalize_shell_command("echo $HOME")
    with pytest.raises(ValueError):
        normalize_shell_command(" ")
    assert normalize_shell_command("echo '") == "echo '"


def test_class_memory_requires_every_segment() -> None:
    entries = [{"kind": "class", "key": "pnpm:script:lint"}]
    assert matches_shell_allow(
        classify_command_expression("pnpm -r --filter foo lint"),
        entries,
        command="pnpm -r --filter foo lint",
    )
    assert not matches_shell_allow(
        classify_command_expression("uv run pytest && pnpm run lint"),
        entries,
        command="uv run pytest && pnpm run lint",
    )
    assert not matches_shell_allow(
        classify_command_expression("pnpm exec lint"), entries, command="pnpm exec lint"
    )
    assert not matches_shell_allow(
        classify_command_expression("pnpm lint && pnpm build"),
        entries,
        command="pnpm lint && pnpm build",
    )
    assert not matches_shell_allow(
        classify_command_expression("pnpm lint"), [], command="pnpm lint"
    )
    assert not matches_shell_allow(
        classify_command_expression("uv run python -c 'print(1)'"),
        [{"kind": "class", "key": "uv:run:python"}],
        command="uv run python -c 'print(1)'",
    )


def test_approval_options() -> None:
    options = build_shell_remember_options(
        classify_command_expression("pnpm run lint"), command="pnpm run lint"
    )
    assert options is not None
    assert options.exact_key == "pnpm run lint"
    assert options.class_key == "pnpm:script:lint"
    options = build_shell_remember_options(
        classify_command_expression("python script.py"), command="python script.py"
    )
    assert options is not None
    assert options.class_key is None
    assert options.class_label is None


@pytest.mark.parametrize(
    ("approved", "other"),
    [
        ("echo '&&' touch file.txt", "echo && touch file.txt"),
        ("echo '$HOME'", "echo $HOME"),
        ('echo "$HOME"', "echo '$HOME'"),
        ("echo '$(pwd)'", "echo $(pwd)"),
        ("echo hi \\\n", "echo hi \\"),
        ("pnpm run lint", " pnpm run lint "),
        ("bash -lc 'echo hi'", "bash -l -c 'echo hi'"),
    ],
)
def test_exact_memory_preserves_original_command(approved: str, other: str) -> None:
    expression = classify_command_expression(approved)
    options = build_shell_remember_options(expression, command=approved)
    assert options.exact_key == approved
    entries = [{"kind": "exact", "key": options.exact_key}]
    assert matches_shell_allow(expression, entries, command=approved)
    assert not matches_shell_allow(classify_command_expression(other), entries, command=other)


def test_empty_expression_does_not_match_class_memory() -> None:
    assert not matches_shell_allow(
        classify_command_expression("# comment"),
        [{"kind": "class", "key": "pnpm:script:lint"}],
        command="# comment",
    )


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("uv run pytest && pnpm run lint", True),
        ("pnpm run lint && uv run ruff check .", True),
        ("uv run pytest", True),
        ("pnpm run lint", True),
        ("pnpm run lint && pnpm build", False),
        ("uv run pytest && python script.py", False),
        ("uv run pytest && pnpm run lint > output.txt", False),
        ("# comment", False),
    ],
)
def test_combine_builtin_and_project_memory(command: str, allowed: bool) -> None:
    expression = classify_command_expression(command)
    entries = [{"kind": "class", "key": "pnpm:script:lint"}]
    assert is_shell_expression_allowed(expression, entries, command=command) is allowed


def test_exact_memory_covers_complete_expression() -> None:
    command = "uv run pytest && python script.py > output.txt"
    expression = classify_command_expression(command)
    assert is_shell_expression_allowed(
        expression, [{"kind": "exact", "key": command}], command=command
    )
    assert not is_shell_expression_allowed(
        expression, [{"kind": "exact", "key": "python script.py > output.txt"}], command=command
    )


def test_script_t_does_not_use_test_memory() -> None:
    command = "pnpm run t"
    assert not is_shell_expression_allowed(
        classify_command_expression(command),
        [{"kind": "class", "key": "pnpm:script:test"}],
        command=command,
    )


def test_unrecognized_uv_options_do_not_hide_inner_command() -> None:
    expression = classify_command_expression("uv run --unknown foo rm -rf /tmp/x")
    assert irreversible_reason(expression) == "Cannot resolve uv run command structure"
