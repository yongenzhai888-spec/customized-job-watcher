"""把新增岗位渲染成邮件（HTML + 纯文本两个版本）。

邮件客户端对 CSS 支持有限，所以全部用内联样式，布局用最保守的写法，
在 Gmail 网页版、iOS 邮件和 Outlook 里都能正常显示。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime

from .apple import JobDetail

TEXT = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
ACCENT = "#0071e3"
BG = "#f5f5f7"


@dataclass(slots=True)
class JobReport:
    """一个岗位的官网原文 + 中文译文。"""

    detail: JobDetail
    translation: dict[str, str] = field(default_factory=dict)

    def field(self, name: str) -> str:
        """优先返回中文，没有译文时回退英文原文。"""
        value = (self.translation.get(name) or "").strip()
        return value or (getattr(self.detail, name, "") or "").strip()

    @property
    def title_cn(self) -> str:
        return self.field("title")

    @property
    def title_en(self) -> str:
        return self.detail.title

    def bullets(self, name: str) -> list[str]:
        return [line.strip(" •\t") for line in self.field(name).split("\n") if line.strip()]


@dataclass(slots=True)
class EmailContent:
    subject: str
    html_body: str
    text_body: str


def build_email(
    reports: list[JobReport],
    *,
    search_url: str,
    subject_prefix: str = "[Apple Pay 招聘监测]",
    translated: bool = True,
    engine: str = "",
    now: datetime | None = None,
) -> EmailContent:
    now = now or datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    count = len(reports)
    headline = reports[0].title_cn if count == 1 else f"{reports[0].title_cn} 等 {count} 个岗位"
    subject = f"{subject_prefix} 新增 {count} 个岗位 · {headline}"
    return EmailContent(
        subject=subject,
        html_body=_render_html(reports, search_url, date_str, translated, engine),
        text_body=_render_text(reports, search_url, date_str, translated),
    )


def _e(value: str) -> str:
    return html.escape(value or "", quote=True)


def _paragraphs(text: str) -> str:
    blocks = [block.strip() for block in text.split("\n") if block.strip()]
    return "".join(
        f'<p style="margin:0 0 10px;font-size:15px;line-height:1.7;color:{TEXT};">{_e(block)}</p>'
        for block in blocks
    )


def _bullet_list(items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(
        f'<li style="margin:0 0 8px;font-size:15px;line-height:1.7;color:{TEXT};">{_e(item)}</li>'
        for item in items
    )
    return f'<ul style="margin:0;padding-left:22px;">{lis}</ul>'


def _section(title: str, body: str) -> str:
    if not body:
        return ""
    return (
        f'<div style="margin:22px 0 0;">'
        f'<div style="font-size:13px;font-weight:600;letter-spacing:.06em;'
        f'text-transform:uppercase;color:{MUTED};margin:0 0 10px;">{_e(title)}</div>'
        f"{body}</div>"
    )


def _meta_row(label: str, value: str) -> str:
    if not value:
        return ""
    return (
        f'<tr><td style="padding:4px 14px 4px 0;font-size:13px;color:{MUTED};'
        f'white-space:nowrap;vertical-align:top;">{_e(label)}</td>'
        f'<td style="padding:4px 0;font-size:13px;color:{TEXT};">{_e(value)}</td></tr>'
    )


def _render_job_card(report: JobReport, index: int, total: int) -> str:
    detail = report.detail
    meta = "".join(
        [
            _meta_row("团队", report.field("team") or detail.team),
            _meta_row("工作地点", "、".join(detail.locations)),
            _meta_row("发布日期", detail.posted_display),
            _meta_row("职位编号", detail.role_number),
            _meta_row("工作时长", detail.weekly_hours),
        ]
    )
    title_en = (
        f'<div style="font-size:14px;color:{MUTED};margin:6px 0 0;">{_e(report.title_en)}</div>'
        if report.title_en and report.title_en != report.title_cn
        else ""
    )

    sections = "".join(
        [
            _section("岗位简介", _paragraphs(report.field("description") or report.field("summary"))),
            _section("主要职责", _bullet_list(report.bullets("responsibilities"))),
            _section("任职要求（必备）", _bullet_list(report.bullets("minimum_qualifications"))),
            _section("加分项", _bullet_list(report.bullets("preferred_qualifications"))),
        ]
    )

    return f"""
      <tr><td style="padding:0 0 18px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border:1px solid {LINE};border-radius:14px;">
          <tr><td style="padding:26px 26px 28px;">
            <div style="font-size:12px;color:{MUTED};letter-spacing:.06em;">岗位 {index} / {total}</div>
            <h2 style="margin:8px 0 0;font-size:21px;line-height:1.35;color:{TEXT};font-weight:600;">
              {_e(report.title_cn)}
            </h2>
            {title_en}
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin:16px 0 0;">
              {meta}
            </table>
            <div style="margin:20px 0 0;">
              <a href="{_e(detail.url)}" target="_blank"
                 style="display:inline-block;background:{ACCENT};color:#ffffff;text-decoration:none;
                        font-size:15px;font-weight:500;padding:11px 22px;border-radius:999px;">
                查看岗位并投递 &rsaquo;
              </a>
            </div>
            <div style="margin:10px 0 0;font-size:12px;color:{MUTED};word-break:break-all;">
              {_e(detail.url)}
            </div>
            {sections}
          </td></tr>
        </table>
      </td></tr>"""


def _render_html(
    reports: list[JobReport],
    search_url: str,
    date_str: str,
    translated: bool,
    engine: str,
) -> str:
    cards = "".join(
        _render_job_card(report, i, len(reports)) for i, report in enumerate(reports, start=1)
    )
    notice = (
        ""
        if translated
        else f'<p style="margin:0 0 18px;padding:12px 14px;background:#fff4e5;border-radius:10px;'
        f'font-size:13px;line-height:1.6;color:#8a5a00;">未检测到可用的翻译服务，本邮件保留了 '
        f"Apple 官网英文原文。配置 LLM_API_KEY 或 DEEPL_API_KEY 后即可自动翻译成中文。</p>"
    )
    engine_note = f"翻译引擎：{engine}。" if translated and engine else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apple Pay 新增岗位</title></head>
<body style="margin:0;padding:0;background:{BG};">
<div style="display:none;max-height:0;overflow:hidden;">
  {date_str}：Apple Pay 中国大陆岗位新增 {len(reports)} 个，含岗位职责与任职要求中文解读。
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};">
  <tr><td align="center" style="padding:28px 14px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:640px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',
                  'Helvetica Neue','Microsoft YaHei',Arial,sans-serif;">
      <tr><td style="padding:0 0 22px;">
        <div style="font-size:13px;color:{MUTED};letter-spacing:.06em;">{_e(date_str)} · 每日监测</div>
        <h1 style="margin:8px 0 10px;font-size:26px;line-height:1.3;color:{TEXT};font-weight:600;">
          Apple Pay 新增 {len(reports)} 个招聘岗位
        </h1>
        <p style="margin:0;font-size:14px;line-height:1.7;color:{MUTED};">
          以下内容来自 Apple 招聘官网（中国大陆 · Apple Pay 产品线）并已翻译为中文。{_e(engine_note)}
        </p>
      </td></tr>
      <tr><td style="padding:0 0 4px;">{notice}</td></tr>
      {cards}
      <tr><td style="padding:10px 4px 0;border-top:1px solid {LINE};">
        <p style="margin:16px 0 0;font-size:12px;line-height:1.8;color:{MUTED};">
          监测范围：<a href="{_e(search_url)}" target="_blank"
          style="color:{ACCENT};text-decoration:none;">Apple Jobs · 中国大陆 · Apple Pay</a><br>
          岗位信息与要求均取自 Apple 官网原文，中文为机器翻译，以官网英文原文为准。<br>
          本邮件由你自建的 applepay-job-watcher 自动发送。
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def _render_text(
    reports: list[JobReport], search_url: str, date_str: str, translated: bool
) -> str:
    lines = [
        f"{date_str} · Apple Pay 招聘监测",
        f"发现 {len(reports)} 个新增岗位（中国大陆 · Apple Pay 产品线）",
        "",
    ]
    if not translated:
        lines += ["提示：未检测到可用翻译服务，以下为 Apple 官网英文原文。", ""]

    for i, report in enumerate(reports, start=1):
        detail = report.detail
        lines.append(f"{'=' * 56}")
        lines.append(f"[{i}/{len(reports)}] {report.title_cn}")
        if report.title_en and report.title_en != report.title_cn:
            lines.append(f"原职位名：{report.title_en}")
        lines.append(f"岗位链接：{detail.url}")
        meta = [
            ("团队", report.field("team") or detail.team),
            ("工作地点", "、".join(detail.locations)),
            ("发布日期", detail.posted_display),
            ("职位编号", detail.role_number),
            ("工作时长", detail.weekly_hours),
        ]
        for label, value in meta:
            if value:
                lines.append(f"{label}：{value}")

        body = report.field("description") or report.field("summary")
        if body:
            lines += ["", "【岗位简介】", body]
        for label, key in (
            ("主要职责", "responsibilities"),
            ("任职要求（必备）", "minimum_qualifications"),
            ("加分项", "preferred_qualifications"),
        ):
            items = report.bullets(key)
            if items:
                lines += ["", f"【{label}】"] + [f"· {item}" for item in items]
        lines.append("")

    lines += [
        "=" * 56,
        f"监测范围：{search_url}",
        "岗位信息与要求均取自 Apple 官网原文，中文为机器翻译，以官网英文原文为准。",
    ]
    return "\n".join(lines)
