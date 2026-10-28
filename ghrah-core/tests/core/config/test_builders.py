# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""core/config/builders 窗口配置构建测试。

覆盖 max_tokens 未声明透传与显式覆盖优先，
以及 compact_* 三字段的默认值、双向透传与非法值校验。
"""

import pytest

from ghrah.core.config.builders import build_window_from_dict, build_window_from_overrides
from ghrah.manifest.agent import WindowOverrides
from ghrah.types.config_types import WindowConfig


class TestBuildWindowFromDict:
    """wire payload → WindowConfig。"""

    def test_default_max_tokens(self) -> None:
        """未设 max_tokens 时透传 None（未声明，由 WindowManager 查表落定）。"""
        config = build_window_from_dict({})

        assert config.max_tokens is None

    def test_explicit_max_tokens_overrides_default(self) -> None:
        """显式 max_tokens 优先于默认值。"""
        config = build_window_from_dict({"max_tokens": 8192})

        assert config.max_tokens == 8192

    def test_compact_defaults_disabled(self) -> None:
        """未声明 compact 字段时：threshold=None（禁用）、keep_recent=2、builtin 方法。"""
        config = build_window_from_dict({})

        assert config.compact_threshold is None
        assert config.compact_keep_recent == 2
        assert config.compact_method == "ghrah.builtin"

    def test_compact_fields_pass_through(self) -> None:
        """wire dict 的 compact 字段透传到 WindowConfig。"""
        config = build_window_from_dict(
            {
                "compact_threshold": 0.8,
                "compact_keep_recent": 4,
                "compact_method": "ghrah.builtin",
            }
        )

        assert config.compact_threshold == 0.8
        assert config.compact_keep_recent == 4
        assert config.compact_method == "ghrah.builtin"

    @pytest.mark.parametrize("bad", [0, 1.5, -0.1])
    def test_compact_threshold_out_of_range_rejected(self, bad: float) -> None:
        """compact_threshold 非 (0,1] 区间值在构建期拒绝。"""
        with pytest.raises(ValueError, match="compact_threshold"):
            build_window_from_dict({"compact_threshold": bad})

    def test_compact_keep_recent_below_two_rejected(self) -> None:
        """compact_keep_recent < 2 破坏节点配对完整性，构建期拒绝。"""
        with pytest.raises(ValueError, match="compact_keep_recent"):
            build_window_from_dict({"compact_keep_recent": 1})

    def test_unknown_compact_method_rejected(self) -> None:
        """未知 compact_method 构建期拒绝（唯一合法值 ghrah.builtin）。"""
        with pytest.raises(ValueError, match="compact_method"):
            build_window_from_dict({"compact_method": "custom.pipeline"})


class TestBuildWindowFromOverrides:
    """manifest WindowOverrides → WindowConfig。"""

    def test_default_max_tokens_when_none(self) -> None:
        """max_tokens 未声明（None）时透传 None（由 WindowManager 查表落定）。"""
        config = build_window_from_overrides(WindowOverrides())

        assert config.max_tokens is None

    def test_explicit_max_tokens_overrides_default(self) -> None:
        """显式 max_tokens 优先于默认值。"""
        config = build_window_from_overrides(WindowOverrides(max_tokens=4096))

        assert config.max_tokens == 4096

    def test_compact_defaults_when_none(self) -> None:
        """compact 字段未声明（None）时兜底禁用语义（None/2/ghrah.builtin）。"""
        config = build_window_from_overrides(WindowOverrides())

        assert config.compact_threshold is None
        assert config.compact_keep_recent == 2
        assert config.compact_method == "ghrah.builtin"

    def test_compact_overrides_pass_through(self) -> None:
        """manifest compact 覆盖值透传到 WindowConfig。"""
        config = build_window_from_overrides(
            WindowOverrides(compact_threshold=0.7, compact_keep_recent=3)
        )

        assert config.compact_threshold == 0.7
        assert config.compact_keep_recent == 3
        assert config.compact_method == "ghrah.builtin"

    def test_invalid_compact_keep_recent_rejected(self) -> None:
        """非法 keep_recent 经合并后的 WindowConfig 校验拒绝。"""
        with pytest.raises(ValueError, match="compact_keep_recent"):
            build_window_from_overrides(WindowOverrides(compact_keep_recent=0))

    def test_invalid_compact_method_rejected(self) -> None:
        """非法 compact_method 经合并后的 WindowConfig 校验拒绝。"""
        with pytest.raises(ValueError, match="compact_method"):
            build_window_from_overrides(WindowOverrides(compact_method="noop"))

    def test_invalid_compact_threshold_rejected(self) -> None:
        """非法 threshold 经合并后的 WindowConfig 校验拒绝。"""
        with pytest.raises(ValueError, match="compact_threshold"):
            build_window_from_overrides(WindowOverrides(compact_threshold=2.0))


class TestWindowConfigValidation:
    """程序化构造路径的 WindowConfig 值校验（单一校验点 __post_init__）。"""

    def test_programmatic_valid_config(self) -> None:
        """程序化构造合法配置通过。"""
        config = WindowConfig(compact_threshold=0.9)

        assert config.compact_threshold == 0.9

    def test_programmatic_invalid_keep_recent_rejected(self) -> None:
        """程序化构造 keep_recent=1 拒绝。"""
        with pytest.raises(ValueError, match="compact_keep_recent"):
            WindowConfig(compact_keep_recent=1)

    def test_programmatic_invalid_threshold_rejected(self) -> None:
        """程序化构造 threshold=0 拒绝。"""
        with pytest.raises(ValueError, match="compact_threshold"):
            WindowConfig(compact_threshold=0)


def test_request_parameter_extensions_cross_both_builders():
    from ghrah.core.config.builders import (
        build_model_overrides_from_config,
        build_model_overrides_from_dict,
    )
    from ghrah.manifest.agent import ModelConfig

    params = {"temperature": 0, "top_p": 0, "extra_params": {"vendor": False}}
    assert build_model_overrides_from_dict(params).extra_params == {"vendor": False}
    model = ModelConfig(endpoint_name="shared", **params)
    result = build_model_overrides_from_config(model)
    assert result.temperature == 0 and result.top_p == 0
    assert result.extra_params == {"vendor": False}
    only_extra = build_model_overrides_from_config(
        ModelConfig(endpoint_name="shared", extra_params={"stop": ["END"]})
    )
    assert only_extra.extra_params == {"stop": ["END"]}
    with pytest.raises(ValueError, match="Unknown model request"):
        build_model_overrides_from_dict({"agent_config_name": "legacy"})
