"""发送邮件。没有配置 SMTP 时自动降级为把邮件写到本地文件，方便先预览效果。"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from abc import ABC, abstractmethod
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from .config import MailConfig
from .email_render import EmailContent

log = logging.getLogger(__name__)


def build_message(content: EmailContent, config: MailConfig) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = content.subject
    message["From"] = formataddr(("Apple Pay 招聘监测", config.from_address))
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="applepay-job-watcher.local")
    message.set_content(content.text_body, subtype="plain", charset="utf-8")
    message.add_alternative(content.html_body, subtype="html", charset="utf-8")
    return message


class Mailer(ABC):
    @abstractmethod
    def send(self, content: EmailContent) -> str:
        """发送邮件并返回一句人类可读的结果说明。"""


class SMTPMailer(Mailer):
    def __init__(self, config: MailConfig) -> None:
        self.config = config

    def send(self, content: EmailContent) -> str:
        message = build_message(content, self.config)
        cfg = self.config
        context = ssl.create_default_context()
        if cfg.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=60, context=context)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=60)
        try:
            server.ehlo()
            if not cfg.use_ssl and cfg.use_starttls:
                server.starttls(context=context)
                server.ehlo()
            server.login(cfg.username, cfg.password)
            server.send_message(message)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:  # pragma: no cover - 关闭连接失败无需中断
                server.close()
        return f"邮件已发送至 {', '.join(cfg.recipients)}"


class FileMailer(Mailer):
    """把邮件落盘成 .html / .txt / .eml，用于 dry-run 或未配置 SMTP 的场景。"""

    def __init__(self, output_dir: Path, config: MailConfig) -> None:
        self.output_dir = Path(output_dir)
        self.config = config

    def send(self, content: EmailContent) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(content.subject)}"
        html_path = self.output_dir / f"{stem}.html"
        html_path.write_text(content.html_body, encoding="utf-8")
        (self.output_dir / f"{stem}.txt").write_text(content.text_body, encoding="utf-8")
        (self.output_dir / f"{stem}.eml").write_text(
            build_message(content, self.config).as_string(), encoding="utf-8"
        )
        return f"未发送邮件（dry-run），预览已写入 {html_path}"


def build_mailer(config: MailConfig, output_dir: Path, *, dry_run: bool) -> Mailer:
    if dry_run:
        return FileMailer(output_dir, config)
    if not config.configured:
        log.warning("SMTP 未配置完整（需要 SMTP_USERNAME / SMTP_PASSWORD），本次改为写入本地预览文件")
        return FileMailer(output_dir, config)
    return SMTPMailer(config)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-")
    return cleaned[:48] or "email"
