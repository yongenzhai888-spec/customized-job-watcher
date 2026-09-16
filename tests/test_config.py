import json
from pathlib import Path

import pytest

from jobwatch.config import Config, ConfigError, load_dotenv, load_watchlist

SRC = Path(__file__).resolve().parents[1] / "src"
REPO_WATCHLIST = Path(__file__).resolve().parents[1] / "watchlist.json"

ENV_KEYS = (
    "MAIL_TO",
    "MAIL_TO_CN",
    "SMTP_HOST",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "TRANSLATOR",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "STATE_DIR",
    "OUTPUT_DIR",
    "WATCHLIST",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """避免读到开发机上真实的 .env 和环境变量。"""
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("jobwatch.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("jobwatch.config.load_dotenv", lambda path=None: None)


def write_watchlist(tmp_path, entries) -> Path:
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def test_repo_watchlist_is_valid_and_has_no_personal_data():
    """仓库自带的清单必须能直接跑，且不含任何邮箱地址。"""
    specs = load_watchlist(REPO_WATCHLIST)
    assert {s.source_id for s in specs} == {"apple-pay", "alibaba-aidc", "bytedance-product"}
    assert {s.provider for s in specs} == {"apple", "alibaba", "bytedance"}

    raw = REPO_WATCHLIST.read_text(encoding="utf-8")
    assert "@" not in raw.replace("$comment", "")


def test_only_apple_needs_translation():
    by_id = {s.source_id: s for s in load_watchlist(REPO_WATCHLIST)}
    assert by_id["apple-pay"].translate is True
    assert by_id["alibaba-aidc"].translate is False
    assert by_id["bytedance-product"].translate is False


def test_recipients_are_resolved_from_named_env_var(monkeypatch, tmp_path):
    path = write_watchlist(
        tmp_path,
        [
            {"id": "a", "provider": "apple", "recipients_env": "MAIL_TO"},
            {"id": "b", "provider": "alibaba", "recipients_env": "MAIL_TO_CN"},
        ],
    )
    monkeypatch.setenv("MAIL_TO", "gmail@example.com")
    monkeypatch.setenv("MAIL_TO_CN", "qq@example.com, second@example.com")

    specs = {s.source_id: s for s in load_watchlist(path)}

    assert specs["a"].recipients == ["gmail@example.com"]
    assert specs["b"].recipients == ["qq@example.com", "second@example.com"]


def test_missing_recipients_env_leaves_source_without_recipients(tmp_path):
    path = write_watchlist(tmp_path, [{"id": "a", "provider": "apple", "recipients_env": "NOPE"}])
    spec = load_watchlist(path)[0]

    assert spec.recipients == []
    assert spec.has_recipients is False
    assert spec.recipients_env == "NOPE"


def test_duplicate_ids_are_rejected(tmp_path):
    path = write_watchlist(
        tmp_path, [{"id": "a", "provider": "apple"}, {"id": "a", "provider": "alibaba"}]
    )
    with pytest.raises(ConfigError, match="重复"):
        load_watchlist(path)


def test_entry_without_provider_is_rejected(tmp_path):
    path = write_watchlist(tmp_path, [{"id": "a"}])
    with pytest.raises(ConfigError, match="provider"):
        load_watchlist(path)


def test_missing_watchlist_gives_actionable_error(tmp_path):
    with pytest.raises(ConfigError, match="找不到监测清单"):
        load_watchlist(tmp_path / "nope.json")


def test_watchlist_env_var_overrides_default(monkeypatch, tmp_path):
    path = write_watchlist(tmp_path, [{"id": "custom", "provider": "apple"}])
    monkeypatch.setenv("WATCHLIST", str(path))

    assert [s.source_id for s in Config.from_env().sources] == ["custom"]


def test_state_file_is_per_source(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHLIST", str(write_watchlist(tmp_path, [{"id": "a", "provider": "apple"}])))
    config = Config.from_env()

    assert config.state_file("a").name == "a.json"
    assert config.state_file("b") != config.state_file("a")


def test_no_personal_email_is_hardcoded_in_source():
    """仓库是公开的，任何真实邮箱地址都不该出现在源码里。"""
    offenders = [
        f"{path.name}:{line_no}"
        for path in SRC.rglob("*.py")
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if any(domain in line for domain in ("@gmail.com", "@qq.com", "@163.com"))
    ]
    assert offenders == [], f"源码里出现了邮箱地址：{offenders}"


def test_dotenv_does_not_override_real_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MAIL_TO=from-dotenv@example.com\nSMTP_HOST=smtp.dotenv\n", encoding="utf-8")
    monkeypatch.setenv("MAIL_TO", "from-shell@example.com")

    load_dotenv(env_file)

    import os

    assert os.environ["MAIL_TO"] == "from-shell@example.com"
    assert os.environ["SMTP_HOST"] == "smtp.dotenv"
