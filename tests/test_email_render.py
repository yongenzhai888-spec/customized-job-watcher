from jobwatch.email_render import build_email
from jobwatch.models import JobPosting, JobSection, SectionStyle

LIST_URL = "https://jobs.apple.com/zh-cn/search?location=china-CHNC"


def posting(**overrides) -> JobPosting:
    defaults = {
        "job_id": "200678634-3715",
        "title": "SDET - Apple Pay 质量工程师",
        "url": "https://jobs.apple.com/zh-cn/details/200678634-3715/sdet",
        "meta": [("团队", "软件和服务"), ("工作地点", "上海"), ("发布日期", "2026年8月19日")],
        "sections": [
            JobSection("岗位简介", "作为 Apple Pay 质量团队的成员……"),
            JobSection("主要职责", "架构自动化框架\n构建 CI/CD 流水线", SectionStyle.BULLETS),
            JobSection("任职要求", "5 年以上经验\n熟悉测试方法论", SectionStyle.BULLETS),
        ],
    }
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_subject_names_the_source_and_count():
    content = build_email([posting()], source_name="Apple Pay · 中国大陆", list_url=LIST_URL)

    assert "Apple Pay · 中国大陆" in content.subject
    assert "新增 1 个岗位" in content.subject
    assert "SDET - Apple Pay 质量工程师" in content.subject


def test_subject_summarises_multiple_jobs():
    content = build_email([posting(), posting()], source_name="字节跳动", list_url=LIST_URL)

    assert "新增 2 个岗位" in content.subject
    assert "等 2 个岗位" in content.subject


def test_overlong_subject_is_truncated():
    content = build_email(
        [posting(title="岗" * 300)], source_name="某来源", list_url=LIST_URL
    )
    assert len(content.subject) <= 160


def test_html_contains_link_meta_and_every_section():
    html = build_email(
        [posting()], source_name="Apple Pay", list_url=LIST_URL, translation_note="引擎：google。"
    ).html_body

    assert "https://jobs.apple.com/zh-cn/details/200678634-3715/sdet" in html
    assert "SDET - Apple Pay 质量工程师" in html
    assert "岗位简介" in html and "主要职责" in html and "任职要求" in html
    assert "架构自动化框架" in html
    assert "软件和服务" in html
    assert "引擎：google。" in html


def test_text_body_mirrors_html_content():
    text = build_email([posting()], source_name="Apple Pay", list_url=LIST_URL).text_body

    assert "岗位链接：https://jobs.apple.com/zh-cn/details/200678634-3715/sdet" in text
    assert "【主要职责】" in text
    assert "· 架构自动化框架" in text
    assert "团队：软件和服务" in text


def test_numbered_prefixes_from_source_are_stripped():
    """国内站点正文常自带 "1、"，邮件里已经有列表符号了，不该出现 "· 1、"。"""
    job = posting(
        sections=[JobSection("任职要求", "1、本科及以上学历\n2、5 年以上经验", SectionStyle.BULLETS)]
    )
    content = build_email([job], source_name="阿里国际", list_url=LIST_URL)

    assert "本科及以上学历" in content.html_body
    assert "1、本科及以上学历" not in content.html_body
    assert "· 本科及以上学历" in content.text_body


def test_untranslated_warning_only_when_requested():
    without = build_email([posting()], source_name="Apple Pay", list_url=LIST_URL)
    assert "未检测到可用的翻译服务" not in without.html_body

    with_warning = build_email(
        [posting()], source_name="Apple Pay", list_url=LIST_URL, untranslated_warning=True
    )
    assert "未检测到可用的翻译服务" in with_warning.html_body
    assert "英文原文" in with_warning.text_body


def test_job_without_sections_still_renders_link():
    content = build_email([posting(sections=[])], source_name="某来源", list_url=LIST_URL)

    assert "https://jobs.apple.com/zh-cn/details/200678634-3715/sdet" in content.html_body
    assert "https://jobs.apple.com/zh-cn/details/200678634-3715/sdet" in content.text_body


def test_html_escapes_untrusted_text():
    html = build_email(
        [posting(title='<script>alert("x")</script>')], source_name="某来源", list_url=LIST_URL
    ).html_body

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
