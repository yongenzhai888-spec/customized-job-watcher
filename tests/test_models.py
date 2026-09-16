from jobwatch.models import JobPosting, JobSection, SectionStyle, clean_text
from jobwatch.watcher import _flatten, _unflatten


def posting() -> JobPosting:
    return JobPosting(
        job_id="1",
        title="Product Manager",
        url="https://example.com/1",
        meta=[("工作地点", "上海")],
        sections=[
            JobSection("岗位描述", "Line one\nLine two"),
            JobSection("任职要求", "Need A\nNeed B\nNeed C", SectionStyle.BULLETS),
        ],
    )


def test_clean_text_normalises_whitespace():
    assert clean_text("  第一行  \r\n  第二行 \n\n") == "第一行\n第二行"
    assert clean_text(None) == ""
    assert clean_text(123) == ""


def test_section_lines_drop_blank_entries():
    section = JobSection("要求", "第一条\n\n  \n第二条")
    assert section.lines == ["第一条", "第二条"]
    assert JobSection("空", "   ").is_empty is True


def test_flatten_produces_one_entry_per_line():
    lines = _flatten(posting())
    assert lines == ["Product Manager", "Line one", "Line two", "Need A", "Need B", "Need C"]


def test_unflatten_restores_section_structure():
    job = posting()
    translated = _unflatten(job, [f"[中]{line}" for line in _flatten(job)])

    assert translated.title == "[中]Product Manager"
    assert translated.sections[0].body == "[中]Line one\n[中]Line two"
    assert translated.sections[1].lines == ["[中]Need A", "[中]Need B", "[中]Need C"]
    # 章节标题、样式和元信息不参与翻译
    assert [s.title for s in translated.sections] == ["岗位描述", "任职要求"]
    assert translated.sections[1].style is SectionStyle.BULLETS
    assert translated.meta == job.meta


def test_translation_with_wrong_length_is_ignored():
    """翻译后端少返回了几条时，宁可原样保留，也不能把正文错位。"""
    job = posting()
    assert job.with_translation(["只有一条"]) is job


def test_empty_translation_falls_back_to_original():
    job = posting()
    translated = _unflatten(job, ["", "", "", "", "", ""])

    assert translated.title == "Product Manager"
    assert translated.sections[0].body == "Line one\nLine two"
