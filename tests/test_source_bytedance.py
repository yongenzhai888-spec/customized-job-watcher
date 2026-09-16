import pytest

from jobwatch.sources import SourceError
from jobwatch.sources import bytedance as bd_module
from jobwatch.sources.bytedance import ByteDanceSource

CATS = ["6704215864629004552", "6704215864591255820"]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """翻页之间的停顿在测试里没必要真的等。"""
    monkeypatch.setattr(bd_module.time, "sleep", lambda _seconds: None)


def make_source(**kwargs) -> ByteDanceSource:
    return ByteDanceSource("bytedance", "字节跳动", category_ids=CATS, **kwargs)


def patch_api(monkeypatch, responses):
    calls = []

    def fake_post_json(url, payload, **kwargs):
        calls.append((payload, kwargs.get("headers", {})))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(bd_module, "post_json", fake_post_json)
    return calls


def test_society_website_path_is_sent(monkeypatch, bytedance_search):
    calls = patch_api(monkeypatch, [bytedance_search])
    make_source().list_jobs()

    _payload, headers = calls[0]
    # website-path 填错会让接口返回 "site not exist"
    assert headers["website-path"] == "society"
    assert headers["Portal-Channel"] == "job"


def test_categories_are_forwarded(monkeypatch, bytedance_search):
    calls = patch_api(monkeypatch, [bytedance_search])
    make_source().list_jobs()

    assert calls[0][0]["job_category_id_list"] == CATS


def test_jobs_are_parsed_with_body_from_list(monkeypatch, bytedance_search):
    patch_api(monkeypatch, [bytedance_search])
    source = make_source()
    refs = source.list_jobs()

    assert len(refs) == 3
    assert refs[0].title
    assert refs[0].url.startswith("https://jobs.bytedance.com/experienced/position/")

    posting = source.fetch_posting(refs[0])
    assert [s.title for s in posting.sections] == ["岗位描述", "任职要求"]
    assert posting.sections[1].body
    assert dict(posting.meta)["工作地点"]


def test_paging_stops_once_count_is_covered(monkeypatch, bytedance_search):
    """count 只有 3 条时不该继续翻页。"""
    calls = patch_api(monkeypatch, [bytedance_search])
    make_source().list_jobs()
    assert len(calls) == 1


def test_paging_continues_until_count_reached(monkeypatch, bytedance_search):
    posts = bytedance_search["data"]["job_post_list"]
    page1 = {"code": 0, "data": {"job_post_list": posts, "count": 6}}
    page2 = {
        "code": 0,
        "data": {
            "job_post_list": [dict(p, id=f"{p['id']}-2") for p in posts],
            "count": 6,
        },
    }
    empty = {"code": 0, "data": {"job_post_list": [], "count": 6}}
    calls = patch_api(monkeypatch, [page1, page2, empty])

    monkeypatch.setattr(bd_module, "PAGE_SIZE", 3)
    jobs = make_source().list_jobs()

    assert len(jobs) == 6
    assert [c[0]["offset"] for c in calls] == [0, 3]


def test_title_pattern_narrows_results(monkeypatch, bytedance_search):
    patch_api(monkeypatch, [bytedance_search])
    titles = [p["title"] for p in bytedance_search["data"]["job_post_list"]]
    needle = titles[0][:4]

    jobs = make_source(title_pattern=needle).list_jobs()

    assert jobs and all(needle in job.title for job in jobs)
    assert len(jobs) < len(titles) or len(titles) == 1


def test_api_error_code_raises(monkeypatch):
    patch_api(monkeypatch, [{"code": -9000003, "message": "site not exist"}])
    with pytest.raises(SourceError, match="site not exist"):
        make_source().list_jobs()
