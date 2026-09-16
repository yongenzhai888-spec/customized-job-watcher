"""各招聘站点共用的岗位模型。

不同公司的岗位字段差别很大：Apple 把内容拆成简介 / 职责 / 必备要求 / 加分项，
阿里和字节只有"岗位描述"和"任职要求"两段。与其塞进一组固定字段再到处判空，
不如让每个来源自己决定要呈现哪些章节，邮件按顺序渲染即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SectionStyle(str, Enum):
    PARAGRAPHS = "paragraphs"
    BULLETS = "bullets"


@dataclass(slots=True)
class JobSection:
    """岗位正文里的一段，例如"主要职责"。"""

    title: str
    body: str
    style: SectionStyle = SectionStyle.PARAGRAPHS

    @property
    def lines(self) -> list[str]:
        return [line.strip() for line in self.body.split("\n") if line.strip()]

    @property
    def is_empty(self) -> bool:
        return not self.body.strip()


@dataclass(slots=True)
class JobRef:
    """岗位的轻量标识，用于和历史快照比对，不含正文。"""

    job_id: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    posted_at: str = ""
    posted_display: str = ""
    payload: dict = field(default_factory=dict, repr=False)
    """来源自己留存的原始数据，避免为了拿正文再请求一次。"""


@dataclass(slots=True)
class JobPosting:
    """一个岗位的完整内容，邮件就是按它渲染的。"""

    job_id: str
    title: str
    url: str
    meta: list[tuple[str, str]] = field(default_factory=list)
    sections: list[JobSection] = field(default_factory=list)

    @property
    def translatable(self) -> list[str]:
        """需要送去翻译的文本：标题 + 各章节正文。"""
        return [self.title] + [section.body for section in self.sections]

    def with_translation(self, texts: list[str]) -> JobPosting:
        """用译文替换标题和正文，章节标题和元信息保持不变。"""
        if len(texts) != len(self.sections) + 1:
            return self
        return JobPosting(
            job_id=self.job_id,
            title=texts[0] or self.title,
            url=self.url,
            meta=list(self.meta),
            sections=[
                JobSection(title=section.title, body=text or section.body, style=section.style)
                for section, text in zip(self.sections, texts[1:], strict=True)
            ],
        )


def clean_text(value: object) -> str:
    """把站点返回的正文统一成去掉多余空白的多行文本。"""
    if not isinstance(value, str):
        return ""
    lines = [line.strip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()
