import json
import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


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
    return (FIXTURES / "search_page.html").read_text(encoding="utf-8")


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "detail_page.html").read_text(encoding="utf-8")


@pytest.fixture
def detail_payload(detail_html) -> dict:
    return read_hydration(detail_html)
