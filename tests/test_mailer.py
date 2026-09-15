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


def test_dry_run_and_missing_credentials_both_use_file_mailer(tmp_path):
    configured = MailConfig(username="me@gmail.com", password="pw")
    assert isinstance(build_mailer(configured, tmp_path, dry_run=True), FileMailer)
    assert isinstance(build_mailer(configured, tmp_path, dry_run=False), SMTPMailer)
    assert isinstance(build_mailer(MailConfig(), tmp_path, dry_run=False), FileMailer)
