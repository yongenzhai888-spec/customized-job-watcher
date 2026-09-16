"""把新增岗位渲染成邮件（HTML + 纯文本两个版本）。

邮件客户端对 CSS 支持有限，所以全部用内联样式，布局用最保守的写法，
在 Gmail 网页版、QQ 邮箱、iOS 邮件和 Outlook 里都能正常显示。
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

from .models import JobPosting, SectionStyle

TEXT = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
ACCENT = "#0071e3"
BG = "#f5f5f7"


@dataclass(slots=True)
class EmailContent:
    subject: str
    html_body: str
    text_body: str


def build_email(
    postings: list[JobPosting],
    *,
    source_name: str,
    list_url: str,
    subject_prefix: str = "[岗位监测]",
    translation_note: str = "",
    untranslated_warning: bool = False,
    now: datetime | None = None,
) -> EmailContent:
    now = now or datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    count = len(postings)
    headline = postings[0].title if count == 1 else f"{postings[0].title} 等 {count} 个岗位"
    subject = f"{subject_prefix} {source_name} 新增 {count} 个岗位 · {headline}"
    return EmailContent(
        subject=_truncate(subject, 160),
        html_body=_render_html(
            postings, source_name, list_url, date_str, translation_note, untranslated_warning
        ),
        text_body=_render_text(
            postings, source_name, list_url, date_str, untranslated_warning
        ),
    )


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _e(value: str) -> str:
    return html.escape(value or "", quote=True)


def _paragraphs(lines: list[str]) -> str:
    return "".join(
        f'<p style="margin:0 0 10px;font-size:15px;line-height:1.7;color:{TEXT};">{_e(line)}</p>'
        for line in lines
    )


def _bullet_list(lines: list[str]) -> str:
    items = "".join(
        f'<li style="margin:0 0 8px;font-size:15px;line-height:1.7;color:{TEXT};">'
        f"{_e(_strip_bullet(line))}</li>"
        for line in lines
    )
    return f'<ul style="margin:0;padding-left:22px;">{items}</ul>'


def _strip_bullet(line: str) -> str:
    """站点原文常自带 "1、" "· " 这类前缀，邮件里已经有列表符号了，去掉更干净。"""
    return line.lstrip("•·-—*").lstrip().lstrip("0123456789").lstrip("、.）)． ").strip() or line


def _render_section(title: str, lines: list[str], style: SectionStyle) -> str:
    if not lines:
        return ""
    body = _bullet_list(lines) if style is SectionStyle.BULLETS else _paragraphs(lines)
    return (
        '<div style="margin:22px 0 0;">'
        f'<div style="font-size:13px;font-weight:600;letter-spacing:.06em;'
        f'color:{MUTED};margin:0 0 10px;">{_e(title)}</div>'
        f"{body}</div>"
    )


def _meta_row(label: str, value: str) -> str:
    return (
        f'<tr><td style="padding:4px 14px 4px 0;font-size:13px;color:{MUTED};'
        f'white-space:nowrap;vertical-align:top;">{_e(label)}</td>'
        f'<td style="padding:4px 0;font-size:13px;color:{TEXT};">{_e(value)}</td></tr>'
    )


def _render_job_card(posting: JobPosting, index: int, total: int) -> str:
    meta = "".join(_meta_row(label, value) for label, value in posting.meta if value)
    sections = "".join(
        _render_section(section.title, section.lines, section.style)
        for section in posting.sections
    )
    return f"""
      <tr><td style="padding:0 0 18px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border:1px solid {LINE};border-radius:14px;">
          <tr><td style="padding:26px 26px 28px;">
            <div style="font-size:12px;color:{MUTED};letter-spacing:.06em;">岗位 {index} / {total}</div>
            <h2 style="margin:8px 0 0;font-size:21px;line-height:1.35;color:{TEXT};font-weight:600;">
              {_e(posting.title)}
            </h2>
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin:16px 0 0;">
              {meta}
            </table>
            <div style="margin:20px 0 0;">
              <a href="{_e(posting.url)}" target="_blank"
                 style="display:inline-block;background:{ACCENT};color:#ffffff;text-decoration:none;
                        font-size:15px;font-weight:500;padding:11px 22px;border-radius:999px;">
                查看岗位并投递 &rsaquo;
              </a>
            </div>
            <div style="margin:10px 0 0;font-size:12px;color:{MUTED};word-break:break-all;">
              {_e(posting.url)}
            </div>
            {sections}
          </td></tr>
        </table>
      </td></tr>"""


def _render_html(
    postings: list[JobPosting],
    source_name: str,
    list_url: str,
    date_str: str,
    translation_note: str,
    untranslated_warning: bool,
) -> str:
    cards = "".join(
        _render_job_card(posting, i, len(postings)) for i, posting in enumerate(postings, start=1)
    )
    notice = (
        '<p style="margin:0 0 18px;padding:12px 14px;background:#fff4e5;border-radius:10px;'
        'font-size:13px;line-height:1.6;color:#8a5a00;">未检测到可用的翻译服务，本邮件保留了'
        "官网英文原文。配置 LLM_API_KEY 或 DEEPL_API_KEY 后即可自动翻译成中文。</p>"
        if untranslated_warning
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(source_name)} 新增岗位</title></head>
<body style="margin:0;padding:0;background:{BG};">
<div style="display:none;max-height:0;overflow:hidden;">
  {_e(date_str)}：{_e(source_name)} 新增 {len(postings)} 个岗位，含岗位职责与任职要求。
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};">
  <tr><td align="center" style="padding:28px 14px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:640px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',
                  'Helvetica Neue','Microsoft YaHei',Arial,sans-serif;">
      <tr><td style="padding:0 0 22px;">
        <div style="font-size:13px;color:{MUTED};letter-spacing:.06em;">{_e(date_str)} · 每日监测</div>
        <h1 style="margin:8px 0 10px;font-size:26px;line-height:1.3;color:{TEXT};font-weight:600;">
          {_e(source_name)} 新增 {len(postings)} 个招聘岗位
        </h1>
        <p style="margin:0;font-size:14px;line-height:1.7;color:{MUTED};">
          以下内容取自招聘官网原文。{_e(translation_note)}
        </p>
      </td></tr>
      <tr><td style="padding:0 0 4px;">{notice}</td></tr>
      {cards}
      <tr><td style="padding:10px 4px 0;border-top:1px solid {LINE};">
        <p style="margin:16px 0 0;font-size:12px;line-height:1.8;color:{MUTED};">
          监测范围：<a href="{_e(list_url)}" target="_blank"
          style="color:{ACCENT};text-decoration:none;">{_e(source_name)}</a><br>
          岗位信息与要求均取自招聘官网原文，请以官网为准。<br>
          本邮件由你自建的 job-watcher 自动发送。
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def _render_text(
    postings: list[JobPosting],
    source_name: str,
    list_url: str,
    date_str: str,
    untranslated_warning: bool,
) -> str:
    lines = [
        f"{date_str} · {source_name} 岗位监测",
        f"发现 {len(postings)} 个新增岗位",
        "",
    ]
    if untranslated_warning:
        lines += ["提示：未检测到可用翻译服务，以下为官网英文原文。", ""]

    for i, posting in enumerate(postings, start=1):
        lines.append("=" * 56)
        lines.append(f"[{i}/{len(postings)}] {posting.title}")
        lines.append(f"岗位链接：{posting.url}")
        for label, value in posting.meta:
            if value:
                lines.append(f"{label}：{value}")
        for section in posting.sections:
            if section.lines:
                lines += ["", f"【{section.title}】"]
                lines += [f"· {_strip_bullet(line)}" for line in section.lines]
        lines.append("")

    lines += ["=" * 56, f"监测范围：{list_url}", "岗位信息与要求均取自招聘官网原文，请以官网为准。"]
    return "\n".join(lines)
