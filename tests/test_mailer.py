from applepay_watch.config import MailConfig
from applepay_watch.email_render import EmailContent
from applepay_watch.mailer import FileMailer, SMTPMailer, build_mailer, build_message

CONTENT = EmailContent(
    subject="[Apple Pay 招聘监测] 新增 1 个岗位 · SDET - Apple Pay 质量工程师",
    html_body="<html><body><p>中文正文</p></body></html>",
    text_body="中文正文",
)


def test_message_is_multipart_with_utf8_parts():
    config = MailConfig(username="me@gmail.com", password="pw", recipients=["you@gmail.com"])
    message = build_message(CONTENT, config)

    assert message["To"] == "you@gmail.com"
    assert "me@gmail.com" in message["From"]
    assert "Apple Pay" in message["Subject"]

    bodies = {part.get_content_type() for part in message.walk() if not part.is_multipart()}
    assert bodies == {"text/plain", "text/html"}
    assert "中文正文" in message.get_body(("plain",)).get_content()


def test_multiple_recipients_are_joined():
    config = MailConfig(
        username="me@gmail.com", password="pw", recipients=["a@x.com", "b@x.com"]
    )
    assert build_message(CONTENT, config)["To"] == "a@x.com, b@x.com"


def test_file_mailer_writes_preview_files(tmp_path):
    config = MailConfig(username="me@gmail.com", password="pw")
    message = FileMailer(tmp_path, config).send(CONTENT)

    suffixes = sorted(path.suffix for path in tmp_path.iterdir())
    assert suffixes == [".eml", ".html", ".txt"]
    assert "dry-run" in message
    html = next(tmp_path.glob("*.html")).read_text(encoding="utf-8")
    assert "中文正文" in html


class FakeSMTP:
    """记录 smtplib 调用顺序，验证 STARTTLS / SSL 两条路径都走对了。"""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.context = context
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
    config = MailConfig(username="me@gmail.com", password="pw", recipients=["you@gmail.com"])

    assert "you@gmail.com" in SMTPMailer(config).send(CONTENT)

    server = FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls == ["ehlo", "starttls", "ehlo", "login:me@gmail.com", "send", "quit"]


def test_ssl_flow_skips_starttls(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    config = MailConfig(
        host="smtp.qq.com",
        port=465,
        username="me@qq.com",
        password="pw",
        use_ssl=True,
        use_starttls=False,
    )

    SMTPMailer(config).send(CONTENT)

    server = FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.qq.com", 465)
    assert "starttls" not in server.calls


def test_connection_is_closed_even_when_sending_fails(monkeypatch):
    class BrokenSMTP(FakeSMTP):
        def send_message(self, message):
            raise RuntimeError("退信")

    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", BrokenSMTP)
    config = MailConfig(username="me@gmail.com", password="pw")

    try:
        SMTPMailer(config).send(CONTENT)
    except RuntimeError:
        pass

    assert "quit" in FakeSMTP.instances[0].calls


def test_dry_run_and_missing_credentials_both_use_file_mailer(tmp_path):
    configured = MailConfig(username="me@gmail.com", password="pw")
    assert isinstance(build_mailer(configured, tmp_path, dry_run=True), FileMailer)
    assert isinstance(build_mailer(configured, tmp_path, dry_run=False), SMTPMailer)
    assert isinstance(build_mailer(MailConfig(), tmp_path, dry_run=False), FileMailer)
