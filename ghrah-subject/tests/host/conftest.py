# SPDX-License-Identifier: Apache-2.0
"""宿主测试只使用代内产物；fixture 不包含源码清单。"""

import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def catalog() -> dict:
    return json.loads(Path(__file__).with_name("catalog_golden.json").read_text("utf-8"))


@pytest.fixture
def generation(tmp_path, catalog):
    root = tmp_path / "中文 generation"
    root.mkdir()
    raw = json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8")
    (root / "catalog.json").write_bytes(raw)
    (root / "GENERATION.toml").write_text(
        '[generation]\ncatalog_sha256 = "' + hashlib.sha256(raw).hexdigest() + '"\n',
        "utf-8",
    )
    return root
