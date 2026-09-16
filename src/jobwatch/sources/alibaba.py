"""阿里巴巴国际数字商业集团（aidc-jobs.alibaba.com）社招岗位。

站点是纯前端渲染的，但后端接口很规整：``POST /position/search``
一次就能拿到岗位描述和任职要求，不用再抓详情页。

两个坑：
1. 接口有 Spring Security 的 CSRF 保护。任意一次请求都会在响应里下发
   ``XSRF-TOKEN`` cookie，之后把它作为 ``_csrf`` 查询参数带回去即可，
   不需要登录。
2. 类目筛选走的是 ``subCategories``（子类 code），``categories``（顶级类 code）
   实际不起过滤作用。所以要先拉一次类目树，把顶级类展开成它的子类。
"""

from __future__ import annotations

import json
import logging

from ..httpclient import CookieSession
from ..models import JobPosting, JobRef, JobSection, SectionStyle, clean_text
from .base import JobSource, SourceError

log = logging.getLogger(__name__)

ORIGIN = "https://aidc-jobs.alibaba.com"
DEFAULT_LIST_URL = f"{ORIGIN}/off-campus/position-list?lang=zh"
CHANNEL = "group_official_site"
PAGE_SIZE = 50
MAX_PAGES = 40

DEGREE_NAMES = {
    "junior_college": "大专",
    "bachelor": "本科",
    "master": "硕士",
    "doctor": "博士",
    "unlimited": "不限",
}


class AlibabaAidcSource(JobSource):
    provider = "alibaba"

    def __init__(
        self,
        source_id: str,
        display_name: str,
        *,
        categories: list[str] | None = None,
        keyword: str = "",
        language: str = "zh",
        **kwargs,
    ):
        list_url = kwargs.pop("list_url", None) or DEFAULT_LIST_URL
        super().__init__(source_id, display_name, list_url=list_url, **kwargs)
        self.categories = [str(c) for c in (categories or [])]
        self.keyword = keyword
        self.language = language
        self.session = CookieSession(timeout=self.timeout, retries=self.retries)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": self.list_url,
        }

    def _post(self, path: str, payload: dict) -> dict:
        body = {"channel": CHANNEL, "language": self.language, **payload}
        # 第一次请求拿不到 token 也没关系，服务端会在 403 响应里下发，
        # CookieSession 记住后重试一次即可。
        for attempt in (1, 2):
            token = self.session.cookies.get("XSRF-TOKEN", "")
            url = f"{ORIGIN}{path}" + (f"?_csrf={token}" if token else "")
            text = self.session.request(
                url,
                method="POST",
                headers=self._headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                allow_status={403} if attempt == 1 else None,
            )
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SourceError(f"阿里接口返回了非 JSON 响应：{text[:200]}") from exc
            if data.get("success"):
                return data.get("content") or {}
            if attempt == 1 and self.session.cookies.get("XSRF-TOKEN"):
                continue
            raise SourceError(f"阿里接口调用失败 {path}：{data.get('errorMsg') or text[:200]}")
        raise SourceError(f"阿里接口调用失败 {path}")

    def _subcategory_codes(self) -> str:
        """把顶级类目展开成逗号分隔的子类 code；没配类目就返回空串（不过滤）。"""
        if not self.categories:
            return ""
        tree = self._post("/category/list", {})
        if not isinstance(tree, list):
            raise SourceError("阿里类目接口返回格式异常")
        wanted = set(self.categories)
        codes: list[str] = []
        matched: list[str] = []
        for top in tree:
            if str(top.get("code")) not in wanted and top.get("name") not in wanted:
                continue
            matched.append(top.get("name") or str(top.get("code")))
            for sub in top.get("categories") or []:
                if sub.get("code"):
                    codes.append(str(sub["code"]))
        if not codes:
            raise SourceError(f"在阿里类目树里没找到 {self.categories}，站点类目可能调整了")
        log.info("[%s] 类目 %s 展开为 %s 个子类", self.source_id, "、".join(matched), len(codes))
        return ",".join(codes)

    def list_jobs(self) -> list[JobRef]:
        sub_categories = self._subcategory_codes()
        jobs: list[JobRef] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            content = self._post(
                "/position/search",
                {
                    "batchId": "",
                    "categories": ",".join(self.categories),
                    "deptCodes": [],
                    "key": self.keyword,
                    "pageIndex": page,
                    "pageSize": PAGE_SIZE,
                    "regions": "",
                    "subCategories": sub_categories,
                },
            )
            rows = content.get("datas") or []
            total = content.get("totalCount") or 0
            log.info("[%s] 第 %s 页 %s 条（共 %s）", self.source_id, page, len(rows), total)
            if not rows:
                break
            for raw in rows:
                job_id = str(raw.get("id") or "")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                jobs.append(self._to_ref(job_id, raw))
            if len(jobs) >= total:
                break
        return jobs

    def _to_ref(self, job_id: str, raw: dict) -> JobRef:
        # positionUrl 带一次性的 track_id，去掉它以免同一岗位每天生成不同链接。
        url = f"{ORIGIN}/off-campus/position-detail?positionId={job_id}"
        return JobRef(
            job_id=job_id,
            title=clean_text(raw.get("name")),
            url=url,
            locations=[str(loc) for loc in raw.get("workLocations") or []],
            posted_at=_to_iso(raw.get("publishTime")),
            posted_display=_to_date(raw.get("publishTime")),
            payload=raw,
        )

    def fetch_posting(self, ref: JobRef) -> JobPosting:
        """列表接口已经返回了正文，不用再请求详情页。"""
        raw = ref.payload
        meta = [
            ("所属部门", clean_text(raw.get("department"))),
            ("岗位类别", "、".join(raw.get("categories") or [])),
            ("工作地点", "、".join(ref.locations)),
            ("职级", clean_text(raw.get("level"))),
            ("经验要求", _experience(raw.get("experience"))),
            ("学历要求", DEGREE_NAMES.get(raw.get("degree") or "", clean_text(raw.get("degree")))),
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


def _experience(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    low, high = value.get("from"), value.get("to")
    if low and high:
        return f"{low}-{high} 年"
    if low:
        return f"{low} 年以上"
    if high:
        return f"{high} 年以内"
    return ""


def _to_iso(millis: object) -> str:
    from datetime import datetime, timezone

    if not isinstance(millis, (int, float)) or millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _to_date(millis: object) -> str:
    from datetime import datetime, timezone

    if not isinstance(millis, (int, float)) or millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).strftime("%Y年%m月%d日")
