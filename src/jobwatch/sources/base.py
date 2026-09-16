"""招聘来源的统一接口。

每个来源要做两件事：列出当前在招岗位（轻量，用于比对新增），
以及给出某个岗位的完整正文（有的站点列表里就带正文，就不用再请求了）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import JobPosting, JobRef


class SourceError(RuntimeError):
    """抓取失败，通常是站点改版或被限流。"""


class JobSource(ABC):
    #: 供应商标识，watchlist 里用它选择实现
    provider: str = ""

    def __init__(
        self,
        source_id: str,
        display_name: str,
        *,
        list_url: str,
        translate: bool = False,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.source_id = source_id
        self.display_name = display_name
        self.list_url = list_url
        self.translate = translate
        self.timeout = timeout
        self.retries = retries

    @abstractmethod
    def list_jobs(self) -> list[JobRef]:
        """返回当前筛选条件下的全部岗位。"""

    @abstractmethod
    def fetch_posting(self, ref: JobRef) -> JobPosting:
        """返回某个岗位的完整内容。"""

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.source_id}>"
