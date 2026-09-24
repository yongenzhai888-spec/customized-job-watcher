"""小红书招聘（job.xiaohongshu.com）社招岗位。

列表接口 ``POST /websiterecruit/position/pageQueryPosition`` 一次就把岗位职责
（``duty``）和任职要求（``qualification``）都返回了，不用再抓详情页——和字节一样。

两个坑：
1. 页面是纯前端渲染的，直接 GET ``/social/position`` 拿到的只有几 KB 的空壳，
   所以必须打接口，不能抓 HTML。
2. ``workplace`` 是多城市拼串，分隔符是全角逗号，且逗号后面带空格
   （``"北京市， 上海市，深圳市"``），要拆开再 strip，否则地点判断会错。
"""

from __future__ import annotations

import json
import logging
import re

from ..httpclient import HttpError, post_json
from ..models import JobPosting, JobRef, JobSection, SectionStyle, clean_text
from .base import JobSource, SourceError

log = logging.getLogger(__name__)

ORIGIN = "https://job.xiaohongshu.com"
API_URL = f"{ORIGIN}/websiterecruit/position/pageQueryPosition"
DEFAULT_LIST_URL = f"{ORIGIN}/social/position"
PAGE_SIZE = 100
MAX_PAGES = 30
RECRUIT_TYPES = {"social": "社招", "campus": "校招"}


class XiaohongshuSource(JobSource):
    provider = "xiaohongshu"

    def __init__(
        self,
        source_id: str,
        display_name: str,
        *,
        keyword: str = "",
        recruit_type: str = "social",
        title_pattern: str = "",
        **kwargs,
    ):
        list_url = kwargs.pop("list_url", None) or DEFAULT_LIST_URL
        super().__init__(source_id, display_name, list_url=list_url, **kwargs)
        self.keyword = keyword
        self.recruit_type = recruit_type
        self.title_pattern = title_pattern

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": self.list_url,
        }

    def _query(self, page_num: int) -> dict:
        payload = {
            "positionName": self.keyword,
            "pageNum": page_num,
            "pageSize": PAGE_SIZE,
            "recruitType": self.recruit_type,
        }
        try:
            data = post_json(
                API_URL,
                payload,
                headers=self._headers,
                timeout=self.timeout,
                retries=self.retries,
            )
        except HttpError as exc:
            raise SourceError(f"小红书接口请求失败：{exc}") from exc
        if isinstance(data, str):  # 兜底：万一返回的不是已解析的 JSON
            data = json.loads(data)
        if data.get("statusCode") != 200:
            raise SourceError(f"小红书接口返回错误：{data.get('alertMsg') or data}")
        return data.get("data") or {}

    def list_jobs(self) -> list[JobRef]:
        jobs: list[JobRef] = []
        seen: set[int] = set()
        matcher = _title_matcher(self.title_pattern)
        for page in range(1, MAX_PAGES + 1):
            page_data = self._query(page)
            rows = page_data.get("list") or []
            total = page_data.get("total") or 0
            log.info(
                "[%s] 第 %s 页 %s 条（共 %s），已收 %s",
                self.source_id,
                page,
                len(rows),
                total,
                len(jobs),
            )
            if not rows:
                break
            for raw in rows:
                position_id = raw.get("positionId")
                if not position_id or position_id in seen:
                    continue
                seen.add(position_id)
                if raw.get("recruitStatus") not in (None, "in_recruitment"):
                    continue
                if matcher and not matcher(raw.get("positionName") or ""):
                    continue
                jobs.append(self._to_ref(position_id, raw))
            if len(seen) >= total:
                break
        return jobs

    def _to_ref(self, position_id: int, raw: dict) -> JobRef:
        return JobRef(
            job_id=str(position_id),
            title=clean_text(raw.get("positionName")),
            url=f"{ORIGIN}/social/position/{position_id}",
            locations=_split_workplace(raw.get("workplace")),
            posted_display=clean_text(raw.get("publishTime")),
            payload=raw,
        )

    def fetch_posting(self, ref: JobRef) -> JobPosting:
        """列表接口已经返回了正文，不用再请求详情页。"""
        raw = ref.payload
        direction = "、".join(
            part
            for part in (
                clean_text(raw.get("directionName")),
                clean_text(raw.get("subDirectionName")),
            )
            if part
        )
        meta = [
            ("岗位类别", clean_text(raw.get("jobType"))),
            ("方向", direction),
            ("工作地点", "、".join(ref.locations)),
            ("发布日期", ref.posted_display),
            ("职位编号", ref.job_id),
        ]
        sections = [
            JobSection("岗位描述", clean_text(raw.get("duty")), SectionStyle.BULLETS),
            JobSection("任职要求", clean_text(raw.get("qualification")), SectionStyle.BULLETS),
        ]
        return JobPosting(
            job_id=ref.job_id,
            title=ref.title,
            url=ref.url,
            meta=[(label, value) for label, value in meta if value],
            sections=[section for section in sections if not section.is_empty],
        )


def _split_workplace(text: object) -> list[str]:
    """``"北京市， 上海市，深圳市"`` → ``["北京市", "上海市", "深圳市"]``。"""
    cleaned = clean_text(text)
    if not cleaned:
        return []
    parts = cleaned.replace(",", "，").split("，")
    return [part.strip() for part in parts if part.strip()]


def _title_matcher(pattern: str):
    if not pattern:
        return None
    compiled = re.compile(pattern, re.IGNORECASE)

    def match(title: str) -> bool:
        return bool(compiled.search(title or ""))

    return match
