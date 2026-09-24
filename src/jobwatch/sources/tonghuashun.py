"""同花顺招聘（campus.10jqka.com.cn）社会招聘岗位。

接口是 ``GET /api/v3/school_recruitment/apply/apply_list``，一次就返回岗位职责
（``intro``）和任职要求（``requirement``），不用再抓详情页，也不需要登录。

两个坑：
1. 站点是纯前端渲染的，直接抓 ``/jobSocial/list`` 只有空壳，必须打接口。
2. **没有单岗位的详情路由**——列表里点开是浮层，``/jobSocial/detail?id=xxx``
   打开是空白页。所以卡片的链接统一给列表页，另附职位编号供人工定位。
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from ..httpclient import HttpError, request
from ..models import JobPosting, JobRef, JobSection, SectionStyle, clean_text
from .base import JobSource, SourceError

log = logging.getLogger(__name__)

ORIGIN = "https://campus.10jqka.com.cn"
API_URL = f"{ORIGIN}/api/v3/school_recruitment/apply/apply_list"
DEFAULT_LIST_URL = f"{ORIGIN}/jobSocial/list?type=social"
PAGE_SIZE = 100
MAX_PAGES = 20
RECRUIT_TYPES = {"social": "社招", "school": "校招"}


class TonghuashunSource(JobSource):
    provider = "tonghuashun"

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
            "Accept": "application/json, text/plain, */*",
            "Referer": self.list_url,
        }

    def _query(self, page: int) -> dict:
        query = urllib.parse.urlencode(
            {
                "applyName": self.keyword,
                "bases": "",
                "page": page,
                "pageCount": PAGE_SIZE,
                "applyColonyId": "",
                "applyRecruitmentSeriesIds": "",
                "type": self.recruit_type,
            }
        )
        try:
            text = request(
                f"{API_URL}?{query}",
                headers=self._headers,
                timeout=self.timeout,
                retries=self.retries,
            )
        except HttpError as exc:
            raise SourceError(f"同花顺接口请求失败：{exc}") from exc
        data = json.loads(text)
        if data.get("erro_msg") != "Success":
            raise SourceError(f"同花顺接口返回错误：{data.get('erro_msg') or data}")
        return data.get("ex_data") or {}

    def list_jobs(self) -> list[JobRef]:
        jobs: list[JobRef] = []
        seen: set[int] = set()
        matcher = _title_matcher(self.title_pattern)
        for page in range(1, MAX_PAGES + 1):
            page_data = self._query(page)
            rows = page_data.get("apply_show_do_list") or []
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
                job_id = raw.get("id")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                if matcher and not matcher(raw.get("name") or ""):
                    continue
                jobs.append(self._to_ref(job_id, raw))
            if len(seen) >= total:
                break
        return jobs

    def _to_ref(self, job_id: int, raw: dict) -> JobRef:
        place = clean_text(raw.get("base"))
        return JobRef(
            job_id=str(job_id),
            title=clean_text(raw.get("name")),
            # 站点没有单岗位路由，统一指向列表页，职位编号放在卡片里
            url=self.list_url,
            locations=[place] if place else [],
            payload=raw,
        )

    def fetch_posting(self, ref: JobRef) -> JobPosting:
        """列表接口已经返回了正文，不用再请求详情页。"""
        raw = ref.payload
        urgent = raw.get("urgent_recruitment")
        meta = [
            ("岗位类别", clean_text(raw.get("apply_type_first"))),
            ("所属集群", clean_text(raw.get("apply_colony_name"))),
            ("工作地点", "、".join(ref.locations)),
            ("招聘项目", clean_text(raw.get("apply_recruitment_series_name"))),
            ("职位编号", ref.job_id),
            ("备注", "急招" if urgent else ""),
        ]
        sections = [
            JobSection("岗位描述", clean_text(raw.get("intro")), SectionStyle.BULLETS),
            JobSection("任职要求", clean_text(raw.get("requirement")), SectionStyle.BULLETS),
        ]
        return JobPosting(
            job_id=ref.job_id,
            title=ref.title,
            url=ref.url,
            meta=[(label, value) for label, value in meta if value],
            sections=[section for section in sections if not section.is_empty],
        )


def _title_matcher(pattern: str):
    if not pattern:
        return None
    import re

    compiled = re.compile(pattern, re.IGNORECASE)

    def match(title: str) -> bool:
        return bool(compiled.search(title or ""))

    return match
