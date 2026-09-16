import pytest
from conftest import wrap_hydration

from jobwatch.sources import SourceError
from jobwatch.sources.apple import AppleJobsSource, extract_hydration_data

SEARCH_URL = "https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY"
EMPTY_PAGE = wrap_hydration({"loaderData": {"search": {"searchResults": [], "totalRecords": 0}}})


def make_source(**kwargs) -> AppleJobsSource:
    return AppleJobsSource("apple-pay", "Apple Pay", list_url=SEARCH_URL, translate=True, **kwargs)


def test_extract_hydration_data(search_html):
    assert extract_hydration_data(search_html)["loaderData"]["search"]["totalRecords"] == 2


def test_unknown_markup_raises_source_error():
    with pytest.raises(SourceError):
        extract_hydration_data("<html><body>Apple 改版了</body></html>")


def test_list_jobs_reads_every_page(monkeypatch, search_html):
    source = make_source()
    requested = []

    def fake_get(url):
        requested.append(url)
        return search_html if "page=1" in url else EMPTY_PAGE

    monkeypatch.setattr(source, "_get", fake_get)
    jobs = source.list_jobs()

    assert [job.title for job in jobs] == [
        "SDET - Apple Pay Quality Engineer",
        "Store Apps Engineering DevOPs Engineer",
    ]
    assert jobs[0].locations == ["上海"]
    assert "product=apple-pay-APPAY" in jobs[0].url
    assert "location=" not in jobs[0].url
    # totalRecords 已满足，不该继续翻页
    assert len(requested) == 1


def test_list_jobs_stops_on_empty_page(monkeypatch):
    source = make_source()
    monkeypatch.setattr(source, "_get", lambda url: EMPTY_PAGE)
    assert source.list_jobs() == []


def test_posting_carries_requirements_and_localised_meta(monkeypatch, search_html, detail_html):
    source = make_source()
    monkeypatch.setattr(source, "_get", lambda url: search_html if "search" in url else detail_html)
    ref = source.list_jobs()[0]
    posting = source.fetch_posting(ref)

    titles = [section.title for section in posting.sections]
    assert titles == ["岗位简介", "主要职责", "任职要求（必备）", "加分项"]
    assert "Apple Pay Quality Team" in posting.sections[0].body
    assert posting.sections[1].lines[0].startswith("Architect automation frameworks")
    assert "5+ years" in posting.sections[2].body
    assert "Kubernetes" in posting.sections[3].body

    meta = dict(posting.meta)
    # 搜索页的团队名和日期已本地化，应当优先于详情页的英文
    assert meta["团队"] == "软件和服务"
    assert meta["发布日期"] == "2026年8月19日"
    assert meta["工作地点"] == "上海"
    assert meta["工作时长"] == "40 小时/周"


def test_official_chinese_posting_wins_over_english(monkeypatch, detail_payload):
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
    source = make_source()
    monkeypatch.setattr(source, "_get", lambda url: wrap_hydration(detail_payload))

    from jobwatch.models import JobRef

    posting = source.fetch_posting(JobRef(job_id="x", title="t", url="https://example.com"))

    assert posting.title == "Apple Pay 质量工程师"
    assert posting.sections[0].body == "官方中文岗位描述"


def test_empty_sections_are_dropped(monkeypatch, detail_payload):
    data = detail_payload["loaderData"]["jobDetails"]["jobsData"]
    data["preferredQualifications"] = ""
    data["responsibilities"] = "   \n  "
    source = make_source()
    monkeypatch.setattr(source, "_get", lambda url: wrap_hydration(detail_payload))

    from jobwatch.models import JobRef

    posting = source.fetch_posting(JobRef(job_id="x", title="t", url="https://example.com"))

    assert [s.title for s in posting.sections] == ["岗位简介", "任职要求（必备）"]
