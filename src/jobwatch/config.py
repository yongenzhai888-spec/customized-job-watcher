"""集中管理配置。

- 监测哪些来源：watchlist.json（可进仓库，里面没有任何隐私信息）
- 收件人和密码：环境变量 / .env（不进仓库）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WATCHLIST = PROJECT_ROOT / "watchlist.json"


class ConfigError(RuntimeError):
    """配置文件写错了。"""


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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    return raw.lower() in {"1", "true", "yes", "y", "on"} if raw else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def parse_recipients(raw: str) -> list[str]:
    return [addr.strip() for addr in raw.replace(";", ",").split(",") if addr.strip()]


@dataclass(slots=True)
class MailConfig:
    host: str = "smtp.gmail.com"
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    use_ssl: bool = False
    use_starttls: bool = True
    subject_prefix: str = "[岗位监测]"

    @property
    def transport_ready(self) -> bool:
        """发信通道本身是否可用（不含收件人，收件人是按来源配的）。"""
        return bool(self.host and self.username and self.password)

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
class SourceSpec:
    """watchlist.json 里的一条监测配置。"""

    source_id: str
    display_name: str
    provider: str
    recipients: list[str] = field(default_factory=list)
    recipients_env: str = "MAIL_TO"
    translate: bool = False
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def has_recipients(self) -> bool:
        return bool(self.recipients)


@dataclass(slots=True)
class Config:
    sources: list[SourceSpec] = field(default_factory=list)
    state_dir: Path = PROJECT_ROOT / "state"
    output_dir: Path = PROJECT_ROOT / "out"
    notify_on_first_run: bool = False
    request_timeout: float = 30.0
    request_retries: int = 3
    mail: MailConfig = field(default_factory=MailConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)

    def source(self, source_id: str) -> SourceSpec:
        for spec in self.sources:
            if spec.source_id == source_id:
                return spec
        known = "、".join(s.source_id for s in self.sources) or "（空）"
        raise ConfigError(f"watchlist 里没有 id={source_id!r} 的来源，已配置：{known}")

    def state_file(self, source_id: str) -> Path:
        return self.state_dir / f"{source_id}.json"

    @classmethod
    def from_env(cls, watchlist: Path | None = None) -> Config:
        load_dotenv()
        mail = MailConfig(
            host=_env("SMTP_HOST", "smtp.gmail.com"),
            port=_env_int("SMTP_PORT", 587),
            username=_env("SMTP_USERNAME"),
            password=_env("SMTP_PASSWORD"),
            sender=_env("MAIL_FROM"),
            use_ssl=_env_bool("SMTP_SSL", False),
            use_starttls=_env_bool("SMTP_STARTTLS", True),
            subject_prefix=_env("MAIL_SUBJECT_PREFIX", "[岗位监测]"),
        )
        translation = TranslationConfig(
            engine=_env("TRANSLATOR", "auto").lower(),
            target_language=_env("TRANSLATE_TO", "zh-CN"),
            llm_api_key=_env("LLM_API_KEY") or _env("OPENAI_API_KEY"),
            llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_model=_env("LLM_MODEL", "gpt-4o-mini"),
            deepl_api_key=_env("DEEPL_API_KEY"),
            cache_file=Path(
                _env("TRANSLATION_CACHE", str(PROJECT_ROOT / ".cache" / "translations.json"))
            ),
        )
        return cls(
            sources=load_watchlist(_watchlist_path(watchlist)),
            state_dir=Path(_env("STATE_DIR", str(PROJECT_ROOT / "state"))),
            output_dir=Path(_env("OUTPUT_DIR", str(PROJECT_ROOT / "out"))),
            notify_on_first_run=_env_bool("NOTIFY_ON_FIRST_RUN", False),
            request_timeout=float(_env_int("REQUEST_TIMEOUT", 30)),
            request_retries=_env_int("REQUEST_RETRIES", 3),
            mail=mail,
            translation=translation,
        )


def _watchlist_path(override: Path | None) -> Path:
    from_env = _env("WATCHLIST")
    if from_env:
        return Path(from_env)
    return override or DEFAULT_WATCHLIST


def load_watchlist(path: Path) -> list[SourceSpec]:
    if not path.is_file():
        raise ConfigError(f"找不到监测清单 {path}，可从仓库里的 watchlist.json 复制一份")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法的 JSON：{exc}") from exc

    entries = raw.get("sources") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} 里没有 sources 列表")

    specs: list[SourceSpec] = []
    seen: set[str] = set()
    for entry in entries:
        source_id = str(entry.get("id") or "").strip()
        provider = str(entry.get("provider") or "").strip()
        if not source_id or not provider:
            raise ConfigError(f"{path} 里有条目缺少 id 或 provider：{entry}")
        if source_id in seen:
            raise ConfigError(f"{path} 里 id={source_id!r} 重复了")
        seen.add(source_id)
        recipients_env = str(entry.get("recipients_env") or "MAIL_TO").strip()
        specs.append(
            SourceSpec(
                source_id=source_id,
                display_name=str(entry.get("name") or source_id),
                provider=provider,
                recipients=parse_recipients(_env(recipients_env)),
                recipients_env=recipients_env,
                translate=bool(entry.get("translate", False)),
                enabled=bool(entry.get("enabled", True)),
                params=dict(entry.get("params") or {}),
            )
        )
    return specs
