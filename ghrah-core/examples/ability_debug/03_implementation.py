# SPDX-License-Identifier: Apache-2.0
"""实现面调试：运行时视图、工具参数（来自 bind_tool）、实例化。

运行（仓库根）：``uv run python ghrah-core/examples/ability_debug/03_implementation.py``
"""

from __future__ import annotations

from ghrah.ability import ability_class, create_ability, load_all_builtin_manifests


def main() -> None:
    """打印全部成员的视图、工具 schema 与一个真实实例。"""
    manifests = load_all_builtin_manifests()
    print(f"== 成员视图：{len(manifests)} 个 ==")
    for full_name, manifest in manifests.items():
        tool = manifest.tool
        params = sorted(tool.parameters) if tool is not None else []
        print(
            f"{full_name:32} | {manifest.metadata.title:16} | "
            f"tool={'—' if tool is None else tool.name} params={params}"
        )

    print("\n== 工具面（OpenAI 形状，直接给模型）==")
    tool_entries = [
        manifest.tool.to_openai_tool()
        for manifest in manifests.values()
        if manifest.tool is not None
    ]
    print(f"共 {len(tool_entries)} 个工具")
    for entry in tool_entries[:3]:
        print(openai_tool_line(entry))

    print("\n== 实例化：create_ability('read_file') ==")
    print("class       :", ability_class("read_file"))
    ability = create_ability("read_file")
    binding = ability.bind_tool()
    print("name        :", ability.name)
    print("bind_tool   :", binding["function"]["name"] if binding else None)
    print(
        "parameters  :", sorted(binding["function"]["parameters"]["properties"]) if binding else []
    )


def openai_tool_line(entry: dict) -> str:
    """把一条 OpenAI tool 定义压成一行，便于终端查看。"""
    function = entry["function"]
    return f"- {function['name']}: {function['description'][:50]}"


if __name__ == "__main__":
    main()
