# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""命令安全模块测试：CommandSafetyChecker 和 CommandApprovalHook。"""

from __future__ import annotations

from typing import Any

import pytest
from _helpers import make_call, make_services

from ghrah.abilities.builtin.command_safety import (
    CommandApprovalHook,
    CommandSafetyCategory,
    CommandSafetyChecker,
)
from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.invocation import OperationCall


def _make_context(**overrides: Any) -> tuple[OperationCall, ExecutionServices]:
    """创建测试用 (OperationCall, ExecutionServices)。"""
    tool_args = overrides.pop("tool_args", None)
    member = overrides.pop("member", "")
    return make_call(member=member, arguments=tool_args), make_services(**overrides)


# ── CommandSafetyChecker.parse_command 测试 ──


class TestParseCommand:
    def test_simple_command(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("ls -la")
        assert base == "ls"
        assert sub is None

    def test_command_with_subcommand(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("git status")
        assert base == "git"
        assert sub == "status"

    def test_command_with_subcommand_and_flags(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("git log --oneline -10")
        assert base == "git"
        assert sub == "log"

    def test_dangerous_command_with_flags(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("rm -rf /")
        assert base == "rm"
        assert sub is None

    def test_absolute_path_command(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("/usr/bin/python script.py")
        assert base == "python"
        assert sub is None

    def test_npm_subcommand(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("npm run build")
        assert base == "npm"
        assert sub == "run"

    def test_empty_command(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("")
        assert base == ""
        assert sub is None

    def test_whitespace_only_command(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("   ")
        assert base == ""
        assert sub is None

    def test_single_command_no_args(self) -> None:
        base, sub = CommandSafetyChecker.parse_command("python")
        assert base == "python"
        assert sub is None

    def test_quoted_command(self) -> None:
        base, sub = CommandSafetyChecker.parse_command('echo "hello world"')
        assert base == "echo"
        assert sub == "hello world"


# ── CommandSafetyChecker.check_command 测试 ──


class TestCheckCommandSafe:
    def test_safe_command_ls(self) -> None:
        checker = CommandSafetyChecker()
        verdict = checker.check_command("ls -la")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.base_command == "ls"

    def test_safe_command_cat(self) -> None:
        verdict = CommandSafetyChecker().check_command("cat /etc/hosts")
        assert verdict.category == CommandSafetyCategory.SAFE

    def test_safe_command_pytest(self) -> None:
        verdict = CommandSafetyChecker().check_command("pytest tests/ -v")
        assert verdict.category == CommandSafetyCategory.SAFE

    def test_safe_command_git_status(self) -> None:
        verdict = CommandSafetyChecker().check_command("git status")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.sub_command == "status"

    def test_safe_command_git_diff(self) -> None:
        verdict = CommandSafetyChecker().check_command("git diff HEAD~1")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.sub_command == "diff"

    def test_safe_command_npm_test(self) -> None:
        verdict = CommandSafetyChecker().check_command("npm test")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.sub_command == "test"

    def test_safe_command_npm_run(self) -> None:
        verdict = CommandSafetyChecker().check_command("npm run build")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.sub_command == "run"

    def test_safe_command_pip_list(self) -> None:
        verdict = CommandSafetyChecker().check_command("pip list")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.sub_command == "list"

    def test_safe_command_cargo_check(self) -> None:
        verdict = CommandSafetyChecker().check_command("cargo check")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.sub_command == "check"


class TestCheckCommandDangerous:
    def test_dangerous_command_rm(self) -> None:
        verdict = CommandSafetyChecker().check_command("rm -rf /")
        assert verdict.category == CommandSafetyCategory.DANGEROUS
        assert verdict.base_command == "rm"

    def test_dangerous_command_chmod(self) -> None:
        verdict = CommandSafetyChecker().check_command("chmod 777 /etc/passwd")
        assert verdict.category == CommandSafetyCategory.DANGEROUS

    def test_dangerous_command_sudo(self) -> None:
        verdict = CommandSafetyChecker().check_command("sudo apt install foo")
        assert verdict.category == CommandSafetyCategory.DANGEROUS
        assert verdict.base_command == "sudo"

    def test_dangerous_command_kill(self) -> None:
        verdict = CommandSafetyChecker().check_command("kill -9 1234")
        assert verdict.category == CommandSafetyCategory.DANGEROUS

    def test_dangerous_subcommand_git_clean(self) -> None:
        verdict = CommandSafetyChecker().check_command("git clean -fdx")
        assert verdict.category == CommandSafetyCategory.DANGEROUS
        assert verdict.base_command == "git"
        assert verdict.sub_command == "clean"

    def test_dangerous_subcommand_npm_publish(self) -> None:
        verdict = CommandSafetyChecker().check_command("npm publish")
        assert verdict.category == CommandSafetyCategory.DANGEROUS
        assert verdict.sub_command == "publish"

    def test_dangerous_subcommand_docker_rmi(self) -> None:
        verdict = CommandSafetyChecker().check_command("docker rmi myimage")
        assert verdict.category == CommandSafetyCategory.DANGEROUS
        assert verdict.sub_command == "rmi"

    def test_base_command_dangerous_overrides_subcommand(self) -> None:
        verdict = CommandSafetyChecker().check_command("rm -rf /tmp")
        assert verdict.category == CommandSafetyCategory.DANGEROUS
        assert verdict.base_command == "rm"


class TestCheckCommandRequireHitl:
    def test_unknown_command_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("curl https://example.com")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL

    def test_git_commit_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("git commit -m 'fix'")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL
        assert verdict.sub_command == "commit"

    def test_git_push_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("git push origin main")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL

    def test_npm_install_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("npm install lodash")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL
        assert verdict.sub_command == "install"

    def test_python_script_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("python script.py")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL
        assert verdict.base_command == "python"

    def test_bash_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("bash -c 'echo hello'")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL

    def test_git_no_args_requires_approval(self) -> None:
        verdict = CommandSafetyChecker().check_command("git")
        assert verdict.category == CommandSafetyCategory.REQUIRE_HITL


class TestCheckCommandEdgeCases:
    def test_empty_command_dangerous(self) -> None:
        verdict = CommandSafetyChecker().check_command("")
        assert verdict.category == CommandSafetyCategory.DANGEROUS

    def test_whitespace_command_dangerous(self) -> None:
        verdict = CommandSafetyChecker().check_command("   ")
        assert verdict.category == CommandSafetyCategory.DANGEROUS

    def test_require_approval_false(self) -> None:
        checker = CommandSafetyChecker(require_approval=False)
        verdict = checker.check_command("curl https://example.com")
        assert verdict.category == CommandSafetyCategory.SAFE

    def test_custom_safe_commands(self) -> None:
        checker = CommandSafetyChecker(safe_commands={"mytool"})
        verdict = checker.check_command("mytool --version")
        assert verdict.category == CommandSafetyCategory.SAFE

    def test_custom_dangerous_commands(self) -> None:
        checker = CommandSafetyChecker(dangerous_commands={"mydanger"})
        verdict = checker.check_command("mydanger --flag")
        assert verdict.category == CommandSafetyCategory.DANGEROUS

    def test_custom_sub_commands(self) -> None:
        checker = CommandSafetyChecker(
            safe_sub_commands={"mytool": {"info", "list"}},
            dangerous_sub_commands={"mytool": {"nuke"}},
        )
        assert checker.check_command("mytool info").category == CommandSafetyCategory.SAFE
        assert checker.check_command("mytool nuke").category == CommandSafetyCategory.DANGEROUS
        assert checker.check_command("mytool deploy").category == CommandSafetyCategory.REQUIRE_HITL

    def test_path_command_strips_to_basename(self) -> None:
        verdict = CommandSafetyChecker().check_command("/usr/bin/ls")
        assert verdict.category == CommandSafetyCategory.SAFE
        assert verdict.base_command == "ls"


# ── CommandApprovalHook 测试 ──


class TestCommandApprovalHook:
    @pytest.mark.asyncio
    async def test_should_trigger_for_execute_command(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(member="execute_command")
        assert await hook.should_trigger(call, services) is True

    @pytest.mark.asyncio
    async def test_should_not_trigger_for_other_ability(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(member="write_file")
        assert await hook.should_trigger(call, services) is False

    @pytest.mark.asyncio
    async def test_safe_command_passes(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(
            member="execute_command",
            tool_args={"command": "ls -la"},
        )
        result = await hook.execute(call, services, None)
        assert result.should_continue is True
        assert result.requires_hitl is False

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(
            member="execute_command",
            tool_args={"command": "rm -rf /"},
        )
        result = await hook.execute(call, services, None)
        assert result.should_continue is False
        assert result.requires_hitl is False

    @pytest.mark.asyncio
    async def test_unknown_command_requires_hitl(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(
            member="execute_command",
            tool_args={"command": "curl https://example.com"},
        )
        result = await hook.execute(call, services, None)
        assert result.should_continue is False
        assert result.requires_hitl is True

    @pytest.mark.asyncio
    async def test_git_status_passes(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(
            member="execute_command",
            tool_args={"command": "git status"},
        )
        result = await hook.execute(call, services, None)
        assert result.should_continue is True

    @pytest.mark.asyncio
    async def test_git_clean_blocked(self) -> None:
        hook = CommandApprovalHook(CommandSafetyChecker())
        call, services = _make_context(
            member="execute_command",
            tool_args={"command": "git clean -fdx"},
        )
        result = await hook.execute(call, services, None)
        assert result.should_continue is False
        assert result.requires_hitl is False


class TestExtendSafeLists:
    """部署方 safe 白名单扩展（配置驱动，只增不减）。"""

    def test_extra_base_command_becomes_safe(self) -> None:
        checker = CommandSafetyChecker(require_approval=True)
        verdict_before = checker.check_command("pnpm build")
        assert verdict_before.category == CommandSafetyCategory.REQUIRE_HITL

        checker.extend_safe_lists(extra_commands={"pnpm", "make"})
        assert checker.check_command("pnpm build").category == CommandSafetyCategory.SAFE
        assert checker.check_command("make").category == CommandSafetyCategory.SAFE

    def test_extra_sub_commands_become_safe(self) -> None:
        checker = CommandSafetyChecker(require_approval=True)
        checker.extend_safe_lists(extra_sub_commands={"pnpm": {"test", "type-check"}})
        assert checker.check_command("pnpm test").category == CommandSafetyCategory.SAFE
        assert (
            checker.check_command("pnpm exec biome check").category
            == CommandSafetyCategory.REQUIRE_HITL  # 未列入的子命令仍需审批
        )

    def test_extension_does_not_touch_dangerous(self) -> None:
        checker = CommandSafetyChecker(require_approval=True)
        checker.extend_safe_lists(extra_commands={"rm"}, extra_sub_commands={"git": {"clean"}})
        # 只增不减：扩展不能把 DANGEROUS 名单中的命令翻成 SAFE
        assert checker.check_command("rm -rf /").category == CommandSafetyCategory.DANGEROUS
        assert checker.check_command("git clean -fdx").category == CommandSafetyCategory.DANGEROUS

    def test_builtin_safe_list_preserved_after_extension(self) -> None:
        checker = CommandSafetyChecker(require_approval=True)
        checker.extend_safe_lists(extra_commands={"pnpm"})
        assert checker.check_command("git status").category == CommandSafetyCategory.SAFE


class TestDeclaredCommandScope:
    """声明命令范围：allowed_commands 自动批准、denied_commands 硬拒绝。"""

    def test_allowed_commands_auto_approve_two_segment(self) -> None:
        checker = CommandSafetyChecker(require_approval=True, allowed_commands=("git status",))
        assert checker.check_command("git status -s").category == CommandSafetyCategory.SAFE
        assert (
            checker.check_command("git commit -m x").category == CommandSafetyCategory.REQUIRE_HITL
        )

    def test_dangerous_command_vetoes_allowed_scope(self) -> None:
        checker = CommandSafetyChecker(require_approval=True, allowed_commands=("rm",))
        assert checker.check_command("rm -rf /").category == CommandSafetyCategory.DANGEROUS

    def test_denied_commands_hard_deny_even_within_allowed(self) -> None:
        checker = CommandSafetyChecker(
            require_approval=True,
            allowed_commands=("git",),
            denied_commands=("git push",),
        )
        assert checker.check_command("git status").category == CommandSafetyCategory.SAFE
        assert checker.check_command("git push origin main").category == (
            CommandSafetyCategory.DANGEROUS
        )

    def test_out_of_scope_denies_when_approval_disabled(self) -> None:
        checker = CommandSafetyChecker(
            require_approval=False,
            allowed_commands=("git status",),
            deny_out_of_scope=True,
        )
        assert checker.check_command("npm install").category == CommandSafetyCategory.DANGEROUS
        assert checker.check_command("git status").category == CommandSafetyCategory.SAFE

    def test_out_of_scope_allows_when_no_scope_declared(self) -> None:
        checker = CommandSafetyChecker(require_approval=False, deny_out_of_scope=True)
        assert checker.check_command("npm install").category == CommandSafetyCategory.SAFE

    def test_empty_declared_scope_denies_non_safe_commands(self) -> None:
        checker = CommandSafetyChecker(
            require_approval=False, allowed_commands=(), deny_out_of_scope=True
        )
        assert checker.check_command("npm install").category == CommandSafetyCategory.DANGEROUS
        # 内置安全列表仍是部署级基线
        assert checker.check_command("git status").category == CommandSafetyCategory.SAFE

    async def test_hook_maps_denied_scope_to_stop(self) -> None:
        checker = CommandSafetyChecker(require_approval=True, denied_commands=("git push",))
        hook = CommandApprovalHook(checker)
        call, services = _make_context(
            member="execute_command",
            tool_args={"command": "git push origin main"},
        )
        result = await hook.execute(call, services, None)
        assert result.should_continue is False
        assert result.requires_hitl is False


@pytest.mark.parametrize(
    ("allowed", "command"),
    [
        ("git", "git clean -fd"),
        ("docker", "docker rmi image"),
        ("kubectl", "kubectl delete pod test"),
        ("npm", "npm publish"),
    ],
)
def test_declared_allow_cannot_override_dangerous_subcommand(allowed, command):
    checker = CommandSafetyChecker(require_approval=False, allowed_commands=(allowed,))
    assert checker.check_command(command).category is CommandSafetyCategory.DANGEROUS


@pytest.mark.parametrize(
    "command",
    [
        "git -C sub clean -fd",
        "git -Csub clean -fd",
        "git --git-dir=sub clean -fd",
        "docker -H host rmi image",
        "kubectl -n default delete pod x",
        "kubectl --namespace=default delete pod x",
        "npm --prefix sub publish",
        "git reset --hard",
        "git push --force",
        "git push --force-with-lease",
        "docker rm -f image",
        "docker system prune",
        "git --unknown value clean -fd",
        "echo ok; git clean -fd",
        "echo $(git clean -fd)",
    ],
)
def test_leading_options_and_shell_composition_cannot_bypass_dangerous_checks(command):
    checker = CommandSafetyChecker(
        allowed_commands=("git", "docker", "kubectl", "npm", "echo"),
        denied_commands=("git clean",),
        require_approval=False,
    )
    assert checker.check_command(command).category is CommandSafetyCategory.DANGEROUS


@pytest.mark.parametrize("command", ["git -C sub status", "git --no-pager status"])
def test_command_and_scope_share_leading_option_parsing(command):
    checker = CommandSafetyChecker(allowed_commands=("git status",), require_approval=True)
    assert checker.check_command(command).category is CommandSafetyCategory.SAFE
    checker = CommandSafetyChecker(allowed_commands=("git",), denied_commands=("git status",))
    assert checker.check_command(command).category is CommandSafetyCategory.DANGEROUS
