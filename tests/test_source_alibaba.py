import json

import pytest

from jobwatch.sources import SourceError
from jobwatch.sources.alibaba import AlibabaAidcSource


class FakeSession:
    """按路径返回预置响应，并记录每次请求的 URL 和请求体。"""

    def __init__(self, responses: dict[str, object], cookies: dict[str, str] | None = None):
        self.responses = responses
        self.cookies = cookies if cookies is not None else {"XSRF-TOKEN": "tok-1"}
        self.calls: list[tuple[str, dict]] = []

    def request(self, url, **kwargs):
        body = json.loads(kwargs["data"].decode("utf-8"))
        self.calls.append((url, body))
        for path, payload in self.responses.items():
            if path in url:
                value = payload(self) if callable(payload) else payload
                return json.dumps(value, ensure_ascii=False)
        raise AssertionError(f"未预置的请求：{url}")


def make_source(session, **kwargs) -> AlibabaAidcSource:
    source = AlibabaAidcSource(
        "alibaba-aidc", "阿里国际", categories=kwargs.pop("categories", ["97", "143"]), **kwargs
    )
    source.session = session
    return source


def test_top_categories_are_expanded_into_subcategories(alibaba_categories, alibaba_search):
    session = FakeSession(
        {"/category/list": alibaba_categories, "/position/search": alibaba_search}
    )
    make_source(session).list_jobs()

    search_body = next(body for url, body in session.calls if "/position/search" in url)
    # 产品类 403-406 + 数据类 446-448，技术类不应混进来
    assert sorted(search_body["subCategories"].split(",")) == [
        "403",
        "404",
        "405",
        "406",
        "446",
        "447",
        "448",
    ]
    assert search_body["channel"] == "group_official_site"


def test_unknown_category_fails_loudly(alibaba_categories):
    session = FakeSession({"/category/list": alibaba_categories})
    with pytest.raises(SourceError, match="没找到"):
        make_source(session, categories=["不存在的类目"]).list_jobs()


def test_no_category_filter_skips_category_lookup(alibaba_search):
    session = FakeSession({"/position/search": alibaba_search})
    make_source(session, categories=[]).list_jobs()

    assert all("/category/list" not in url for url, _ in session.calls)


def test_csrf_token_is_sent_as_query_param(alibaba_categories, alibaba_search):
    session = FakeSession(
        {"/category/list": alibaba_categories, "/position/search": alibaba_search}
    )
    make_source(session).list_jobs()

    assert all("_csrf=tok-1" in url for url, _ in session.calls)


def test_handshake_retries_once_after_token_arrives(alibaba_search):
    """首次没有 token 时服务端会 403 并下发 cookie，第二次带上就该成功。"""
    session = FakeSession({"/position/search": None}, cookies={})
    attempts = {"n": 0}

    def respond(sess):
        attempts["n"] += 1
        if attempts["n"] == 1:
            sess.cookies["XSRF-TOKEN"] = "tok-late"
            return {"success": False, "errorMsg": "Forbidden"}
        return alibaba_search

    session.responses["/position/search"] = respond
    jobs = make_source(session, categories=[]).list_jobs()

    assert attempts["n"] == 2
    assert jobs
    assert "_csrf=tok-late" in session.calls[-1][0]


def test_posting_uses_list_payload_without_extra_request(alibaba_search):
    session = FakeSession({"/position/search": alibaba_search})
    source = make_source(session, categories=[])
    refs = source.list_jobs()
    before = len(session.calls)

    posting = source.fetch_posting(refs[0])

    assert len(session.calls) == before  # 列表里已有正文，不再请求
    assert [s.title for s in posting.sections] == ["岗位描述", "任职要求"]
    assert posting.sections[0].body
    meta = dict(posting.meta)
    assert meta["工作地点"]
    assert meta["学历要求"] in ("本科", "硕士", "博士", "大专", "不限")
    assert posting.url.endswith(refs[0].job_id)
    # track_id 是一次性的，不能进链接，否则同一岗位每天链接都不同
    assert "track_id" not in posting.url


def test_search_failure_surfaces_error_message():
    session = FakeSession({"/position/search": {"success": False, "errorMsg": "系统繁忙"}})
    with pytest.raises(SourceError, match="系统繁忙"):
        make_source(session, categories=[]).list_jobs()
