"""招聘来源注册表。watchlist 里的 provider 字段对应这里的实现。"""

from __future__ import annotations

from .alibaba import AlibabaAidcSource
from .apple import AppleJobsSource
from .base import JobSource, SourceError
from .bytedance import ByteDanceSource
from .tonghuashun import TonghuashunSource
from .xiaohongshu import XiaohongshuSource

PROVIDERS: dict[str, type[JobSource]] = {
    cls.provider: cls
    for cls in (
        AppleJobsSource,
        AlibabaAidcSource,
        ByteDanceSource,
        XiaohongshuSource,
        TonghuashunSource,
    )
}


def build_source(spec, *, timeout: float = 30.0, retries: int = 3) -> JobSource:
    """根据 watchlist 里的一条配置创建抓取器。"""
    cls = PROVIDERS.get(spec.provider)
    if cls is None:
        known = "、".join(sorted(PROVIDERS))
        raise SourceError(f"未知的招聘来源 provider={spec.provider!r}，已支持：{known}")
    return cls(
        spec.source_id,
        spec.display_name,
        translate=spec.translate,
        timeout=timeout,
        retries=retries,
        **spec.params,
    )


__all__ = [
    "AlibabaAidcSource",
    "AppleJobsSource",
    "ByteDanceSource",
    "JobSource",
    "PROVIDERS",
    "SourceError",
    "TonghuashunSource",
    "XiaohongshuSource",
    "build_source",
]
