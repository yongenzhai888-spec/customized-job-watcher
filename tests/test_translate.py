import pytest

from jobwatch.config import TranslationConfig
from jobwatch.translate import (
    CachingTranslator,
    GoogleWebTranslator,
    NullTranslator,
    Translator,
    build_translator,
    looks_chinese,
)


class FakeTranslator(Translator):
    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.fail = fail
        self.calls: list[list[str]] = []

    def translate_batch(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("后端不可用")
        return [f"[{self.name}]{text}" for text in texts]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("精通 Java 或 Kotlin", True),
        ("Proficient in Java or Kotlin", False),
        ("", True),
        ("CI/CD 流水线", True),
    ],
)
def test_looks_chinese(text, expected):
    assert looks_chinese(text) is expected


def test_falls_back_to_next_backend_when_one_fails(tmp_path):
    translator = CachingTranslator(
        [FakeTranslator("broken", fail=True), FakeTranslator("working")],
        tmp_path / "cache.json",
        "zh-CN",
    )

    assert translator.translate_batch(["Hello"]) == ["[working]Hello"]
    assert translator.name == "working"


def test_returns_original_text_when_all_backends_fail(tmp_path):
    translator = CachingTranslator(
        [FakeTranslator("a", fail=True), FakeTranslator("b", fail=True)],
        tmp_path / "cache.json",
        "zh-CN",
    )
    assert translator.translate_batch(["Hello"]) == ["Hello"]


def test_chinese_and_empty_text_are_not_sent_to_backend(tmp_path):
    backend = FakeTranslator("backend")
    translator = CachingTranslator([backend], tmp_path / "cache.json", "zh-CN")

    assert translator.translate_batch(["软件和服务", "   ", "Hello"]) == [
        "软件和服务",
        "   ",
        "[backend]Hello",
    ]
    assert backend.calls == [["Hello"]]


def test_translations_are_cached_on_disk(tmp_path):
    cache = tmp_path / "cache.json"
    CachingTranslator([FakeTranslator("backend")], cache, "zh-CN").translate_batch(["Hello"])

    second = FakeTranslator("backend")
    result = CachingTranslator([second], cache, "zh-CN").translate_batch(["Hello"])

    assert result == ["[backend]Hello"]
    assert second.calls == []


def test_google_batches_requests_by_size():
    translator = GoogleWebTranslator("zh-CN")
    chunks = translator._chunks(["short"] * 25)

    assert all(len(chunk) <= translator.MAX_ITEMS_PER_CALL for chunk in chunks)
    assert sum(len(chunk) for chunk in chunks) == 25

    long_text = "word " * 1000
    assert translator._chunks([long_text, "short"]) == [[long_text], ["short"]]


def test_build_translator_engine_selection(tmp_path):
    base = {"cache_file": tmp_path / "c.json", "target_language": "zh-CN"}

    auto_no_key = build_translator(TranslationConfig(engine="auto", **base))
    assert [b.name for b in auto_no_key.backends] == ["google", "mymemory", "none"]

    with_key = build_translator(TranslationConfig(engine="auto", llm_api_key="sk-test", **base))
    assert with_key.backends[0].name == "llm"

    disabled = build_translator(TranslationConfig(engine="none", **base))
    assert isinstance(disabled.backends[0], NullTranslator)

    # 指定了 llm 却没有 key 时不该崩溃，而是退化成不翻译
    assert [b.name for b in build_translator(TranslationConfig(engine="llm", **base)).backends] == [
        "none"
    ]
