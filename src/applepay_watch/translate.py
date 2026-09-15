"""把 Apple 官网的英文岗位原文翻译成中文。

支持多种翻译后端，按可用性自动降级：
  1. llm       —— 任意 OpenAI 兼容接口（OpenAI / DeepSeek / 通义 / Moonshot 等），质量最好
  2. deepl     —— DeepL API（免费额度即可）
  3. google    —— Google 翻译网页接口，无需 key，但可能被限流
  4. mymemory  —— MyMemory 免费接口，无需 key，每日额度很小
  5. none      —— 不翻译，邮件里保留英文原文并标注说明

翻译按"行"进行，这样官网原文里一行一条的职责/要求在邮件中仍然是一条一条的。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path

from .apple import JobDetail
from .config import TranslationConfig
from .httpclient import HttpError, post_json, request

log = logging.getLogger(__name__)

LLM_BATCH_SIZE = 25
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def looks_chinese(text: str) -> bool:
    """Apple 偶尔会直接给中文文案，这时就不用再翻一遍。"""
    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return True
    return len(CJK_PATTERN.findall(stripped)) / len(stripped) > 0.2


class Translator(ABC):
    name = "base"

    @abstractmethod
    def translate_batch(self, texts: list[str]) -> list[str]:
        """输入若干段文本，返回等长的译文列表。"""

    def close(self) -> None:  # pragma: no cover - 默认无需清理
        return None


class NullTranslator(Translator):
    name = "none"

    def translate_batch(self, texts: list[str]) -> list[str]:
        return list(texts)


class LLMTranslator(Translator):
    name = "llm"

    SYSTEM_PROMPT = (
        "你是一名资深的科技公司招聘文案译者。将用户给出的 JSON 字符串数组逐条翻译成简体中文，"
        "用于向求职者介绍岗位。要求：保持条目数量与顺序完全一致；"
        "专有名词（Apple Pay、Wallet、iOS、CI/CD、Kubernetes、Swift 等）保留英文原样；"
        "译文简洁通顺、符合中文招聘语境，不要添加原文没有的信息，不要输出解释。"
        "只返回 JSON 数组本身。"
    )

    def __init__(self, api_key: str, base_url: str, model: str, target_language: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.target_language = target_language

    def translate_batch(self, texts: list[str]) -> list[str]:
        out: list[str] = []
        for i in range(0, len(texts), LLM_BATCH_SIZE):
            chunk = texts[i : i + LLM_BATCH_SIZE]
            out.extend(self._translate_chunk(chunk))
        return out

    def _translate_chunk(self, chunk: list[str]) -> list[str]:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"目标语言：{self.target_language}\n{json.dumps(chunk, ensure_ascii=False)}",
                },
            ],
        }
        data = post_json(
            f"{self.base_url}/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=120.0,
        )
        content = data["choices"][0]["message"]["content"].strip()
        parsed = _parse_json_array(content)
        if parsed is None or len(parsed) != len(chunk):
            raise HttpError(f"LLM 返回的译文条数与原文不一致（期望 {len(chunk)} 条）")
        return [str(item) for item in parsed]


class DeepLTranslator(Translator):
    name = "deepl"

    def __init__(self, api_key: str, target_language: str) -> None:
        self.api_key = api_key
        self.target = "ZH" if target_language.lower().startswith("zh") else target_language.upper()
        host = "api-free.deepl.com" if api_key.endswith(":fx") else "api.deepl.com"
        self.endpoint = f"https://{host}/v2/translate"

    def translate_batch(self, texts: list[str]) -> list[str]:
        body = urllib.parse.urlencode(
            [("target_lang", self.target), ("source_lang", "EN")]
            + [("text", text) for text in texts]
        ).encode("utf-8")
        raw = request(
            self.endpoint,
            method="POST",
            headers={
                "Authorization": f"DeepL-Auth-Key {self.api_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=body,
            timeout=60.0,
        )
        items = json.loads(raw).get("translations", [])
        if len(items) != len(texts):
            raise HttpError("DeepL 返回的译文条数与原文不一致")
        return [item.get("text", "") for item in items]


class GoogleWebTranslator(Translator):
    """Google 翻译的网页接口，无需 API key。

    优先用 ``clients5`` 端点，它接受多个 ``q`` 参数、一次请求翻译多段文本，
    被限流的概率也比 ``translate_a/single`` 低；单段文本过长时再退回后者。
    """

    name = "google"
    BATCH_ENDPOINT = "https://clients5.google.com/translate_a/t"
    SINGLE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
    MAX_ITEMS_PER_CALL = 10
    MAX_QUERY_CHARS = 1600

    def __init__(self, target_language: str) -> None:
        self.target = target_language

    def translate_batch(self, texts: list[str]) -> list[str]:
        results: list[str] = []
        for chunk in self._chunks(texts):
            if len(chunk) == 1 and len(chunk[0]) > self.MAX_QUERY_CHARS:
                results.append(self._translate_single(chunk[0]))
            else:
                results.extend(self._translate_chunk(chunk))
        return results

    def _chunks(self, texts: list[str]) -> list[list[str]]:
        chunks: list[list[str]] = []
        current: list[str] = []
        size = 0
        for text in texts:
            encoded = len(urllib.parse.quote(text))
            if current and (
                len(current) >= self.MAX_ITEMS_PER_CALL or size + encoded > self.MAX_QUERY_CHARS
            ):
                chunks.append(current)
                current, size = [], 0
            current.append(text)
            size += encoded
        if current:
            chunks.append(current)
        return chunks

    def _translate_chunk(self, chunk: list[str]) -> list[str]:
        query = urllib.parse.urlencode(
            [("client", "dict-chrome-ex"), ("sl", "en"), ("tl", self.target)]
            + [("q", text) for text in chunk]
        )
        raw = request(f"{self.BATCH_ENDPOINT}?{query}", timeout=30.0, retries=2)
        data = _load_google_json(raw)
        items = data if isinstance(data, list) else []
        flattened = [_flatten_google_item(item) for item in items]
        if len(flattened) != len(chunk):
            raise HttpError("Google 翻译返回的条数与原文不一致")
        return flattened

    def _translate_single(self, text: str) -> str:
        query = urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": self.target, "dt": "t", "q": text}
        )
        data = _load_google_json(request(f"{self.SINGLE_ENDPOINT}?{query}", timeout=30.0, retries=2))
        return "".join(seg[0] for seg in data[0] if seg and seg[0])


class MyMemoryTranslator(Translator):
    name = "mymemory"
    ENDPOINT = "https://api.mymemory.translated.net/get"

    def __init__(self, target_language: str) -> None:
        self.pair = f"en|{target_language}"

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [self._translate_one(text) for text in texts]

    def _translate_one(self, text: str) -> str:
        query = urllib.parse.urlencode({"q": text[:500], "langpair": self.pair})
        data = json.loads(request(f"{self.ENDPOINT}?{query}", timeout=30.0, retries=2))
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        if "MYMEMORY WARNING" in translated.upper() or not translated:
            raise HttpError("MyMemory 免费额度已用尽")
        return translated


class CachingTranslator(Translator):
    """给任意后端套一层磁盘缓存 + 失败降级。"""

    def __init__(self, backends: list[Translator], cache_file: Path, target_language: str) -> None:
        self.backends = backends or [NullTranslator()]
        self.cache_file = Path(cache_file)
        self.target_language = target_language
        self._cache = self._load_cache()
        self._dirty = False
        self.active = self.backends[0]

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.active.name

    def _load_cache(self) -> dict[str, str]:
        if not self.cache_file.is_file():
            return {}
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _key(self, engine: str, text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return f"{engine}:{self.target_language}:{digest}"

    def translate_batch(self, texts: list[str]) -> list[str]:
        results: list[str | None] = [None] * len(texts)
        pending: list[int] = []

        for idx, text in enumerate(texts):
            if not text.strip() or looks_chinese(text):
                results[idx] = text
                continue
            cached = self._cache.get(self._key(self.active.name, text))
            if cached is not None:
                results[idx] = cached
            else:
                pending.append(idx)

        while pending:
            backend = self.active
            chunk = [texts[i] for i in pending]
            try:
                translated = backend.translate_batch(chunk)
            except Exception as exc:  # 任一后端失败就降级到下一个
                log.warning("翻译后端 %s 失败：%s", backend.name, exc)
                if not self._switch_backend():
                    for idx in pending:
                        results[idx] = texts[idx]
                    break
                # 换后端后可能命中新的缓存键，重新过一遍
                still_pending = []
                for idx in pending:
                    cached = self._cache.get(self._key(self.active.name, texts[idx]))
                    if cached is not None:
                        results[idx] = cached
                    else:
                        still_pending.append(idx)
                pending = still_pending
                continue

            for idx, text in zip(pending, translated, strict=True):
                value = text.strip() or texts[idx]
                results[idx] = value
                self._cache[self._key(backend.name, texts[idx])] = value
                self._dirty = True
            pending = []

        self.flush()
        return [value if value is not None else texts[i] for i, value in enumerate(results)]

    def _switch_backend(self) -> bool:
        idx = self.backends.index(self.active)
        if idx + 1 >= len(self.backends):
            return False
        self.active = self.backends[idx + 1]
        log.info("切换到备用翻译后端：%s", self.active.name)
        return True

    def flush(self) -> None:
        if not self._dirty:
            return
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=0), encoding="utf-8"
        )
        self._dirty = False

    def close(self) -> None:
        self.flush()


def build_translator(config: TranslationConfig) -> CachingTranslator:
    """按配置组装翻译链。"""
    engine = (config.engine or "auto").lower()
    chain: list[Translator] = []

    def add_llm() -> None:
        if config.llm_api_key:
            chain.append(
                LLMTranslator(
                    config.llm_api_key,
                    config.llm_base_url,
                    config.llm_model,
                    config.target_language,
                )
            )

    def add_deepl() -> None:
        if config.deepl_api_key:
            chain.append(DeepLTranslator(config.deepl_api_key, config.target_language))

    if engine == "none":
        chain = [NullTranslator()]
    elif engine == "llm":
        add_llm()
    elif engine == "deepl":
        add_deepl()
    elif engine == "google":
        chain.append(GoogleWebTranslator(config.target_language))
    elif engine == "mymemory":
        chain.append(MyMemoryTranslator(config.target_language))
    else:  # auto
        add_llm()
        add_deepl()
        chain.append(GoogleWebTranslator(config.target_language))
        chain.append(MyMemoryTranslator(config.target_language))

    if not chain or chain[-1].name != "none":
        chain.append(NullTranslator())
    return CachingTranslator(chain, config.cache_file, config.target_language)


def translate_job(detail: JobDetail, translator: Translator) -> dict[str, str]:
    """逐行翻译岗位正文，返回 字段名 -> 中文文本。"""
    fields = detail.text_fields
    line_index: list[tuple[str, int]] = []
    lines: list[str] = []
    for field_name, value in fields.items():
        for pos, line in enumerate(value.split("\n")):
            line_index.append((field_name, pos))
            lines.append(line)

    translated_lines = translator.translate_batch(lines)

    grouped: dict[str, list[str]] = {name: [] for name in fields}
    for (field_name, _), text in zip(line_index, translated_lines, strict=True):
        grouped[field_name].append(text)
    return {name: "\n".join(values).strip() for name, values in grouped.items()}


def _load_google_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HttpError("Google 翻译接口返回了非 JSON 响应（可能被限流）") from exc


def _flatten_google_item(item) -> str:
    """接口有时返回 "译文"，有时返回 ["译文", "en"]，统一取译文。"""
    if isinstance(item, str):
        return item
    if isinstance(item, list) and item:
        return _flatten_google_item(item[0])
    return ""


def _parse_json_array(content: str) -> list | None:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", content, re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, list) else None
