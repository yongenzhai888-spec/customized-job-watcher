from pathlib import Path

import pytest

from applepay_watch.config import Config, load_dotenv

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """避免读到开发机上真实的 .env 和环境变量。"""
    for name in (
        "MAIL_TO",
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "APPLE_JOBS_SEARCH_URL",
        "TRANSLATOR",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "STATE_FILE",
        "OUTPUT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("applepay_watch.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("applepay_watch.config.load_dotenv", lambda path=None: None)


def test_recipients_come_only_from_env(monkeypatch):
    assert Config.from_env().mail.recipients == []

    monkeypatch.setenv("MAIL_TO", "a@example.com, b@example.com;c@example.com")
    assert Config.from_env().mail.recipients == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_mail_is_not_considered_configured_without_recipient(monkeypatch):
    monkeypatch.setenv("SMTP_USERNAME", "me@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    assert Config.from_env().mail.configured is False

    monkeypatch.setenv("MAIL_TO", "me@example.com")
    assert Config.from_env().mail.configured is True


def test_no_personal_email_is_hardcoded_in_source():
    """仓库是公开的，任何真实邮箱地址都不该出现在源码里。"""
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "@gmail.com" in line or "@qq.com" in line or "@163.com" in line:
                offenders.append(f"{path.name}:{line_no}")
    assert offenders == [], f"源码里出现了邮箱地址：{offenders}"


def test_dotenv_does_not_override_real_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MAIL_TO=from-dotenv@example.com\nSMTP_HOST=smtp.dotenv\n", encoding="utf-8")
    monkeypatch.setenv("MAIL_TO", "from-shell@example.com")

    load_dotenv(env_file)

    import os

    assert os.environ["MAIL_TO"] == "from-shell@example.com"
    assert os.environ["SMTP_HOST"] == "smtp.dotenv"
