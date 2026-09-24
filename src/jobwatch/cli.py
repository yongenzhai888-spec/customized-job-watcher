"""命令行入口：jobwatch <子命令>。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from .config import Config
from .email_render import EmailContent, TopSection, build_email
from .mailer import SMTPMailer, build_mailer, missing_mail_settings
from .match import MatchProfile, TopState, business_map_due, days_since, rebuild_top10
from .sources import build_source
from .store import JobStore
from .watcher import run_all

log = logging.getLogger("jobwatch")


def gh_annotate(level: str, title: str, message: str) -> None:
    """在 GitHub Actions 上把失败原因写成注解，直接显示在运行页的 Annotations 里，
    而不是只留一句 "Process completed with exit code 1"。"""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{escaped}", flush=True)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _selected(args: argparse.Namespace) -> list[str] | None:
    return args.source or None


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    report = run_all(
        config,
        dry_run=args.dry_run,
        force_notify=args.force,
        require_mail=args.require_mail,
        only=_selected(args),
    )
    print()
    for result in report.results:
        print(result.summary)
        marker = "·" if result.baseline_only else "+"
        for job in result.new_jobs:
            where = "、".join(job.locations) or "地点未标注"
            print(f"    {marker} {job.title}（{where}）")
    print(
        f"\n合计：新增 {report.new_job_count} 个岗位，发出 {report.emails_sent} 封邮件，"
        f"{len(report.failed)} 个来源失败。"
    )
    if report.failed:
        # 完整的排查指引在这里统一打印一次，上面的逐行摘要只留首行
        details = list(dict.fromkeys(r.error for r in report.failed))
        print("\n" + "─" * 60)
        for detail in details:
            print(detail)
        print("─" * 60)
        gh_annotate(
            "error",
            f"{len(report.failed)} 个来源失败",
            "\n\n".join(details),
        )
    return 1 if report.failed else 0


def _cmd_loop(args: argparse.Namespace, config: Config) -> int:
    interval = max(args.interval, 60)
    log.info("进入常驻模式，每 %s 秒检查一次（Ctrl+C 退出）", interval)
    while True:
        try:
            report = run_all(config, dry_run=args.dry_run, only=_selected(args))
            for result in report.results:
                log.info(result.summary)
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
    only = _selected(args)
    payload = []
    for spec in config.sources:
        if only and spec.source_id not in only:
            continue
        source = build_source(
            spec, timeout=config.request_timeout, retries=config.request_retries
        )
        jobs = source.list_jobs()
        if args.json:
            payload.append(
                {
                    "source": spec.source_id,
                    "name": spec.display_name,
                    "count": len(jobs),
                    "jobs": [
                        {
                            "id": j.job_id,
                            "title": j.title,
                            "url": j.url,
                            "locations": j.locations,
                            "posted_at": j.posted_at,
                        }
                        for j in jobs
                    ],
                }
            )
            continue
        print(f"\n### {spec.display_name}（{len(jobs)} 个岗位）")
        print(f"    {source.list_url}\n")
        for job in jobs[: args.limit]:
            print(f"· {job.title}")
            print(
                f"  地点：{'、'.join(job.locations) or '未标注'}　"
                f"发布：{job.posted_display or '未知'}"
            )
            print(f"  链接：{job.url}")
        if len(jobs) > args.limit:
            print(f"\n  …… 还有 {len(jobs) - args.limit} 个，用 --limit 调整显示数量")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace, config: Config) -> int:
    print(f"监测清单：{len(config.sources)} 个来源\n")
    for spec in config.sources:
        store = JobStore(config.state_file(spec.source_id))
        flags = []
        if not spec.enabled:
            flags.append("已禁用")
        if spec.translate:
            flags.append("需翻译")
        suffix = f"（{'、'.join(flags)}）" if flags else ""
        print(f"### {spec.display_name}{suffix}")
        print(f"    id：{spec.source_id}　provider：{spec.provider}")
        print(
            f"    收件人：{'、'.join(spec.recipients) or f'未设置（请设环境变量 {spec.recipients_env}）'}"
        )
        print(f"    上次运行：{store.last_run or '从未运行'}　已记录 {len(store.snapshot)} 个岗位")
        print()

    missing = missing_mail_settings(config.mail)
    print(f"发件通道：{config.mail.host}:{config.mail.port}")
    print(f"发件人：{config.mail.from_address or '未设置'}")
    print(f"状态：{'就绪' if not missing else '缺少 ' + '、'.join(missing) + '（将写本地预览）'}")
    print(f"翻译引擎：{config.translation.engine}")

    profile = MatchProfile.load()
    state = TopState.load()
    print(f"匹配口径：{_profile_label(profile)}")
    print(
        f"Top{profile.top_n}：{len(state.entries)} 个在榜，更新于 {state.updated_at or '从未更新'}"
    )
    if profile.business_map_path:
        waited = days_since(state.business_map_reminded_at)
        state_text = "待提醒" if business_map_due(profile, state) else "已提醒"
        print(
            f"业务地图：{profile.business_map_path}"
            f"（{'从未提醒' if waited is None else f'上次提醒 {waited} 天前'}·{state_text}）"
        )
    return 0


def _profile_label(profile: MatchProfile) -> str:
    if profile.loaded_from == "env":
        return "TOP10_PROFILE_JSON（从环境变量读入）"
    if profile.source_path:
        return str(profile.source_path)
    return "通用默认词表（未找到 local/profile.json）"


def _print_top10(state: TopState, profile: MatchProfile) -> None:
    if not state.entries:
        return
    print(f"\n最匹配你的 Top {len(state.entries)}（分数 → 年限门槛低的在前）：\n")
    for index, entry in enumerate(state.entries, start=1):
        flag = "　[门槛超配]" if entry.over_bar else ""
        print(f"{index:>3}. [{entry.score:>3}分] {entry.title}{flag}")
        print(f"      {entry.where} | {entry.source_id} | 年限：{entry.years_label}")
        print(f"      匹配点：{'、'.join(entry.hits) or '—'}")
        if entry.reason:
            print(f"      对口：{entry.reason}")
        if entry.blocker:
            print(f"      卡点：{entry.blocker}")
        print(f"      {entry.url}\n")
    if state.watch:
        print("方向对口但门槛超配、挂在观察名单：")
        for entry in state.watch:
            print(f"  · {entry.title}（{entry.where}，{entry.years_label}）")
        print()


def _cmd_top10(args: argparse.Namespace, config: Config) -> int:
    """看当前名单，或用 --rebuild 按口径重算一份。"""
    profile = MatchProfile.load()
    state = TopState.load()

    if not args.rebuild:
        if not state.entries:
            print("还没有名单。先跑一次 `python -m jobwatch top10 --rebuild` 建一份。")
            return 1
        if args.json:
            print(
                json.dumps(
                    [entry.to_dict() for entry in state.entries],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        _print_top10(state, profile)
        print(f"口径来源：{_profile_label(profile)}")
        print(f"名单文件：{state.path}")
        return 0

    only = _selected(args)
    candidates = []
    for spec in config.sources:
        if only and spec.source_id not in only:
            continue
        source = build_source(
            spec, timeout=config.request_timeout, retries=config.request_retries
        )
        jobs = source.list_jobs()
        candidates += [
            (ref, spec.source_id, spec.display_name) for ref in jobs
        ]
        print(f"{spec.display_name}：{len(jobs)} 个岗位参与打分")

    picked = rebuild_top10(profile, state, candidates=candidates)
    state.save()
    print(f"\n已重建 Top{len(picked)}，共 {len(candidates)} 个岗位参与排序。")
    print(f"口径来源：{_profile_label(profile)}")
    _print_top10(state, profile)
    return 0


def _cmd_preview(args: argparse.Namespace, config: Config) -> int:
    """抓取最新的几个岗位渲染成邮件预览，不改动状态、不发信。"""
    from .watcher import _build_postings

    only = _selected(args)
    for spec in config.sources:
        if only and spec.source_id not in only:
            continue
        source = build_source(
            spec, timeout=config.request_timeout, retries=config.request_retries
        )
        jobs = source.list_jobs()[: args.limit]
        if not jobs:
            print(f"{spec.display_name}：当前没有岗位，跳过预览")
            continue
        postings, engine = _build_postings(config, source, jobs)
        state = TopState.load()
        top_section = (
            TopSection(
                entries=list(state.entries),
                updated_at=state.updated_at,
                watch=[entry.title for entry in state.watch],
            )
            if state.entries
            else None
        )
        content = build_email(
            postings,
            source_name=spec.display_name,
            list_url=source.list_url,
            subject_prefix=config.mail.subject_prefix,
            translation_note=f"已翻译为中文（引擎：{engine}）。" if spec.translate else "",
            untranslated_warning=spec.translate and engine == "none",
            top_section=top_section,
            reminder="业务地图该更新了（这是 preview 出来的提醒示例，真实提醒按 30 天间隔触发）。"
            if args.with_reminder
            else "",
        )
        mailer = build_mailer(
            config.mail, spec.recipients, config.output_dir, dry_run=not args.send
        )
        print(f"\n主题：{content.subject}")
        print(mailer.send(content))
    return 0


def _cmd_mailtest(args: argparse.Namespace, config: Config) -> int:
    """验证发件配置是否可用——不抓岗位、不动状态，只连 SMTP。"""
    missing = missing_mail_settings(config.mail)
    if missing:
        print(f"发件配置不完整，缺少：{'、'.join(missing)}")
        print("请复制 .env.example 为 .env 并填写，详见 README 的「配置发信邮箱」一节。")
        return 1

    recipients = sorted({addr for spec in config.sources for addr in spec.recipients})
    if not recipients:
        print("watchlist 里所有来源都没有收件人，请设置对应的 MAIL_TO* 环境变量。")
        return 1

    print(f"发件服务器：{config.mail.host}:{config.mail.port}"
          f"（{'SSL' if config.mail.use_ssl else 'STARTTLS'}）")
    print(f"发件人：{config.mail.from_address}")
    print(f"收件人：{', '.join(recipients)}")
    print("正在登录……")
    mailer = SMTPMailer(config.mail, recipients)
    print(mailer.verify())

    if args.send:
        lines = "".join(
            f"<li>{spec.display_name} → {'、'.join(spec.recipients) or '未设置收件人'}</li>"
            for spec in config.sources
        )
        content = EmailContent(
            subject=f"{config.mail.subject_prefix} 配置测试成功",
            html_body=(
                '<div style="font-family:-apple-system,\'PingFang SC\',sans-serif;'
                'font-size:15px;line-height:1.7;color:#1d1d1f;">'
                "<p>收到这封邮件说明发信配置已经跑通。当前监测清单：</p>"
                f"<ul>{lines}</ul>"
                "<p>之后任一来源出现新岗位，你就会收到带岗位链接、职责和要求的通知邮件。</p></div>"
            ),
            text_body="收到这封邮件说明发信配置已经跑通。之后出现新岗位就会收到岗位通知。",
        )
        print(mailer.send(content))
    else:
        print("加 --send 可以再发一封测试邮件，确认收件箱能收到。")
    return 0


def _cmd_seed(args: argparse.Namespace, config: Config) -> int:
    only = _selected(args)
    for spec in config.sources:
        if only and spec.source_id not in only:
            continue
        source = build_source(
            spec, timeout=config.request_timeout, retries=config.request_retries
        )
        jobs = source.list_jobs()
        JobStore(config.state_file(spec.source_id)).commit(jobs)
        print(f"{spec.display_name}：已把当前 {len(jobs)} 个岗位记为基线。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobwatch",
        description="每日监测各招聘官网的新增岗位，并把岗位职责与要求邮件发给你。",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_source_filter(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "-s",
            "--source",
            action="append",
            metavar="ID",
            help="只处理指定来源（可重复），默认处理 watchlist 里全部来源",
        )

    run = sub.add_parser("run", help="执行一次监测（每日定时任务调用的就是它）")
    run.add_argument("--dry-run", action="store_true", help="不发邮件，只把邮件写到 out/ 目录")
    run.add_argument("--force", action="store_true", help="即使没有新增岗位也把当前岗位发一遍")
    run.add_argument(
        "--require-mail",
        action="store_true",
        help="发信未配置时直接报错退出，而不是降级成本地预览（定时任务建议开启）",
    )
    add_source_filter(run)
    run.set_defaults(func=_cmd_run)

    loop = sub.add_parser("loop", help="常驻进程，按固定间隔重复检查")
    loop.add_argument("--interval", type=int, default=86400, help="检查间隔（秒），默认 86400=每天")
    loop.add_argument("--dry-run", action="store_true", help="不发邮件，只写本地预览")
    add_source_filter(loop)
    loop.set_defaults(func=_cmd_loop)

    listing = sub.add_parser("list", help="列出各来源当前命中的岗位")
    listing.add_argument("--json", action="store_true", help="以 JSON 输出")
    listing.add_argument("--limit", type=int, default=20, help="每个来源最多显示几个，默认 20")
    add_source_filter(listing)
    listing.set_defaults(func=_cmd_list)

    status = sub.add_parser("status", help="查看监测清单、本地状态和发信配置")
    status.set_defaults(func=_cmd_status)

    preview = sub.add_parser("preview", help="用真实岗位渲染一封邮件预览")
    preview.add_argument("--limit", type=int, default=2, help="取最新的几个岗位，默认 2")
    preview.add_argument("--send", action="store_true", help="真的发出这封预览邮件")
    preview.add_argument(
        "--with-reminder",
        action="store_true",
        help="顺便把「更新业务地图」的月度提醒也渲染进去，方便看版式",
    )
    add_source_filter(preview)
    preview.set_defaults(func=_cmd_preview)

    mailtest = sub.add_parser("mailtest", help="验证发件邮箱配置是否可用")
    mailtest.add_argument("--send", action="store_true", help="登录成功后再发一封测试邮件")
    mailtest.set_defaults(func=_cmd_mailtest)

    seed = sub.add_parser("seed", help="把当前岗位记为基线（不发邮件）")
    add_source_filter(seed)
    seed.set_defaults(func=_cmd_seed)

    top10 = sub.add_parser("top10", help="查看「最匹配你的 Top N」名单，或按口径重建")
    top10.add_argument("--rebuild", action="store_true", help="重新抓全部来源、按口径重排名单")
    top10.add_argument("--json", action="store_true", help="以 JSON 输出当前名单")
    add_source_filter(top10)
    top10.set_defaults(func=_cmd_top10)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        config = Config.from_env()
        return args.func(args, config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("运行失败：%s", exc, exc_info=args.verbose)
        gh_annotate("error", "岗位监测运行失败", str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
