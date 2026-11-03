# SPDX-License-Identifier: Apache-2.0
"""内容面调试：把包封成版本，看逐成员摘要与文件切片。

运行（仓库根）：``uv run python ghrah-core/examples/ability_debug/04_content.py``
"""

from __future__ import annotations

import json
from pathlib import Path

from ghrah.ability.versions import capture_ability_version, capture_file_version

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "ghrah-core/examples/ability_content/reporting"


def main() -> None:
    """捕获示例包与文件切片，打印版本摘要与内容清单。"""
    print("== capture_ability_version(reporting) ==")
    version = capture_ability_version((EXAMPLE / "ability.yaml").read_text(), root=EXAMPLE)
    print("version.digest          :", version.digest)
    print("version.definition_digest:", version.definition_digest)
    print("version.content_digest  :", version.content_digest)
    definition = version.definition()
    print("identity                :", definition.identity.full_name)
    print("operations              :", sorted(definition.operations))
    print("skills                  :", sorted(definition.skills))
    print("---- manifest（成员地址 → 摘要）----")
    for member, entry in sorted(json.loads(version.manifest_json).items()):
        print(f"  {member:34} {entry['digest'][:19]}… {entry['size']:>6} B  {entry['media_type']}")
    print("blobs                   :", len(version.blobs))

    print("\n== capture_file_version()：文件切片 ==")
    file_version = capture_file_version()
    file_definition = file_version.definition()
    print("operations:", sorted(file_definition.operations))
    print("providers :", {k: v.contract for k, v in file_definition.provider_requirements.items()})
    print("digest    :", file_version.digest)


if __name__ == "__main__":
    main()
