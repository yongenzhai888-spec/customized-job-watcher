"""把新增岗位渲染成邮件（HTML + 纯文本两个版本）。

邮件客户端对 CSS 支持有限，所以全部用内联样式，布局用最保守的写法，
在 Gmail 网页版、QQ 邮箱、iOS 邮件和 Outlook 里都能正常显示。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime

from .match import TopEntry
from .models import JobPosting, SectionStyle

TEXT = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
ACCENT = "#0071e3"
BG = "#f5f5f7"
WARN = "#8a5a00"
WARN_BG = "#fff4e5"


@dataclass(slots=True)
class TopSection:
    """邮件末尾的「最匹配你的 Top N」区块。"""

    entries: list[TopEntry]
    updated_at: str = ""
    entered: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    watch: list[str] = field(default_factory=list)


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
    top_section: TopSection | None = None,
    reminder: str = "",
    now: datetime | None = None,
) -> EmailContent:
    now = now or datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    count = len(postings)
    headline = postings[0].title if count == 1 else f"{postings[0].title} 等 {count} 个岗位"
    subject = f"{subject_prefix} {source_name} 新增 {count} 个岗位 · {headline}"
    if top_section and (top_section.entered or top_section.dropped):
        subject += " · Top10 有更新"
    return EmailContent(
        subject=_truncate(subject, 160),
        html_body=_render_html(
            postings,
            source_name,
            list_url,
            date_str,
            translation_note,
            untranslated_warning,
            top_section,
            reminder,
        ),
        text_body=_render_text(
            postings,
            source_name,
            list_url,
            date_str,
            untranslated_warning,
            top_section,
            reminder,
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


def _stamp(iso_text: str) -> str:
    """名单更新时间在邮件里显示成本地可读格式，而不是原始 ISO 串。"""
    if not iso_text:
        return ""
    try:
        moment = datetime.fromisoformat(iso_text)
    except ValueError:
        return iso_text
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def _top_change_line(section: TopSection) -> str:
    parts = []
    if section.entered:
        parts.append("本次新进 " + "、".join(section.entered))
    if section.dropped:
        parts.append("掉出 " + "、".join(section.dropped))
    return "；".join(parts)


def _render_top_card(entry: TopEntry, index: int, total: int) -> str:
    """与新增岗位卡同一个版式，但只放摘要（名单是自动排的，没有正文）。"""
    meta = "".join(
        _meta_row(label, value)
        for label, value in (
            ("来源", entry.source_name),
            ("城市", entry.where),
            ("年限门槛", entry.years_label),
        )
        if value
    )
    chips = "".join(
        f'<span style="display:inline-block;margin:8px 6px 0 0;padding:3px 9px;'
        f'background:{BG};border-radius:999px;font-size:12px;color:{MUTED};">{_e(hit)}</span>'
        for hit in entry.hits[:8]
    )
    tag = ""
    if entry.over_bar:
        tag = (
            f'<span style="display:inline-block;margin-left:8px;padding:2px 8px;'
            f'background:{WARN_BG};border-radius:999px;font-size:12px;color:{WARN};">门槛超配</span>'
        )
    notes = ""
    if entry.reason or entry.blocker:
        rows = []
        if entry.reason:
            rows.append(f"对口：{_e(entry.reason)}")
        if entry.blocker:
            rows.append(f"卡点：{_e(entry.blocker)}")
        notes = (
            f'<p style="margin:10px 0 0;font-size:13px;line-height:1.7;color:{MUTED};">'
            + "<br>".join(rows)
            + "</p>"
        )
    return f"""
      <tr><td style="padding:0 0 12px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border:1px solid {LINE};border-radius:14px;">
          <tr><td style="padding:18px 20px 20px;">
            <div style="font-size:12px;color:{MUTED};letter-spacing:.06em;">
              第 {index} 名 / {total}{tag}
            </div>
            <h3 style="margin:6px 0 0;font-size:17px;line-height:1.4;font-weight:600;">
              <a href="{_e(entry.url)}" target="_blank"
                 style="color:{TEXT};text-decoration:none;">{_e(entry.title)}</a>
            </h3>
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin:10px 0 0;">
              {meta}
            </table>
            <div style="margin:0;">{chips}</div>
            {notes}
            <div style="margin:14px 0 0;">
              <a href="{_e(entry.url)}" target="_blank"
                 style="display:inline-block;background:{ACCENT};color:#ffffff;text-decoration:none;
                        font-size:14px;font-weight:500;padding:9px 18px;border-radius:999px;">
                查看岗位 &rsaquo;
              </a>
            </div>
          </td></tr>
        </table>
      </td></tr>"""


def _render_top_section(section: TopSection) -> tuple[str, str, str]:
    """返回三段：表头行 / 卡片行 / 表尾行。

    卡片本身就是 <tr>，所以必须与外层平级，不能塞进另一个 <tr> 的 <td> 里，
    否则浏览器会按 HTML 规则把嵌套的 tr 重排，邮件里顺序就乱了。
    """
    if not section.entries:
        return "", "", ""
    cards = "".join(
        _render_top_card(entry, index, len(section.entries))
        for index, entry in enumerate(section.entries, start=1)
    )
    update_note = (
        f'<p style="margin:10px 0 0;padding:10px 12px;background:{WARN_BG};border-radius:10px;'
        f'font-size:13px;line-height:1.6;color:{WARN};">{_e(_top_change_line(section))}</p>'
        if section.entered or section.dropped
        else ""
    )
    stamp = f"更新于 {_e(_stamp(section.updated_at))}" if section.updated_at else ""
    head = f"""
      <tr><td style="padding:30px 0 0;border-top:1px solid {LINE};">
        <div style="font-size:13px;color:{MUTED};letter-spacing:.06em;">{stamp}</div>
        <h2 style="margin:8px 0 6px;font-size:22px;line-height:1.35;color:{TEXT};font-weight:600;">
          最匹配你的 Top {len(section.entries)}
        </h2>
        <p style="margin:0 0 16px;font-size:14px;line-height:1.7;color:{MUTED};">
          按本地匹配口径（方向 + 年限门槛 + 城市）自动排序，只看方向是否对口，
          不代表投递优先级；新岗位一旦分数够高就会替换末位。
        </p>
        {update_note}
      </td></tr>"""
    watch_note = (
        f'<p style="margin:14px 0 0;font-size:12px;line-height:1.8;color:{MUTED};">'
        f"方向对口但门槛超配、暂挂观察：{_e('、'.join(section.watch))}</p>"
        if section.watch
        else ""
    )
    foot = (
        f'\n      <tr><td style="padding:4px 0 0;">{watch_note}</td></tr>'
        if watch_note
        else ""
    )
    return head, cards, foot


def _render_reminder(reminder: str) -> str:
    if not reminder:
        return ""
    paragraphs = "".join(
        f'<p style="margin:0 0 8px;font-size:13px;line-height:1.7;color:{WARN};">{_e(line)}</p>'
        for line in reminder.split("\n")
        if line.strip()
    )
    return f"""
      <tr><td style="padding:26px 0 0;">
        <div style="padding:16px 18px;background:{WARN_BG};border-radius:12px;">
          <div style="font-size:13px;font-weight:600;color:{WARN};margin:0 0 8px;">
            每月提醒 · 更新业务地图
          </div>
          {paragraphs}
        </div>
      </td></tr>"""


def _render_html(
    postings: list[JobPosting],
    source_name: str,
    list_url: str,
    date_str: str,
    translation_note: str,
    untranslated_warning: bool,
    top_section: TopSection | None = None,
    reminder: str = "",
) -> str:
    cards = "".join(
        _render_job_card(posting, i, len(postings)) for i, posting in enumerate(postings, start=1)
    )
    top_head, top_cards, top_foot = _render_top_section(top_section) if top_section else ("", "", "")
    reminder_block = _render_reminder(reminder)
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
      {top_head}
      {top_cards}
      {top_foot}
      {reminder_block}
      <tr><td style="padding:24px 4px 0;border-top:1px solid {LINE};">
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


def _render_top_text(section: TopSection) -> list[str]:
    if not section.entries:
        return []
    lines = ["=" * 56, f"最匹配你的 Top {len(section.entries)}", ""]
    if section.updated_at:
        lines.append(f"更新于 {_stamp(section.updated_at)}")
    change = _top_change_line(section)
    if change:
        lines.append(change)
    lines.append("")
    for index, entry in enumerate(section.entries, start=1):
        flag = "（门槛超配）" if entry.over_bar else ""
        lines.append(f"[{index}] {entry.title}{flag}")
        lines.append(
            f"    来源：{entry.source_name}　城市：{entry.where}　年限门槛：{entry.years_label}"
        )
        if entry.hits:
            lines.append(f"    匹配点：{'、'.join(entry.hits[:8])}")
        if entry.reason:
            lines.append(f"    对口：{entry.reason}")
        if entry.blocker:
            lines.append(f"    卡点：{entry.blocker}")
        lines.append(f"    链接：{entry.url}")
    if section.watch:
        lines += ["", f"方向对口但门槛超配、暂挂观察：{'、'.join(section.watch)}"]
    lines.append("")
    return lines


def _render_text(
    postings: list[JobPosting],
    source_name: str,
    list_url: str,
    date_str: str,
    untranslated_warning: bool,
    top_section: TopSection | None = None,
    reminder: str = "",
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

    if top_section:
        lines += _render_top_text(top_section)
    if reminder:
        lines += ["=" * 56, "【每月提醒】更新业务地图", ""]
        lines += [line for line in reminder.split("\n") if line.strip()]
        lines.append("")
    lines += ["=" * 56, f"监测范围：{list_url}", "岗位信息与要求均取自招聘官网原文，请以官网为准。"]
    return "\n".join(lines)
