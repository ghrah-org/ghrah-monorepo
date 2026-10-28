# SPDX-License-Identifier: Apache-2.0
"""在全新解释器中验证适配器与 LLM 公共入口不依赖导入顺序。"""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "first_module",
    ["ghrah.chat.format.openai_responses", "ghrah.llm.factory", "ghrah.llm.errors"],
)
def test_adapter_and_factory_import_in_fresh_process(first_module: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, sys; importlib.import_module(sys.argv[1]); "
            "from ghrah.chat.format.openai_responses import OpenAIResponsesFormat; "
            "from ghrah.llm import LLMFactory; "
            "from ghrah.llm.factory import LLMFactory as Factory; "
            "assert LLMFactory is Factory",
            first_module,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
