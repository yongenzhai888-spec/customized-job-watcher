import json
import re
from pathlib import Path

import pytest

from jobwatch.config import Config, MailConfig, SourceSpec, TranslationConfig

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> dict:
    return json.loads(load_fixture(name))


def wrap_hydration(payload: dict) -> str:
    """把一段 loaderData 包装成 Apple 招聘站那种带 hydration 脚本的 HTML。"""
    literal = json.dumps(json.dumps(payload, ensure_ascii=False))
    return (
        "<!DOCTYPE html><html><body><div id='root'>markup</div>"
        f"<script>window.__staticRouterHydrationData = JSON.parse({literal});</script>"
        "</body></html>"
    )


def read_hydration(html: str) -> dict:
    literal = re.search(r"JSON\.parse\((\".*?\")\);", html, re.S).group(1)
    return json.loads(json.loads(literal))


@pytest.fixture
def search_html() -> str:
    """来自 jobs.apple.com 真实响应的搜索页（已裁剪到必要字段）。"""
    return load_fixture("search_page.html")


@pytest.fixture
def detail_html() -> str:
    return load_fixture("detail_page.html")


@pytest.fixture
def detail_payload(detail_html) -> dict:
    return read_hydration(detail_html)


@pytest.fixture
def alibaba_categories() -> dict:
    return load_json_fixture("alibaba_categories.json")


@pytest.fixture
def alibaba_search() -> dict:
    return load_json_fixture("alibaba_search.json")


@pytest.fixture
def bytedance_search() -> dict:
    return load_json_fixture("bytedance_search.json")


def make_spec(source_id="demo", provider="apple", **overrides) -> SourceSpec:
    defaults = {
        "source_id": source_id,
        "display_name": overrides.pop("display_name", "示例来源"),
        "provider": provider,
        "recipients": ["someone@example.com"],
        "recipients_env": "MAIL_TO",
        "translate": False,
        "enabled": True,
        "params": {},
    }
    defaults.update(overrides)
    return SourceSpec(**defaults)


@pytest.fixture
def config_factory(tmp_path):
    def build(*specs: SourceSpec, **overrides) -> Config:
        return Config(
            sources=list(specs) or [make_spec()],
            state_dir=tmp_path / "state",
            output_dir=tmp_path / "out",
            mail=overrides.pop(
                "mail", MailConfig(username="bot@example.com", password="pw")
            ),
            translation=overrides.pop(
                "translation",
                TranslationConfig(engine="none", cache_file=tmp_path / "cache.json"),
            ),
            **overrides,
        )

    return build
