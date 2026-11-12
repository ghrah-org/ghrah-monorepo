"""资源入口对现有模型/窗口构造器的显式参数校验。"""

from typing import Any, Literal

from ghrah.protocol.runtime import RuntimeRequest
from pydantic import Field


class ModelOptions(RuntimeRequest):
    """当前执行器支持的模型覆盖；模型厂商特有参数放 extra_params。"""

    temperature: float | None = Field(default=None, allow_inf_nan=False)
    max_tokens: int | None = Field(default=None, gt=0)
    top_p: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    top_k: int | None = Field(default=None, ge=0)
    extra_params: dict[str, Any] = Field(default_factory=dict)


class WindowOptions(RuntimeRequest):
    """显式窗口策略，省略项沿用现有 WindowConfig 的默认值。"""

    max_tokens: int | None = Field(default=None, gt=0)
    strategies: list[Literal["tool_call_fold", "sliding_window", "truncation", "llm_summary"]] = (
        Field(default_factory=lambda: ["tool_call_fold", "truncation"])
    )
    tool_call_max_length: int = Field(default=500, gt=0)
    sliding_window_size: int = Field(default=20, gt=0)
    compact_threshold: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    compact_keep_recent: int = Field(default=2, ge=2)
    compact_method: Literal["ghrah.builtin"] = "ghrah.builtin"
