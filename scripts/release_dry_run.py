#!/usr/bin/env python3
"""构建并审计 ghrah 的全部交付产物（含内部包），不执行发布操作。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGES = (
    "ghrah-core",
    "ghrah-plugin",
    "ghrah-protocol",
    "ghrah-subject",
    "ghrah-taskstore",
    "ghrah-scheme",
)
NPM_PACKAGES = {
    "@ghrah/protocol": ROOT / "ghrah-observer-webui" / "packages" / "protocol",
    "@ghrah/observer-core": ROOT / "ghrah-observer-webui" / "packages" / "observer-core",
}
FORBIDDEN_PARTS = {
    ".kilo",
    "plans",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}
FORBIDDEN_SDIST_ROOT_FILES = {
    ".python-version",
    "build.sh",
    "main.py",
}


def _run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def _require_empty_output_dir(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(f"输出目录必须为空：{path}")
    path.mkdir(parents=True, exist_ok=True)


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl" or path.suffix == ".vsix":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def _assert_no_forbidden_parts(path: Path, names: list[str]) -> None:
    for name in names:
        parts = set(Path(name).parts)
        overlap = parts & FORBIDDEN_PARTS
        if overlap:
            raise RuntimeError(f"{path.name} 包含禁止路径 {sorted(overlap)}：{name}")


def _read_tgz_member(path: Path, member: str) -> bytes:
    with tarfile.open(path, "r:gz") as archive:
        extracted = archive.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"{path.name} 缺少文件：{member}")
        return extracted.read()


def _audit_wheel(path: Path) -> None:
    names = _archive_names(path)
    _assert_no_forbidden_parts(path, names)
    if "ghrah/__init__.py" in names:
        raise RuntimeError(f"{path.name} 不得写入共享 namespace 文件 ghrah/__init__.py")
    if any(name.startswith(("tests/", "docs/", "examples/")) for name in names):
        raise RuntimeError(f"{path.name} wheel 夹带 tests/docs/examples")

    if path.name.startswith("ghrah_subject-"):
        entry_points = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        licenses = [name for name in names if ".dist-info/licenses/" in name]
        if len(entry_points) != 1 or not licenses:
            raise RuntimeError(f"{path.name} 缺少 entry_points 或许可证")
        with zipfile.ZipFile(path) as archive:
            content = archive.read(entry_points[0]).decode()
        for script in ("ghrah-subject", "ghrah-subject-server"):
            if script not in content:
                raise RuntimeError(f"{path.name} 缺少 console script：{script}")


def _audit_sdist(path: Path) -> None:
    names = _archive_names(path)
    _assert_no_forbidden_parts(path, names)
    for name in names:
        relative = Path(*Path(name).parts[1:])
        if len(relative.parts) == 1 and relative.name in FORBIDDEN_SDIST_ROOT_FILES:
            raise RuntimeError(f"{path.name} 夹带本地根文件：{relative.name}")
        if len(relative.parts) == 1 and relative.name.startswith(".env"):
            raise RuntimeError(f"{path.name} 夹带环境文件：{relative.name}")
    required = ("pyproject.toml", "README.md", "LICENSES/Apache-2.0.txt")
    relative_names = {"/".join(Path(name).parts[1:]) for name in names}
    missing = [name for name in required if name not in relative_names]
    if missing:
        raise RuntimeError(f"{path.name} 缺少 sdist 必需文件：{missing}")


def _audit_python_collisions(paths: list[Path]) -> None:
    owners: dict[str, str] = {}
    for path in paths:
        if path.suffix != ".whl":
            continue
        for name in _archive_names(path):
            if name.endswith("/") or ".dist-info" in Path(name).parts:
                continue
            previous = owners.setdefault(name, path.name)
            if previous != path.name:
                raise RuntimeError(
                    f"Python wheels 文件冲突：{name} 同时来自 {previous} 和 {path.name}"
                )


def _audit_npm(path: Path) -> None:
    names = _archive_names(path)
    _assert_no_forbidden_parts(path, names)
    required = {
        "package/package.json",
        "package/README.md",
        "package/LICENSE",
        "package/dist/index.js",
        "package/dist/index.d.ts",
    }
    missing = sorted(required - set(names))
    if missing:
        raise RuntimeError(f"{path.name} 缺少 npm 必需文件：{missing}")
    if any(name.startswith(("package/src/", "package/tests/")) for name in names):
        raise RuntimeError(f"{path.name} 夹带源码或测试")

    package_json = json.loads(_read_tgz_member(path, "package/package.json"))
    serialized = json.dumps(package_json, sort_keys=True)
    if "workspace:" in serialized:
        raise RuntimeError(f"{path.name} 仍包含 workspace 协议依赖")
    if package_json.get("license") != "Apache-2.0":
        raise RuntimeError(f"{path.name} 缺少 Apache-2.0 元数据")

    for name in names:
        if not name.endswith(".map"):
            continue
        source_map = json.loads(_read_tgz_member(path, name))
        if source_map.get("sourcesContent"):
            raise RuntimeError(f"{path.name} sourcemap 内嵌完整源码：{name}")


def _audit_vsix(path: Path) -> None:
    names = _archive_names(path)
    required = {
        "extension/package.json",
        "extension/dist/extension.js",
        "extension/readme.md",
        "extension/LICENSE.txt",
        "extension.vsixmanifest",
    }
    missing = sorted(required - set(names))
    if missing:
        raise RuntimeError(f"{path.name} 缺少 VSIX 必需文件：{missing}")
    forbidden_prefixes = (
        "extension/src/",
        "extension/node_modules/",
        "extension/tests/",
    )
    if any(name.startswith(forbidden_prefixes) for name in names):
        raise RuntimeError(f"{path.name} 夹带 VSIX 开发文件")
    forbidden_files = {
        "extension/tsconfig.json",
        "extension/tsconfig.tsbuildinfo",
        "extension/.vscodeignore",
    }
    overlap = sorted(set(names) & forbidden_files)
    if overlap:
        raise RuntimeError(f"{path.name} 夹带 VSIX 配置文件：{overlap}")

    with zipfile.ZipFile(path) as archive:
        package_json = json.loads(archive.read("extension/package.json"))
        manifest = archive.read("extension.vsixmanifest").decode()
    version = package_json["version"]
    if f'Version="{version}"' not in manifest:
        raise RuntimeError(f"{path.name} manifest 与 package.json 版本不一致")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(output_dir: Path, artifacts: list[Path]) -> None:
    payload: list[dict[str, Any]] = []
    for artifact in sorted(artifacts):
        payload.append(
            {
                "path": str(artifact.relative_to(output_dir)),
                "bytes": artifact.stat().st_size,
                "sha256": _sha256(artifact),
                "files": _archive_names(artifact),
            }
        )
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_python(output_dir: Path) -> list[Path]:
    artifacts: list[Path] = []
    for package in PYTHON_PACKAGES:
        target = output_dir / "python" / package
        target.mkdir(parents=True)
        _run("uv", "build", "--package", package, "--out-dir", str(target))
        for path in target.iterdir():
            if path.suffix == ".whl":
                _audit_wheel(path)
                artifacts.append(path)
            elif path.name.endswith(".tar.gz"):
                _audit_sdist(path)
                artifacts.append(path)
    _audit_python_collisions(artifacts)
    return artifacts


def _build_typescript(output_dir: Path) -> list[Path]:
    _run("pnpm", "-r", "build")
    npm_dir = output_dir / "npm"
    npm_dir.mkdir(parents=True)
    for package, package_dir in NPM_PACKAGES.items():
        print(f"packing {package}")
        _run("pnpm", "pack", "--pack-destination", str(npm_dir), cwd=package_dir)
    artifacts = sorted(npm_dir.glob("*.tgz"))
    for path in artifacts:
        _audit_npm(path)

    vscode_dir = output_dir / "vscode"
    vscode_dir.mkdir(parents=True)
    vsix = vscode_dir / "ghrah-vscode-extension.vsix"
    extension_dir = ROOT / "ghrah-observer-webui" / "vscode-extension"
    _run(
        "pnpm",
        "exec",
        "vsce",
        "package",
        "--no-dependencies",
        "--out",
        str(vsix),
        cwd=extension_dir,
    )
    _audit_vsix(vsix)
    return [*artifacts, vsix]


def main() -> int:
    """执行完整构建审计，并返回适合 CI 的退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="构建并审计全部交付产物（含内部包）")
    parser.add_argument("--out-dir", type=Path, required=True, help="必须为空的产物输出目录")
    args = parser.parse_args()
    if not args.all:
        parser.error("必须显式传入 --all")

    output_dir = args.out_dir.resolve()
    _require_empty_output_dir(output_dir)
    artifacts = [*_build_python(output_dir), *_build_typescript(output_dir)]
    _write_manifest(output_dir, artifacts)
    print(f"release dry run passed: {len(artifacts)} artifacts in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
