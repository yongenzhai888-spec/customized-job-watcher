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


class AuthError(RuntimeError):
    """SMTP 登录被拒绝。附带针对该邮箱服务商的排查建议。"""


class SMTPMailer(Mailer):
    def __init__(self, config: MailConfig) -> None:
        self.config = config

    def _connect(self) -> smtplib.SMTP:
        """建立连接并完成登录，调用方负责 quit。"""
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
        except smtplib.SMTPAuthenticationError as exc:
            _close(server)
            raise AuthError(auth_hint(cfg, exc)) from exc
        except Exception:
            _close(server)
            raise
        return server

    def verify(self) -> str:
        """只验证能否登录，不发任何邮件。"""
        _close(self._connect())
        return f"SMTP 登录成功：{self.config.username} @ {self.config.host}:{self.config.port}"

    def send(self, content: EmailContent) -> str:
        message = build_message(content, self.config)
        server = self._connect()
        try:
            server.send_message(message)
        finally:
            _close(server)
        return f"邮件已发送至 {', '.join(self.config.recipients)}"


def auth_hint(config: MailConfig, exc: Exception) -> str:
    """把 SMTP 的认证失败翻译成「接下来该干什么」。"""
    host = config.host.lower()
    if "gmail" in host or "google" in host:
        advice = (
            "Gmail 不接受账号密码，必须用 16 位「应用专用密码」，而它又要求先开启两步验证：\n"
            "  1. 开启两步验证：https://myaccount.google.com/signinoptions/twosv\n"
            "  2. 创建应用专用密码：https://myaccount.google.com/apppasswords\n"
            "     （若这里提示「您的账号不支持您正在尝试的设置」，说明第 1 步还没真正生效）\n"
            "  3. 把生成的 16 位密码去掉空格填进 SMTP_PASSWORD\n"
            "如果账号加入了「高级保护计划」、两步验证只绑了实体安全密钥，"
            "或这是被管理员限制的 Workspace 账号，就拿不到应用专用密码，请改用 QQ / 163 邮箱发信。"
        )
    elif "qq.com" in host or "163.com" in host or "126.com" in host:
        advice = (
            "QQ / 163 邮箱需要在「设置 → 账户」里开启 SMTP 服务，并使用生成的「授权码」，"
            "而不是邮箱登录密码。端口用 465 并设置 SMTP_SSL=true、SMTP_STARTTLS=false。"
        )
    else:
        advice = "请确认该邮箱已开启 SMTP 服务，且 SMTP_PASSWORD 填的是服务商要求的授权码或专用密码。"
    return f"SMTP 登录被拒绝（{config.username} @ {config.host}）：{exc}\n\n{advice}"


def _close(server: smtplib.SMTP) -> None:
    try:
        server.quit()
    except smtplib.SMTPException:  # pragma: no cover - 关闭连接失败无需中断主流程
        server.close()


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


class ConfigError(RuntimeError):
    """发信配置不完整，且调用方要求必须真的把邮件发出去。"""


def missing_mail_settings(config: MailConfig) -> list[str]:
    return [
        name
        for name, value in (
            ("MAIL_TO", config.recipients),
            ("SMTP_HOST", config.host),
            ("SMTP_USERNAME", config.username),
            ("SMTP_PASSWORD", config.password),
        )
        if not value
    ]


def ensure_mail_configured(config: MailConfig) -> None:
    """配置不全就抛错。用在"必须真的发出去"的场景（比如定时任务）。"""
    missing = missing_mail_settings(config)
    if not missing:
        return
    raise ConfigError(
        f"发信配置不完整，缺少：{'、'.join(missing)}。\n"
        "在 GitHub Actions 上请到 Settings → Secrets and variables → Actions 配置："
        "SMTP_USERNAME / SMTP_PASSWORD 放 Secrets，MAIL_TO 放 Variables。\n"
        "在本机运行请把它们写进项目根目录的 .env（可从 .env.example 复制）。"
    )


def build_mailer(
    config: MailConfig, output_dir: Path, *, dry_run: bool, require_mail: bool = False
) -> Mailer:
    if dry_run:
        return FileMailer(output_dir, config)
    if require_mail:
        ensure_mail_configured(config)
    if not config.configured:
        log.warning(
            "发信配置不完整（需要 MAIL_TO / SMTP_USERNAME / SMTP_PASSWORD），本次改为写入本地预览文件"
        )
        return FileMailer(output_dir, config)
    return SMTPMailer(config)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-")
    return cleaned[:48] or "email"
