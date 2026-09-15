import pytest

from applepay_watch import watcher
from applepay_watch.apple import JobDetail, JobSummary
from applepay_watch.config import Config, MailConfig, TranslationConfig
from applepay_watch.email_render import EmailContent
from applepay_watch.store import JobStore


class FakeClient:
    def __init__(self, jobs):
        self.jobs = jobs
        self.detail_calls = []

    def search(self, max_pages=25):
        return self.jobs

    def detail_url(self, job):
        return f"https://jobs.apple.com/zh-cn/details/{job.job_id}/{job.slug}"

    def fetch_detail(self, job):
        self.detail_calls.append(job.job_id)
        return JobDetail(
            job_id=job.job_id,
            title=job.title,
            url=self.detail_url(job),
            description="Job description",
            responsibilities="Do things",
            minimum_qualifications="Know things",
        )


class RecordingMailer:
    def __init__(self):
        self.sent: list[EmailContent] = []

    def send(self, content):
        self.sent.append(content)
        return "已发送（测试）"


def job(job_id, title="Apple Pay Engineer"):
    return JobSummary(job_id=job_id, position_id=job_id, title=title, slug="apple-pay-engineer")


@pytest.fixture
def config(tmp_path):
    return Config(
        state_file=tmp_path / "state.json",
        output_dir=tmp_path / "out",
        mail=MailConfig(username="a@b.com", password="pw", recipients=["someone@example.com"]),
        translation=TranslationConfig(engine="none", cache_file=tmp_path / "cache.json"),
    )


@pytest.fixture
def wiring(monkeypatch):
    """把网络和发信都换成假的，只验证编排逻辑。"""
    mailer = RecordingMailer()
    holder = {"client": None, "mailer": mailer}

    def install(jobs):
        client = FakeClient(jobs)
        holder["client"] = client
        monkeypatch.setattr(watcher, "AppleJobsClient", lambda *a, **k: client)
        monkeypatch.setattr(watcher, "build_mailer", lambda *a, **k: mailer)
        return client

    holder["install"] = install
    return holder


def test_first_run_only_records_baseline(config, wiring):
    wiring["install"]([job("a"), job("b")])

    result = watcher.run_once(config)

    assert result.is_first_run is True
    assert wiring["mailer"].sent == []
    assert "首次运行" in result.message
    assert JobStore(config.state_file).known_ids == {"a", "b"}


def test_first_run_can_notify_when_configured(config, wiring):
    config.notify_on_first_run = True
    wiring["install"]([job("a")])

    result = watcher.run_once(config)

    assert len(wiring["mailer"].sent) == 1
    assert result.email_sent is True


def test_new_job_triggers_email_with_only_that_job(config, wiring):
    client = wiring["install"]([job("a")])
    watcher.run_once(config)

    client.jobs = [job("a"), job("b", "Apple Pay Product Manager")]
    client.detail_calls.clear()
    wiring["mailer"].sent.clear()

    result = watcher.run_once(config)

    assert [j.job_id for j in result.new_jobs] == ["b"]
    assert client.detail_calls == ["b"]  # 不重复抓已通知过的岗位
    assert len(wiring["mailer"].sent) == 1
    assert "新增 1 个岗位" in wiring["mailer"].sent[0].subject


def test_no_email_when_nothing_changed(config, wiring):
    wiring["install"]([job("a")])
    watcher.run_once(config)
    wiring["mailer"].sent.clear()

    result = watcher.run_once(config)

    assert result.new_jobs == []
    assert wiring["mailer"].sent == []
    assert "没有新增岗位" in result.message


def test_removed_job_is_reported_without_email(config, wiring):
    client = wiring["install"]([job("a"), job("b")])
    watcher.run_once(config)
    wiring["mailer"].sent.clear()

    client.jobs = [job("a")]
    result = watcher.run_once(config)

    assert result.removed_ids == ["b"]
    assert wiring["mailer"].sent == []


def test_state_is_not_advanced_when_sending_fails(config, wiring, monkeypatch):
    client = wiring["install"]([job("a")])
    watcher.run_once(config)

    class BrokenMailer:
        def send(self, content):
            raise RuntimeError("SMTP 挂了")

    monkeypatch.setattr(watcher, "build_mailer", lambda *a, **k: BrokenMailer())
    client.jobs = [job("a"), job("b")]

    with pytest.raises(RuntimeError):
        watcher.run_once(config)

    # 下一次运行仍然应该把 b 当成新岗位，用户不会因为一次发送失败漏掉岗位
    monkeypatch.setattr(watcher, "build_mailer", lambda *a, **k: wiring["mailer"])
    assert [j.job_id for j in watcher.run_once(config).new_jobs] == ["b"]


def test_force_notifies_all_current_jobs(config, wiring):
    wiring["install"]([job("a"), job("b")])
    watcher.run_once(config)
    wiring["mailer"].sent.clear()

    result = watcher.run_once(config, force_notify=True)

    assert result.new_jobs == []
    assert len(wiring["mailer"].sent) == 1
    assert "新增 2 个岗位" in wiring["mailer"].sent[0].subject
