"""集中管理配置：全部来自环境变量，可用项目根目录的 .env 覆盖默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SEARCH_URL = (
    "https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY"
)
DEFAULT_MAIL_TO = "yuhanhe0614@gmail.com"


def load_dotenv(path: Path | None = None) -> None:
    """把 .env 里的键值读进 os.environ（不覆盖已存在的真实环境变量）。"""
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


@dataclass(slots=True)
class MailConfig:
    host: str = "smtp.gmail.com"
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=lambda: [DEFAULT_MAIL_TO])
    use_ssl: bool = False
    use_starttls: bool = True
    subject_prefix: str = "[Apple Pay 招聘监测]"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.password and self.recipients)

    @property
    def from_address(self) -> str:
        return self.sender or self.username


@dataclass(slots=True)
class TranslationConfig:
    # auto / llm / deepl / google / mymemory / none
    engine: str = "auto"
    target_language: str = "zh-CN"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    deepl_api_key: str = ""
    cache_file: Path = PROJECT_ROOT / ".cache" / "translations.json"


@dataclass(slots=True)
class Config:
    search_url: str = DEFAULT_SEARCH_URL
    locale: str = "zh-cn"
    state_file: Path = PROJECT_ROOT / "state" / "seen_jobs.json"
    output_dir: Path = PROJECT_ROOT / "out"
    notify_on_first_run: bool = False
    request_timeout: float = 30.0
    request_retries: int = 3
    mail: MailConfig = field(default_factory=MailConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()
        recipients = [
            addr.strip()
            for addr in (_env("MAIL_TO", DEFAULT_MAIL_TO)).replace(";", ",").split(",")
            if addr.strip()
        ]
        mail = MailConfig(
            host=_env("SMTP_HOST", "smtp.gmail.com"),
            port=_env_int("SMTP_PORT", 587),
            username=_env("SMTP_USERNAME"),
            password=_env("SMTP_PASSWORD"),
            sender=_env("MAIL_FROM"),
            recipients=recipients or [DEFAULT_MAIL_TO],
            use_ssl=_env_bool("SMTP_SSL", False),
            use_starttls=_env_bool("SMTP_STARTTLS", True),
            subject_prefix=_env("MAIL_SUBJECT_PREFIX", "[Apple Pay 招聘监测]"),
        )
        translation = TranslationConfig(
            engine=_env("TRANSLATOR", "auto").lower(),
            target_language=_env("TRANSLATE_TO", "zh-CN"),
            llm_api_key=_env("LLM_API_KEY") or _env("OPENAI_API_KEY"),
            llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_model=_env("LLM_MODEL", "gpt-4o-mini"),
            deepl_api_key=_env("DEEPL_API_KEY"),
            cache_file=Path(_env("TRANSLATION_CACHE", str(PROJECT_ROOT / ".cache" / "translations.json"))),
        )
        return cls(
            search_url=_env("APPLE_JOBS_SEARCH_URL", DEFAULT_SEARCH_URL),
            locale=_env("APPLE_JOBS_LOCALE", "zh-cn"),
            state_file=Path(_env("STATE_FILE", str(PROJECT_ROOT / "state" / "seen_jobs.json"))),
            output_dir=Path(_env("OUTPUT_DIR", str(PROJECT_ROOT / "out"))),
            notify_on_first_run=_env_bool("NOTIFY_ON_FIRST_RUN", False),
            request_timeout=float(_env_int("REQUEST_TIMEOUT", 30)),
            request_retries=_env_int("REQUEST_RETRIES", 3),
            mail=mail,
            translation=translation,
        )
