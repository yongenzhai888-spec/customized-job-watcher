import pytest
from conftest import make_spec

from jobwatch import watcher
from jobwatch.email_render import EmailContent
from jobwatch.models import JobPosting, JobRef, JobSection
from jobwatch.store import JobStore


class FakeSource:
    def __init__(self, refs, *, translate=False, source_id="demo"):
        self.refs = refs
        self.translate = translate
        self.source_id = source_id
        self.list_url = "https://example.com/jobs"
        self.fetched: list[str] = []

    def list_jobs(self):
        return self.refs

    def fetch_posting(self, ref):
        self.fetched.append(ref.job_id)
        return JobPosting(
            job_id=ref.job_id,
            title=ref.title,
            url=ref.url,
            meta=[("工作地点", "、".join(ref.locations))],
            sections=[JobSection("岗位描述", "Build things"), JobSection("任职要求", "Know things")],
        )


class RecordingMailer:
    def __init__(self):
        self.sent: list[EmailContent] = []

    def send(self, content):
        self.sent.append(content)
        return "已发送（测试）"


def ref(job_id, title="产品经理"):
    return JobRef(job_id=job_id, title=title, url=f"https://example.com/{job_id}", locations=["上海"])


@pytest.fixture
def wiring(monkeypatch):
    mailer = RecordingMailer()
    state = {"mailer": mailer, "source": None, "mailer_args": []}

    def install(refs, **kwargs):
        source = FakeSource(refs, **kwargs)
        state["source"] = source
        monkeypatch.setattr(watcher, "build_source", lambda spec, **kw: source)

        def fake_build_mailer(mail_cfg, recipients, out_dir, **kw):
            state["mailer_args"].append((recipients, kw))
            return mailer

        monkeypatch.setattr(watcher, "build_mailer", fake_build_mailer)
        return source

    state["install"] = install
    return state


def test_first_run_only_records_baseline(config_factory, wiring):
    config = config_factory(make_spec("demo"))
    wiring["install"]([ref("a"), ref("b")])

    report = watcher.run_all(config)
    result = report.results[0]

    assert result.baseline_only is True
    assert wiring["mailer"].sent == []
    assert "首次运行" in result.message
    assert JobStore(config.state_file("demo")).known_ids == {"a", "b"}


def test_new_job_triggers_email_for_that_job_only(config_factory, wiring):
    config = config_factory(make_spec("demo"))
    source = wiring["install"]([ref("a")])
    watcher.run_all(config)

    source.refs = [ref("a"), ref("b", "数据分析师")]
    source.fetched.clear()
    wiring["mailer"].sent.clear()

    result = watcher.run_all(config).results[0]

    assert [j.job_id for j in result.new_jobs] == ["b"]
    assert source.fetched == ["b"]  # 不重复抓已通知过的岗位
    assert len(wiring["mailer"].sent) == 1
    assert "新增 1 个岗位" in wiring["mailer"].sent[0].subject


def test_no_email_when_nothing_changed(config_factory, wiring):
    config = config_factory(make_spec("demo"))
    wiring["install"]([ref("a")])
    watcher.run_all(config)
    wiring["mailer"].sent.clear()

    result = watcher.run_all(config).results[0]

    assert result.new_jobs == []
    assert wiring["mailer"].sent == []
    assert "没有新增岗位" in result.message


def test_state_not_advanced_when_sending_fails(config_factory, wiring, monkeypatch):
    config = config_factory(make_spec("demo"))
    source = wiring["install"]([ref("a")])
    watcher.run_all(config)

    class BrokenMailer:
        def send(self, content):
            raise RuntimeError("SMTP 挂了")

    monkeypatch.setattr(watcher, "build_mailer", lambda *a, **k: BrokenMailer())
    source.refs = [ref("a"), ref("b")]

    report = watcher.run_all(config)
    assert report.failed and "SMTP 挂了" in report.failed[0].error

    # 下次运行仍应把 b 当成新岗位，一次发送失败不能让岗位被漏掉
    monkeypatch.setattr(watcher, "build_mailer", lambda *a, **k: wiring["mailer"])
    assert [j.job_id for j in watcher.run_all(config).results[0].new_jobs] == ["b"]


