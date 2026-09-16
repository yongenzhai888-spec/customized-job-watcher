import smtplib

import pytest

from jobwatch.config import MailConfig
from jobwatch.email_render import EmailContent
from jobwatch.mailer import (
    AuthError,
    ConfigError,
    FileMailer,
    SMTPMailer,
    auth_hint,
    build_mailer,
    build_message,
    ensure_mail_configured,
)

CONTENT = EmailContent(
    subject="[岗位监测] 阿里国际 新增 1 个岗位 · 产品经理",
    html_body="<html><body><p>中文正文</p></body></html>",
    text_body="中文正文",
)


def cfg(**overrides) -> MailConfig:
    return MailConfig(**{"username": "bot@example.com", "password": "pw", **overrides})


def test_message_is_multipart_with_utf8_parts():
    message = build_message(CONTENT, cfg(), ["you@example.com"])

    assert message["To"] == "you@example.com"
    assert "bot@example.com" in message["From"]
    assert "阿里国际" in message["Subject"]

    bodies = {part.get_content_type() for part in message.walk() if not part.is_multipart()}
    assert bodies == {"text/plain", "text/html"}
    assert "中文正文" in message.get_body(("plain",)).get_content()


def test_multiple_recipients_are_joined():
    message = build_message(CONTENT, cfg(), ["a@x.com", "b@x.com"])
    assert message["To"] == "a@x.com, b@x.com"


class FakeSMTP:
    """记录 smtplib 调用顺序，验证 STARTTLS / SSL 两条路径都走对了。"""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: list = []
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(f"login:{username}")

    def send_message(self, message):
        self.calls.append("send")
        self.sent.append(message)

    def quit(self):
        self.calls.append("quit")


def test_starttls_flow(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    assert "you@example.com" in SMTPMailer(cfg(), ["you@example.com"]).send(CONTENT)

    server = FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls == ["ehlo", "starttls", "ehlo", "login:bot@example.com", "send", "quit"]


def test_ssl_flow_skips_starttls(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    config = cfg(host="smtp.qq.com", port=465, use_ssl=True, use_starttls=False)

    SMTPMailer(config, ["you@qq.com"]).send(CONTENT)

    server = FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.qq.com", 465)
    assert "starttls" not in server.calls


def test_verify_logs_in_without_sending(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    message = SMTPMailer(cfg(), ["you@example.com"]).verify()

    assert "登录成功" in message
    assert FakeSMTP.instances[0].sent == []
    assert FakeSMTP.instances[0].calls[-1] == "quit"


def test_gmail_auth_failure_explains_app_password_and_2fa(monkeypatch):
    class RejectingSMTP(FakeSMTP):
        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", RejectingSMTP)

    with pytest.raises(AuthError) as excinfo:
        SMTPMailer(cfg(host="smtp.gmail.com"), ["you@gmail.com"]).send(CONTENT)

    hint = str(excinfo.value)
    assert "应用专用密码" in hint and "两步验证" in hint
    assert "myaccount.google.com/apppasswords" in hint
    # 认证失败也不能泄漏连接
    assert FakeSMTP.instances[0].calls[-1] == "quit"


def test_qq_auth_failure_mentions_authorization_code():
    hint = auth_hint(cfg(host="smtp.qq.com", port=465), RuntimeError("535 login fail"))

    assert "授权码" in hint
    assert "应用专用密码" not in hint


def test_connection_is_closed_even_when_sending_fails(monkeypatch):
    class BrokenSMTP(FakeSMTP):
        def send_message(self, message):
            raise RuntimeError("退信")

    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", BrokenSMTP)

    with pytest.raises(RuntimeError):
        SMTPMailer(cfg(), ["you@example.com"]).send(CONTENT)

    assert "quit" in FakeSMTP.instances[0].calls


def test_file_mailer_writes_preview_files(tmp_path):
    message = FileMailer(tmp_path, cfg(), ["you@example.com"]).send(CONTENT)

    assert sorted(path.suffix for path in tmp_path.iterdir()) == [".eml", ".html", ".txt"]
    assert "dry-run" in message
    assert "中文正文" in next(tmp_path.glob("*.html")).read_text(encoding="utf-8")


def test_dry_run_and_incomplete_config_use_file_mailer(tmp_path):
    ready, to = cfg(), ["you@example.com"]
    assert isinstance(build_mailer(ready, to, tmp_path, dry_run=True), FileMailer)
    assert isinstance(build_mailer(ready, to, tmp_path, dry_run=False), SMTPMailer)
    # 缺密码或缺收件人都不该尝试连 SMTP
    assert isinstance(build_mailer(cfg(password=""), to, tmp_path, dry_run=False), FileMailer)
    assert isinstance(build_mailer(ready, [], tmp_path, dry_run=False), FileMailer)


def test_require_mail_raises_with_actionable_message(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        build_mailer(cfg(password=""), ["a@b.com"], tmp_path, dry_run=False, require_mail=True)

    assert "SMTP_PASSWORD" in str(excinfo.value)
    assert "Secrets" in str(excinfo.value)


def test_ensure_mail_configured_passes_when_ready():
    ensure_mail_configured(cfg())
