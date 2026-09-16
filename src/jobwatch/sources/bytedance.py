"""字节跳动 / TikTok 招聘（jobs.bytedance.com）社招岗位。

``POST /api/v1/search/job/posts`` 一次就返回岗位描述和任职要求，不用抓详情页。

两个坑：
1. 必须带 ``website-path`` 头。``society`` 是社招，``campus`` 是校招；
   值填错会返回 ``site not exist``。
2. 请求密集时站点会用 **405 Method Not Allowed** 来限流——不是真的不支持 POST，
   隔几秒重发同一个请求就会成功。所以这里刻意把每页请求之间拉开间隔，
   并依赖 httpclient 对 405 的重试。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..httpclient import post_json
from ..models import JobPosting, JobRef, JobSection, SectionStyle, clean_text
from .base import JobSource, SourceError

log = logging.getLogger(__name__)

API_URL = "https://jobs.bytedance.com/api/v1/search/job/posts"
DEFAULT_LIST_URL = "https://jobs.bytedance.com/experienced/position"
#: 接口允许一次取 50 条，页数少一半就能少一半被限流的机会。
PAGE_SIZE = 50
MAX_PAGES = 80
#: 站点对连续请求很敏感，翻页之间稍作停顿比事后重试划算。
PAGE_INTERVAL_SECONDS = 2.0

RECRUIT_TYPES = {"society": "社招", "campus": "校招"}


class ByteDanceSource(JobSource):
    provider = "bytedance"

    def __init__(
        self,
        source_id: str,
        display_name: str,
        *,
        category_ids: list[str] | None = None,
        location_codes: list[str] | None = None,
        keyword: str = "",
        website_path: str = "society",
        title_pattern: str = "",
        **kwargs,
    ):
        list_url = kwargs.pop("list_url", None) or DEFAULT_LIST_URL
        super().__init__(source_id, display_name, list_url=list_url, **kwargs)
        self.category_ids = [str(c) for c in (category_ids or [])]
        self.location_codes = [str(c) for c in (location_codes or [])]
        self.keyword = keyword
        self.website_path = website_path
        self.title_pattern = title_pattern

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://jobs.bytedance.com",
            "Referer": self.list_url,
            "website-path": self.website_path,
            "Portal-Channel": "job",
            "Portal-Platform": "pc",
        }

    def _search(self, offset: int) -> dict:
        payload = {
            "keyword": self.keyword,
            "limit": PAGE_SIZE,
            "offset": offset,
            "portal_type": 2,
            "portal_entrance": 1,
        }
        if self.category_ids:
            payload["job_category_id_list"] = self.category_ids
        if self.location_codes:
            payload["location_code_list"] = self.location_codes

        data = post_json(
            API_URL,
            payload,
            headers=self._headers,
            timeout=self.timeout,
            # 405 是限流信号，多给几次机会
            retries=max(self.retries, 5),
        )
        if data.get("code") != 0:
            raise SourceError(f"字节接口返回错误：{data.get('message') or data}")
        return data.get("data") or {}

    def list_jobs(self) -> list[JobRef]:
        jobs: list[JobRef] = []
        seen: set[str] = set()
        matcher = _title_matcher(self.title_pattern)
        for page in range(MAX_PAGES):
            if page:
                time.sleep(PAGE_INTERVAL_SECONDS)
            data = self._search(page * PAGE_SIZE)
            posts = data.get("job_post_list") or []
            total = data.get("count") or 0
            log.info(
                "[%s] 第 %s 页 %s 条（站点共 %s），已收 %s",
                self.source_id,
                page + 1,
                len(posts),
                total,
                len(jobs),
            )
            if not posts:
                break
            for raw in posts:
                job_id = str(raw.get("id") or "")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                if matcher and not matcher(raw.get("title") or ""):
                    continue
                jobs.append(self._to_ref(job_id, raw))
            if (page + 1) * PAGE_SIZE >= total:
                break
        return jobs

    def _to_ref(self, job_id: str, raw: dict) -> JobRef:
        city = (raw.get("city_info") or {}).get("name") or ""
        return JobRef(
            job_id=job_id,
            title=clean_text(raw.get("title")),
            url=f"https://jobs.bytedance.com/experienced/position/{job_id}/detail",
            locations=[city] if city else [],
            posted_at=_to_iso(raw.get("publish_time")),
            posted_display=_to_date(raw.get("publish_time")),
            payload=raw,
        )

    def fetch_posting(self, ref: JobRef) -> JobPosting:
        """列表接口已经返回了正文，不用再请求详情页。"""
        raw = ref.payload
        recruit = raw.get("recruit_type") or {}
        meta = [
            ("岗位类别", (raw.get("job_category") or {}).get("name") or ""),
            ("工作地点", "、".join(ref.locations)),
            ("招聘类型", (recruit.get("parent") or {}).get("name") or recruit.get("name") or ""),
            ("发布日期", ref.posted_display),
            ("职位编号", clean_text(raw.get("code")) or ref.job_id),
        ]
        sections = [
            JobSection("岗位描述", clean_text(raw.get("description")), SectionStyle.BULLETS),
            JobSection("任职要求", clean_text(raw.get("requirement")), SectionStyle.BULLETS),
        ]
        return JobPosting(
            job_id=ref.job_id,
            title=ref.title,
            url=ref.url,
            meta=[(k, v) for k, v in meta if v],
            sections=[s for s in sections if not s.is_empty],
        )


def _title_matcher(pattern: str):
    """可选的标题过滤，用来把宽泛的类目收窄到真正关心的业务线。"""
    if not pattern:
        return None
    import re

    compiled = re.compile(pattern, re.IGNORECASE)
    return lambda title: bool(compiled.search(title or ""))


def _to_iso(millis: object) -> str:
    if not isinstance(millis, (int, float)) or millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _to_date(millis: object) -> str:
    if not isinstance(millis, (int, float)) or millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).strftime("%Y年%m月%d日")
