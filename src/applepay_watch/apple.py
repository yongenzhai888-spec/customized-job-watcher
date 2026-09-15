"""解析 jobs.apple.com 的岗位数据。

Apple 招聘站是服务端渲染的 React 应用，页面里内嵌了一段
``window.__staticRouterHydrationData = JSON.parse("...")``，
里面就是搜索结果和岗位详情的结构化数据，因此不需要解析 DOM，
也不需要走未公开的 POST 接口（该接口对外已 301）。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass, field

from .httpclient import build_url, request

log = logging.getLogger(__name__)

HYDRATION_MARKER = "window.__staticRouterHydrationData"
JSON_PARSE_MARKER = "JSON.parse("
MAX_PAGES = 25


class ParseError(RuntimeError):
    """页面结构与预期不符，通常意味着 Apple 改版了。"""


def extract_hydration_data(html: str) -> dict:
    start = html.find(HYDRATION_MARKER)
    if start == -1:
        raise ParseError("页面中找不到 __staticRouterHydrationData，Apple 招聘站可能已改版")
    parse_at = html.find(JSON_PARSE_MARKER, start)
    if parse_at == -1:
        raise ParseError("__staticRouterHydrationData 不是 JSON.parse 形式，无法解析")
    cursor = parse_at + len(JSON_PARSE_MARKER)
    try:
        # 外层是一个 JS 字符串字面量，先解出字符串再解出真正的 JSON。
        payload, _ = json.JSONDecoder().raw_decode(html[cursor:].lstrip())
    except ValueError as exc:
        raise ParseError(f"解析 hydration 数据失败：{exc}") from exc
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ParseError("hydration 数据不是对象")
    return payload


def _loader(html: str, key: str) -> dict:
    data = extract_hydration_data(html).get("loaderData", {})
    section = data.get(key)
    if not isinstance(section, dict):
        raise ParseError(f"hydration 数据里缺少 loaderData.{key}")
    return section


@dataclass(slots=True)
class JobSummary:
    """搜索结果里的一条岗位。"""

    job_id: str
    position_id: str
    title: str
    slug: str
    locations: list[str] = field(default_factory=list)
    team: str = ""
    posted_at: str = ""
    posted_display: str = ""

    @classmethod
    def from_payload(cls, raw: dict) -> JobSummary:
        job_id = str(raw.get("id") or raw.get("reqId") or "").strip()
        if not job_id:
            raise ParseError("搜索结果里存在没有 id 的岗位")
        return cls(
            job_id=job_id,
            position_id=str(raw.get("positionId") or ""),
            title=(raw.get("postingTitle") or "").strip(),
            slug=(raw.get("transformedPostingTitle") or "").strip(),
            locations=[
                loc.get("name", "").strip()
                for loc in raw.get("locations") or []
                if loc.get("name")
            ],
            team=((raw.get("team") or {}).get("teamName") or "").strip(),
            posted_at=(raw.get("postDateInGMT") or "").strip(),
            posted_display=(raw.get("postingDate") or "").strip(),
        )


@dataclass(slots=True)
class JobDetail:
    """岗位详情页上的完整信息，全部来自 Apple 官网原文。"""

    job_id: str
    title: str
    url: str
    summary: str = ""
    description: str = ""
    responsibilities: str = ""
    minimum_qualifications: str = ""
    preferred_qualifications: str = ""
    team: str = ""
    locations: list[str] = field(default_factory=list)
    weekly_hours: str = ""
    employment_type: str = ""
    posted_display: str = ""
    posted_at: str = ""
    role_number: str = ""

    @property
    def text_fields(self) -> dict[str, str]:
        """需要翻译成中文的正文字段。"""
        return {
            "title": self.title,
            "summary": self.summary,
            "description": self.description,
            "responsibilities": self.responsibilities,
            "minimum_qualifications": self.minimum_qualifications,
            "preferred_qualifications": self.preferred_qualifications,
        }


class AppleJobsClient:
    """只读访问 jobs.apple.com。"""

    def __init__(
        self,
        search_url: str,
        *,
        locale: str = "zh-cn",
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.search_url = search_url
        self.locale = locale
        self.timeout = timeout
        self.retries = retries
        parts = urllib.parse.urlsplit(search_url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.query_params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))

    def _get(self, url: str) -> str:
        return request(
            url,
            headers={"Referer": self.search_url},
            timeout=self.timeout,
            retries=self.retries,
        )

    def search(self, max_pages: int = MAX_PAGES) -> list[JobSummary]:
        """翻完所有分页，返回筛选条件下的全部岗位。"""
        jobs: list[JobSummary] = []
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            url = build_url(self.search_url, {"page": page})
            section = _loader(self._get(url), "search")
            results = section.get("searchResults") or []
            log.info(
                "第 %s 页返回 %s 个岗位（官网 totalRecords=%s）",
                page,
                len(results),
                section.get("totalRecords"),
            )
            if not results:
                break
            for raw in results:
                job = JobSummary.from_payload(raw)
                if job.job_id in seen:
                    continue
                seen.add(job.job_id)
                jobs.append(job)
            total = section.get("totalRecords") or 0
            if len(jobs) >= total:
                break
        return jobs

    def detail_url(self, job: JobSummary) -> str:
        slug = job.slug or "role"
        path = f"/{self.locale}/details/{job.job_id}/{slug}"
        keep = {k: v for k, v in self.query_params.items() if k in ("product", "team")}
        return build_url(f"{self.origin}{path}", keep)

    def fetch_detail(self, job: JobSummary) -> JobDetail:
        url = self.detail_url(job)
        data = _loader(self._get(url), "jobDetails").get("jobsData") or {}
        posting = _pick_localized_posting(data)
        locations = [
            loc.get("name", "").strip()
            for loc in (data.get("localeLocation") or data.get("locations") or [])
            if loc.get("name")
        ]
        weekly = data.get("standardWeeklyHours")
        return JobDetail(
            job_id=job.job_id,
            title=posting.get("postingTitle") or job.title,
            url=url,
            summary=_clean(posting.get("jobSummary")),
            description=_clean(posting.get("description")),
            responsibilities=_clean(posting.get("responsibilities")),
            minimum_qualifications=_clean(posting.get("minimumQualifications")),
            preferred_qualifications=_clean(posting.get("preferredQualifications")),
            # 搜索页的团队名和日期已按 locale 本地化，详情页的是英文，优先用前者。
            team=job.team or ", ".join(data.get("teamNames") or []),
            locations=locations or job.locations,
            weekly_hours=f"{weekly} 小时/周" if weekly else "",
            employment_type=str(data.get("employmentType") or ""),
            posted_display=job.posted_display or str(data.get("postingDate") or ""),
            posted_at=str(data.get("postDateInGMT") or job.posted_at),
            role_number=str(data.get("jobNumber") or job.job_id),
        )


def _pick_localized_posting(data: dict) -> dict:
    """优先取 Apple 官方的中文文案，没有再回退到详情页默认（英文）字段。"""
    localizations = data.get("localizations") or {}
    for code in ("zh_CN", "zh_TW", "zh_HK"):
        posting = (localizations.get(code) or {}).get("posting")
        if isinstance(posting, dict) and posting.get("description"):
            return posting
    return data


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()
