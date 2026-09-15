"""带重试的极简 HTTP 客户端，只依赖标准库，方便在 cron / CI 里零安装运行。"""

from __future__ import annotations

import gzip
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", errors="replace")


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 2.0,
) -> str:
    """发起请求并返回文本响应；5xx / 网络错误会按指数退避重试。"""
    merged = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    merged.update(headers or {})

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=merged, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _decode(resp.read(), resp.headers.get("Content-Encoding"))
        except urllib.error.HTTPError as exc:
            body = _decode(exc.read(), exc.headers.get("Content-Encoding"))[:500]
            last_error = HttpError(f"HTTP {exc.code} {url}: {body}", status=exc.code)
            if exc.code < 500 and exc.code != 429:
                raise last_error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = HttpError(f"请求失败 {url}: {exc}")

        if attempt < retries:
            delay = backoff ** attempt + random.uniform(0, 0.5)
            log.warning("第 %s 次请求失败，%.1fs 后重试：%s", attempt, delay, last_error)
            time.sleep(delay)

    assert last_error is not None
    raise last_error


def post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    retries: int = 3,
) -> dict:
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    text = request(
        url,
        method="POST",
        headers=merged,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
        retries=retries,
    )
    return json.loads(text)


def build_url(base: str, params: dict[str, str | int]) -> str:
    parts = urllib.parse.urlsplit(base)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), "")
    )
