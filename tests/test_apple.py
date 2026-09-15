import pytest
from conftest import wrap_hydration

from applepay_watch.apple import AppleJobsClient, JobSummary, ParseError, extract_hydration_data

SEARCH_URL = "https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY"


def test_extract_hydration_data(search_html):
    data = extract_hydration_data(search_html)
    assert data["loaderData"]["search"]["totalRecords"] == 2


def test_extract_hydration_data_raises_on_unknown_markup():
    with pytest.raises(ParseError):
        extract_hydration_data("<html><body>Apple 改版了</body></html>")


def test_search_parses_all_pages(monkeypatch, search_html):
    client = AppleJobsClient(SEARCH_URL)
    requested = []

    def fake_get(url):
        requested.append(url)
        # 第二页开始返回空结果，模拟官网的真实行为
        return search_html if "page=1" in url else EMPTY_PAGE

    monkeypatch.setattr(client, "_get", fake_get)
    jobs = client.search()

    assert [job.title for job in jobs] == [
        "SDET - Apple Pay Quality Engineer",
        "Store Apps Engineering DevOPs Engineer",
    ]
    assert jobs[0].locations == ["上海"]
    assert jobs[0].team == "软件和服务"
    # totalRecords 已满足，不应该继续翻页
    assert len(requested) == 1


def test_search_stops_when_page_is_empty(monkeypatch):
    client = AppleJobsClient(SEARCH_URL)
    monkeypatch.setattr(client, "_get", lambda url: EMPTY_PAGE)
    assert client.search() == []


def test_detail_url_keeps_product_filter():
    client = AppleJobsClient(SEARCH_URL)
    job = JobSummary(
        job_id="200678634-3715",
        position_id="200678634",
        title="SDET - Apple Pay Quality Engineer",
        slug="sdet-apple-pay-quality-engineer",
    )
    url = client.detail_url(job)
    assert url.startswith(
        "https://jobs.apple.com/zh-cn/details/200678634-3715/sdet-apple-pay-quality-engineer"
    )
    assert "product=apple-pay-APPAY" in url
    assert "location=" not in url


def test_fetch_detail_extracts_requirements(monkeypatch, detail_html):
    client = AppleJobsClient(SEARCH_URL)
    monkeypatch.setattr(client, "_get", lambda url: detail_html)
    job = JobSummary(
        job_id="200678634-3715",
        position_id="200678634",
        title="SDET - Apple Pay Quality Engineer",
        slug="sdet-apple-pay-quality-engineer",
        team="软件和服务",
        posted_display="2026年8月19日",
    )
    detail = client.fetch_detail(job)

    assert detail.title == "SDET - Apple Pay Quality Engineer"
    assert "Apple Pay Quality Team" in detail.description
    assert detail.responsibilities.splitlines()[0].startswith("Architect automation frameworks")
    assert "5+ years" in detail.minimum_qualifications
    assert "Kubernetes" in detail.preferred_qualifications
    assert detail.locations == ["上海"]
    assert detail.weekly_hours == "40 小时/周"
    # 搜索页的本地化文案优先于详情页的英文
    assert detail.team == "软件和服务"
    assert detail.posted_display == "2026年8月19日"


def test_fetch_detail_prefers_official_chinese_posting(monkeypatch, detail_payload):
    """如果 Apple 自己提供了中文文案，就直接用官方版本，不再机器翻译。"""
    detail_payload["loaderData"]["jobDetails"]["jobsData"]["localizations"] = {
        "zh_CN": {
            "posting": {
                "postingTitle": "Apple Pay 质量工程师",
                "description": "官方中文岗位描述",
                "responsibilities": "官方中文职责",
                "minimumQualifications": "官方中文要求",
            }
        }
    }
    client = AppleJobsClient(SEARCH_URL)
    monkeypatch.setattr(client, "_get", lambda url: wrap_hydration(detail_payload))
    detail = client.fetch_detail(JobSummary(job_id="x", position_id="x", title="t", slug="s"))

    assert detail.title == "Apple Pay 质量工程师"
    assert detail.description == "官方中文岗位描述"
    assert detail.responsibilities == "官方中文职责"


EMPTY_PAGE = wrap_hydration({"loaderData": {"search": {"searchResults": [], "totalRecords": 0}}})
