"""Apple 招聘官网（jobs.apple.com）。

Apple 招聘站是服务端渲染的 React 应用，页面里内嵌了一段
``window.__staticRouterHydrationData = JSON.parse("...")``，
搜索结果和岗位详情的结构化数据都在里面，因此不需要解析 DOM，
也不需要走未公开的 POST 接口（该接口对外已 301）。
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from ..httpclient import build_url, request
from ..models import JobPosting, JobRef, JobSection, SectionStyle, clean_text
from .base import JobSource, SourceError

log = logging.getLogger(__name__)

HYDRATION_MARKER = "window.__staticRouterHydrationData"
JSON_PARSE_MARKER = "JSON.parse("
MAX_PAGES = 25
DEFAULT_SEARCH_URL = (
    "https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY"
)


def extract_hydration_data(html: str) -> dict:
    start = html.find(HYDRATION_MARKER)
    if start == -1:
        raise SourceError("页面中找不到 __staticRouterHydrationData，Apple 招聘站可能已改版")
    parse_at = html.find(JSON_PARSE_MARKER, start)
    if parse_at == -1:
        raise SourceError("__staticRouterHydrationData 不是 JSON.parse 形式，无法解析")
    cursor = parse_at + len(JSON_PARSE_MARKER)
    try:
        # 外层是一个 JS 字符串字面量，先解出字符串再解出真正的 JSON。
        payload, _ = json.JSONDecoder().raw_decode(html[cursor:].lstrip())
    except ValueError as exc:
        raise SourceError(f"解析 hydration 数据失败：{exc}") from exc
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise SourceError("hydration 数据不是对象")
    return payload


def _loader(html: str, key: str) -> dict:
    section = extract_hydration_data(html).get("loaderData", {}).get(key)
    if not isinstance(section, dict):
        raise SourceError(f"hydration 数据里缺少 loaderData.{key}")
    return section


class AppleJobsSource(JobSource):
    provider = "apple"

    def __init__(self, source_id: str, display_name: str, *, locale: str = "zh-cn", **kwargs):
        list_url = kwargs.pop("list_url", None) or DEFAULT_SEARCH_URL
        super().__init__(source_id, display_name, list_url=list_url, **kwargs)
        self.locale = locale
        parts = urllib.parse.urlsplit(self.list_url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.query_params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))

    def _get(self, url: str) -> str:
        return request(
            url,
            headers={"Referer": self.list_url},
            timeout=self.timeout,
            retries=self.retries,
        )

    def list_jobs(self) -> list[JobRef]:
        jobs: list[JobRef] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            section = _loader(self._get(build_url(self.list_url, {"page": page})), "search")
            results = section.get("searchResults") or []
            log.info(
                "[%s] 第 %s 页返回 %s 个岗位（官网 totalRecords=%s）",
                self.source_id,
                page,
                len(results),
                section.get("totalRecords"),
            )
            if not results:
                break
            for raw in results:
                job_id = str(raw.get("id") or raw.get("reqId") or "").strip()
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                jobs.append(self._to_ref(job_id, raw))
            if len(jobs) >= (section.get("totalRecords") or 0):
                break
        return jobs

    def _to_ref(self, job_id: str, raw: dict) -> JobRef:
        slug = (raw.get("transformedPostingTitle") or "role").strip()
        keep = {k: v for k, v in self.query_params.items() if k in ("product", "team")}
        url = build_url(f"{self.origin}/{self.locale}/details/{job_id}/{slug}", keep)
        return JobRef(
            job_id=job_id,
            title=(raw.get("postingTitle") or "").strip(),
            url=url,
            locations=[
                loc.get("name", "").strip() for loc in raw.get("locations") or [] if loc.get("name")
            ],
            posted_at=(raw.get("postDateInGMT") or "").strip(),
            posted_display=(raw.get("postingDate") or "").strip(),
            payload={
                "team": ((raw.get("team") or {}).get("teamName") or "").strip(),
                "slug": slug,
            },
        )

    def fetch_posting(self, ref: JobRef) -> JobPosting:
        data = _loader(self._get(ref.url), "jobDetails").get("jobsData") or {}
        posting = _pick_localized_posting(data)
        locations = [
            loc.get("name", "").strip()
            for loc in (data.get("localeLocation") or data.get("locations") or [])
            if loc.get("name")
        ] or ref.locations
        weekly = data.get("standardWeeklyHours")

        # 搜索页的团队名和日期已按 locale 本地化，详情页的是英文，优先用前者。
        meta = [
            ("团队", ref.payload.get("team") or ", ".join(data.get("teamNames") or [])),
            ("工作地点", "、".join(locations)),
            ("发布日期", ref.posted_display or str(data.get("postingDate") or "")),
            ("职位编号", str(data.get("jobNumber") or ref.job_id)),
            ("工作时长", f"{weekly} 小时/周" if weekly else ""),
        ]
        sections = [
            JobSection(
                "岗位简介",
                clean_text(posting.get("description")) or clean_text(posting.get("jobSummary")),
            ),
            JobSection("主要职责", clean_text(posting.get("responsibilities")), SectionStyle.BULLETS),
            JobSection(
                "任职要求（必备）",
                clean_text(posting.get("minimumQualifications")),
                SectionStyle.BULLETS,
            ),
            JobSection(
                "加分项",
                clean_text(posting.get("preferredQualifications")),
                SectionStyle.BULLETS,
            ),
        ]
        return JobPosting(
            job_id=ref.job_id,
            title=posting.get("postingTitle") or ref.title,
            url=ref.url,
            meta=[(k, v) for k, v in meta if v],
            sections=[s for s in sections if not s.is_empty],
        )


def _pick_localized_posting(data: dict) -> dict:
    """优先取 Apple 官方的中文文案，没有再回退到详情页默认（英文）字段。"""
    localizations = data.get("localizations") or {}
    for code in ("zh_CN", "zh_TW", "zh_HK"):
        posting = (localizations.get(code) or {}).get("posting")
        if isinstance(posting, dict) and posting.get("description"):
            return posting
    return data
