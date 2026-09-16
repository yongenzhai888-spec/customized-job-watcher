"""带重试的极简 HTTP 客户端，只依赖标准库，方便在 cron / CI 里零安装运行。"""

from __future__ import annotations

import gzip
import http.cookiejar
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

#: 这些状态码值得重试。429/5xx 是常规限流与故障；
#: 405 是字节招聘站被限流时的表现——它不是真的不支持 POST，
#: 同一个请求隔几秒重发就会成功。
RETRY_STATUSES = frozenset({405, 408, 425, 429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", errors="replace")


def _merge_headers(headers: dict[str, str] | None) -> dict[str, str]:
    merged = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    merged.update(headers or {})
    return merged


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 2.0,
    opener: urllib.request.OpenerDirector | None = None,
    allow_status: set[int] | None = None,
) -> str:
    """发起请求并返回文本响应。

    ``allow_status`` 里的状态码不会抛错，而是把响应体原样返回——
    用于那种"先拿一个 403 换取 CSRF token"的握手。
    """
    merged = _merge_headers(headers)
    open_func = opener.open if opener else urllib.request.urlopen

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=merged, method=method)
        try:
            with open_func(req, timeout=timeout) as resp:
                return _decode(resp.read(), resp.headers.get("Content-Encoding"))
        except urllib.error.HTTPError as exc:
            body = _decode(exc.read(), exc.headers.get("Content-Encoding"))
            if allow_status and exc.code in allow_status:
                return body
            last_error = HttpError(
                f"HTTP {exc.code} {url}: {body[:300]}", status=exc.code, body=body
            )
            if exc.code not in RETRY_STATUSES:
                raise last_error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = HttpError(f"请求失败 {url}: {exc}")

        if attempt < retries:
            delay = backoff**attempt + random.uniform(0, 0.5)
            log.warning("第 %s 次请求失败，%.1fs 后重试：%s", attempt, delay, last_error)
            time.sleep(delay)

    assert last_error is not None
    raise last_error


class CookieSession:
    """在多次请求之间保留 cookie，用于需要先握手再调用的接口。"""

    def __init__(self, *, timeout: float = 30.0, retries: int = 3) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.timeout = timeout
        self.retries = retries

    @property
    def cookies(self) -> dict[str, str]:
        return {cookie.name: cookie.value or "" for cookie in self.jar}

    def request(self, url: str, **kwargs) -> str:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("retries", self.retries)
        return request(url, opener=self.opener, **kwargs)


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
