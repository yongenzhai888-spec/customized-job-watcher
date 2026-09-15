"""命令行入口：applepay-watch <子命令>。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime

from .apple import AppleJobsClient
from .config import Config
from .email_render import EmailContent, JobReport, build_email
from .mailer import build_mailer
from .store import JobStore
from .watcher import run_once

log = logging.getLogger("applepay_watch")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    result = run_once(
        config,
        dry_run=args.dry_run,
        force_notify=args.force,
        require_mail=args.require_mail,
    )
    print(result.exit_summary)
    marker = "·" if result.baseline_only else "+"
    for job in result.new_jobs:
        print(f"  {marker} {job.title}（{'、'.join(job.locations) or '地点未标注'}）")
    for job_id in result.removed_ids:
        print(f"  - 已下架：{job_id}")
    return 0


def _cmd_loop(args: argparse.Namespace, config: Config) -> int:
    interval = max(args.interval, 60)
    log.info("进入常驻模式，每 %s 秒检查一次（Ctrl+C 退出）", interval)
    while True:
        try:
            result = run_once(config, dry_run=args.dry_run)
            log.info(result.exit_summary)
        except KeyboardInterrupt:
            log.info("已退出")
            return 0
        except Exception as exc:  # 常驻模式下单次失败不应该让整个监测停掉
            log.error("本轮检查失败：%s", exc, exc_info=args.verbose)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("已退出")
            return 0


def _cmd_list(args: argparse.Namespace, config: Config) -> int:
    client = AppleJobsClient(config.search_url, locale=config.locale)
    jobs = client.search()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": job.job_id,
                        "title": job.title,
                        "locations": job.locations,
                        "team": job.team,
                        "posted_at": job.posted_at,
                        "url": client.detail_url(job),
                    }
                    for job in jobs
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"当前筛选条件下共 {len(jobs)} 个岗位：{config.search_url}\n")
    for job in jobs:
        print(f"· {job.title}")
        print(f"  地点：{'、'.join(job.locations) or '未标注'}　发布：{job.posted_display or '未知'}")
        print(f"  链接：{client.detail_url(job)}\n")
    return 0


def _cmd_status(args: argparse.Namespace, config: Config) -> int:
    store = JobStore(config.state_file)
    jobs = store.snapshot
    print(f"状态文件：{config.state_file}")
    print(f"上次运行：{store.last_run or '从未运行'}")
    print(f"已记录岗位：{len(jobs)} 个")
    for job_id, meta in sorted(jobs.items(), key=lambda kv: kv[1].get("first_seen", "")):
        print(f"  · {meta.get('title', job_id)}　首次发现：{meta.get('first_seen', '?')}")
    print(f"\n收件人：{', '.join(config.mail.recipients) or '未设置（请填 MAIL_TO）'}")
    print(f"SMTP：{'已配置 ' + config.mail.host if config.mail.configured else '未配置（将写本地预览）'}")
    print(f"翻译引擎：{config.translation.engine}")
    return 0


def _cmd_preview(args: argparse.Namespace, config: Config) -> int:
    """抓取最新的 N 个岗位并渲染成邮件预览，不改动状态，用来确认邮件长什么样。"""
    from .translate import build_translator, translate_job

    client = AppleJobsClient(config.search_url, locale=config.locale)
    jobs = client.search()[: args.limit]
    if not jobs:
        print("当前筛选条件下没有岗位，无法生成预览。")
        return 1
    translator = build_translator(config.translation)
    reports: list[JobReport] = []
    try:
        for job in jobs:
            detail = client.fetch_detail(job)
            reports.append(JobReport(detail=detail, translation=translate_job(detail, translator)))
        engine = translator.name
    finally:
        translator.close()

    content = build_email(
        reports,
        search_url=config.search_url,
        subject_prefix=config.mail.subject_prefix,
        translated=engine != "none",
        engine=engine,
        now=datetime.now(),
    )
    mailer = build_mailer(config.mail, config.output_dir, dry_run=not args.send)
    print(f"主题：{content.subject}")
    print(mailer.send(content))
    return 0


def _cmd_mailtest(args: argparse.Namespace, config: Config) -> int:
    """验证发件配置是否可用——不抓岗位、不动状态，只连 SMTP。"""
    from .mailer import SMTPMailer

    mail = config.mail
    missing = [
        name
        for name, value in (
            ("SMTP_HOST", mail.host),
            ("SMTP_USERNAME", mail.username),
            ("SMTP_PASSWORD", mail.password),
            ("MAIL_TO", ", ".join(mail.recipients)),
        )
        if not value
    ]
    if missing:
        print(f"发件配置不完整，缺少：{'、'.join(missing)}")
        print("请复制 .env.example 为 .env 并填写，详见 README 的「配置发信邮箱」一节。")
        return 1

    print(f"发件服务器：{mail.host}:{mail.port}（{'SSL' if mail.use_ssl else 'STARTTLS'}）")
    print(f"发件人：{mail.from_address}")
    print(f"收件人：{', '.join(mail.recipients)}")
    print("正在登录……")
    mailer = SMTPMailer(mail)
    print(mailer.verify())

    if args.send:
        content = EmailContent(
            subject=f"{mail.subject_prefix} 配置测试成功",
            html_body=(
                '<div style="font-family:-apple-system,\'PingFang SC\',sans-serif;'
                'font-size:15px;line-height:1.7;color:#1d1d1f;">'
                "<p>收到这封邮件说明发信配置已经跑通。</p>"
                "<p>之后一旦 Apple Pay 产品线出现新岗位，你就会在这个邮箱收到"
                "带岗位链接和中文职责、要求的通知邮件。</p></div>"
            ),
            text_body="收到这封邮件说明发信配置已经跑通。之后出现新岗位就会收到中文岗位通知。",
        )
        print(mailer.send(content))
    else:
        print("加 --send 可以再发一封测试邮件，确认收件箱能收到。")
    return 0


def _cmd_seed(args: argparse.Namespace, config: Config) -> int:
    client = AppleJobsClient(config.search_url, locale=config.locale)
    jobs = client.search()
    JobStore(config.state_file).commit(jobs)
    print(f"已把当前 {len(jobs)} 个岗位记录为基线，之后只有新岗位才会触发邮件。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="applepay-watch",
        description="监测 Apple 招聘官网 Apple Pay 产品线在中国大陆的新增岗位，并邮件通知（中文）。",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行一次监测（每日定时任务调用的就是它）")
    run.add_argument("--dry-run", action="store_true", help="不发邮件，只把邮件写到 out/ 目录")
    run.add_argument("--force", action="store_true", help="即使没有新增岗位也把当前岗位发一遍")
    run.add_argument(
        "--require-mail",
        action="store_true",
        help="发信未配置时直接报错退出，而不是降级成本地预览（定时任务建议开启）",
    )
    run.set_defaults(func=_cmd_run)

    loop = sub.add_parser("loop", help="常驻进程，按固定间隔重复检查")
    loop.add_argument("--interval", type=int, default=86400, help="检查间隔（秒），默认 86400=每天")
    loop.add_argument("--dry-run", action="store_true", help="不发邮件，只写本地预览")
    loop.set_defaults(func=_cmd_loop)

    listing = sub.add_parser("list", help="列出官网当前命中的岗位")
    listing.add_argument("--json", action="store_true", help="以 JSON 输出")
    listing.set_defaults(func=_cmd_list)

    status = sub.add_parser("status", help="查看本地状态和配置")
    status.set_defaults(func=_cmd_status)

    preview = sub.add_parser("preview", help="用真实岗位渲染一封邮件预览")
    preview.add_argument("--limit", type=int, default=2, help="取最新的几个岗位，默认 2")
    preview.add_argument("--send", action="store_true", help="真的发出这封预览邮件")
    preview.set_defaults(func=_cmd_preview)

    mailtest = sub.add_parser("mailtest", help="验证发件邮箱配置是否可用")
    mailtest.add_argument("--send", action="store_true", help="登录成功后再发一封测试邮件")
    mailtest.set_defaults(func=_cmd_mailtest)

    seed = sub.add_parser("seed", help="把当前岗位记为基线（不发邮件）")
    seed.set_defaults(func=_cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    config = Config.from_env()
    try:
        return args.func(args, config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("运行失败：%s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
