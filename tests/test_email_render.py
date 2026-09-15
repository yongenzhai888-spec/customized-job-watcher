from applepay_watch.apple import JobDetail
from applepay_watch.email_render import JobReport, build_email

SEARCH_URL = "https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY"


def make_report(**overrides) -> JobReport:
    detail = JobDetail(
        job_id="200678634-3715",
        title="SDET - Apple Pay Quality Engineer",
        url="https://jobs.apple.com/zh-cn/details/200678634-3715/sdet-apple-pay-quality-engineer",
        description="As a member of the Apple Pay Quality Team...",
        responsibilities="Architect automation frameworks\nBuild CI/CD pipelines",
        minimum_qualifications="5+ years of experience\nBS/MS in Computer Science",
        preferred_qualifications="Proficient in Java or Kotlin",
        team="软件和服务",
        locations=["上海"],
        posted_display="2026年8月19日",
        role_number="200678634-3715",
        weekly_hours="40 小时/周",
    )
    translation = {
        "title": "SDET - Apple Pay 质量工程师",
        "description": "作为 Apple Pay 质量团队的成员……",
        "responsibilities": "架构自动化框架\n构建 CI/CD 流水线",
        "minimum_qualifications": "5 年以上经验\n计算机科学学士/硕士学位",
        "preferred_qualifications": "精通 Java 或 Kotlin",
    }
    translation.update(overrides)
    return JobReport(detail=detail, translation=translation)


def test_subject_mentions_count_and_chinese_title():
    content = build_email([make_report()], search_url=SEARCH_URL)
    assert "新增 1 个岗位" in content.subject
    assert "SDET - Apple Pay 质量工程师" in content.subject


def test_subject_summarises_multiple_jobs():
    content = build_email([make_report(), make_report()], search_url=SEARCH_URL)
    assert "新增 2 个岗位" in content.subject
    assert "等 2 个岗位" in content.subject


def test_html_contains_link_requirements_and_chinese_body():
    content = build_email([make_report()], search_url=SEARCH_URL, engine="google")
    html = content.html_body

    assert "https://jobs.apple.com/zh-cn/details/200678634-3715" in html
    assert "SDET - Apple Pay 质量工程师" in html
    assert "SDET - Apple Pay Quality Engineer" in html  # 保留英文原名便于核对
    assert "任职要求（必备）" in html
    assert "架构自动化框架" in html
    assert "精通 Java 或 Kotlin" in html
    assert "2026年8月19日" in html
    assert "翻译引擎：google" in html


def test_text_body_mirrors_html_content():
    text = build_email([make_report()], search_url=SEARCH_URL).text_body

    assert "岗位链接：https://jobs.apple.com/zh-cn/details/200678634-3715" in text
    assert "【主要职责】" in text
    assert "· 架构自动化框架" in text
    assert "【任职要求（必备）】" in text


def test_untranslated_email_carries_a_notice():
    report = JobReport(detail=make_report().detail, translation={})
    content = build_email([report], search_url=SEARCH_URL, translated=False)

    assert "未检测到可用的翻译服务" in content.html_body
    assert "英文原文" in content.text_body
    # 没有译文时回退到官网英文原文，信息不能丢
    assert "Architect automation frameworks" in content.html_body


def test_html_escapes_untrusted_text():
    report = make_report(title='<script>alert("x")</script>')
    html = build_email([report], search_url=SEARCH_URL).html_body

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