def test_source_without_recipients_is_skipped(config_factory, wiring):
    config = config_factory(make_spec("demo", recipients=[], recipients_env="MAIL_TO_CN"))
    wiring["install"]([ref("a")])

    result = watcher.run_all(config).results[0]

    assert result.skipped is True
    assert "MAIL_TO_CN" in result.message
    assert wiring["mailer"].sent == []


def test_require_mail_turns_missing_recipients_into_a_failure(config_factory, wiring):
    """定时任务里"跳过"等于悄悄不发邮件，最难发现，所以必须直接失败。"""
    config = config_factory(make_spec("demo", recipients=[], recipients_env="MAIL_TO_CN"))
    wiring["install"]([ref("a")])

    report = watcher.run_all(config, require_mail=True)

    assert report.failed
    assert "MAIL_TO_CN" in report.failed[0].error
    assert wiring["mailer"].sent == []


def test_disabled_source_is_not_run(config_factory, wiring):
    config = config_factory(make_spec("demo", enabled=False))
    wiring["install"]([ref("a")])

    assert watcher.run_all(config).results == []


def test_only_filter_limits_sources(config_factory, wiring):
    config = config_factory(make_spec("a-source"), make_spec("b-source"))
    wiring["install"]([ref("a")])

    report = watcher.run_all(config, only=["b-source"])

    assert [r.source_id for r in report.results] == ["b-source"]


def test_one_source_failure_does_not_stop_the_others(config_factory, monkeypatch):
    config = config_factory(make_spec("broken"), make_spec("healthy"))
    mailer = RecordingMailer()
    monkeypatch.setattr(watcher, "build_mailer", lambda *a, **k: mailer)

    def build(spec, **kwargs):
        if spec.source_id == "broken":
            raise RuntimeError("站点改版了")
        return FakeSource([ref("x")], source_id=spec.source_id)

    monkeypatch.setattr(watcher, "build_source", build)
    report = watcher.run_all(config)

    assert [r.source_id for r in report.results] == ["broken", "healthy"]
    assert report.failed[0].error == "站点改版了"
    assert report.results[1].baseline_only is True


def test_recipients_are_passed_per_source(config_factory, wiring):
    config = config_factory(
        make_spec("demo", recipients=["cn@example.com"]), notify_on_first_run=True
    )
    wiring["install"]([ref("a")])

    watcher.run_all(config)

    assert wiring["mailer_args"][0][0] == ["cn@example.com"]


def test_translation_is_skipped_for_chinese_sources(config_factory, wiring, monkeypatch):
    config = config_factory(make_spec("demo", translate=False), notify_on_first_run=True)
    wiring["install"]([ref("a")], translate=False)

    def explode(*args, **kwargs):
        raise AssertionError("不需要翻译的来源不该调用翻译器")

    monkeypatch.setattr(watcher, "build_translator", explode)
    watcher.run_all(config)

    assert len(wiring["mailer"].sent) == 1


def test_translation_runs_for_translated_sources(config_factory, wiring, monkeypatch):
    config = config_factory(make_spec("demo", translate=True), notify_on_first_run=True)
    wiring["install"]([ref("a", "Product Manager")], translate=True)

    class FakeTranslator:
        name = "fake"

        def translate_batch(self, texts):
            return [f"[中]{t}" for t in texts]

        def close(self):
            pass

    monkeypatch.setattr(watcher, "build_translator", lambda cfg: FakeTranslator())
    watcher.run_all(config)

    body = wiring["mailer"].sent[0].text_body
    assert "[中]Product Manager" in body
    assert "[中]Build things" in body


def test_posting_falls_back_to_title_when_body_fetch_fails(config_factory, wiring):
    config = config_factory(make_spec("demo"), notify_on_first_run=True)
    source = wiring["install"]([ref("a")])

    def boom(_ref):
        raise RuntimeError("详情页 404")

    source.fetch_posting = boom
    report = watcher.run_all(config)

    # 单个岗位正文取不到，邮件仍要发出去，只是内容退化成标题和链接
    assert report.results[0].email_sent is True
    assert "https://example.com/a" in wiring["mailer"].sent[0].text_body
