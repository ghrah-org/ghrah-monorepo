# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

# 启动本地开发服务，并带上启动期能力授权策略。
#
# Core 的 builtin 模式要求「启动授权策略」把 manifest 声明的能力包转成授权事实；
# 该策略通过 GHRAH_SUBJECT_ABILITY_GRANT_POLICY 注入（JSON 文本）。
# ghrah dev run 没有环境变量透传参数，因此这里显式设置后再调用。
#
# 用法：
#   ./scripts/dev-run.ps1
#   ./scripts/dev-run.ps1 -NoBrowser

[CmdletBinding()]
param(
    [string]$Distro = 'local',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

# mode=merge：在 manifest 已声明的能力之上并入这份规则。
#
# 只列具体成员。整包授予会把 conversation/end_task 一起授出，而这两个是
# 循环控制成员：由 Agent 自身提供、manifest 用 type: 声明。整包再授一次
# 就会重复注册，spawn 直接失败。
$rules = @(
    [ordered]@{ package = 'ghrah.core'; operation = 'list_abilities' },
    [ordered]@{ package = 'ghrah.fs'; operation = 'read_file' },
    [ordered]@{ package = 'ghrah.fs'; operation = 'list_directory' },
    [ordered]@{ package = 'ghrah.shell'; operation = 'execute_command' }
)
$policyObject = [ordered]@{ mode = 'merge'; rules = $rules }
$policy = $policyObject | ConvertTo-Json -Compress -Depth 5

$env:GHRAH_SUBJECT_ABILITY_GRANT_POLICY = $policy
Write-Host "GHRAH_SUBJECT_ABILITY_GRANT_POLICY=$policy" -ForegroundColor DarkGray

$repoRoot = Split-Path -Parent $PSScriptRoot
$toolchain = Join-Path $repoRoot '..\ghrah-toolchain'
$marker = Join-Path $toolchain 'pyproject.toml'
if (-not (Test-Path $marker)) {
    throw "找不到 ghrah-toolchain：$toolchain"
}

$arguments = @('dev', 'run')
if ($Distro) {
    $arguments += '--distro'
    $arguments += $Distro
}
if ($NoBrowser) {
    $arguments += '--no-browser'
}

Push-Location $toolchain
try {
    & ghrah @arguments
}
finally {
    Pop-Location
}