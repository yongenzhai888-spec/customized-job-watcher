"""编排整条工作流：逐个来源抓取 → 比对新增 → 取正文 → 按需翻译 → 发邮件 → 落状态。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config, SourceSpec
from .email_render import build_email
from .mailer import ConfigError, build_mailer, ensure_mail_configured
from .models import JobPosting, JobRef
from .sources import JobSource, build_source
from .store import JobStore
from .translate import build_translator

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceResult:
    source_id: str
    display_name: str
    total_jobs: int = 0
    new_jobs: list[JobRef] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    baseline_only: bool = False
    email_sent: bool = False
    skipped: bool = False
    error: str = ""
    message: str = ""

    @property
    def summary(self) -> str:
        if self.error:
            # 只取首行：完整的排查指引由调用方在末尾统一打印一次，
            # 否则同一段多行提示会在日志里重复三遍，反而看不清。
            return f"{self.display_name}：失败——{self.error.splitlines()[0]}"
        if self.skipped:
            return f"{self.display_name}：已跳过——{self.message}"
        if self.baseline_only:
            return f"{self.display_name}：当前 {self.total_jobs} 个岗位。{self.message}"
        return (
            f"{self.display_name}：当前 {self.total_jobs} 个岗位；"
            f"新增 {len(self.new_jobs)}，下架 {len(self.removed_ids)}。{self.message}"
        )


@dataclass(slots=True)
class RunReport:
    results: list[SourceResult] = field(default_factory=list)

    @property
    def failed(self) -> list[SourceResult]:
        return [r for r in self.results if r.error]

    @property
    def emails_sent(self) -> int:
        return sum(1 for r in self.results if r.email_sent)

    @property
    def new_job_count(self) -> int:
        return sum(len(r.new_jobs) for r in self.results)


def run_all(
    config: Config,
    *,
    dry_run: bool = False,
    force_notify: bool = False,
    require_mail: bool = False,
    only: list[str] | None = None,
) -> RunReport:
    """跑完所有来源。单个来源失败不影响其它来源。"""
    selected = [
        spec
        for spec in config.sources
        if spec.enabled and not (only and spec.source_id not in only)
    ]
    if require_mail and not dry_run:
        # 先把配置问题一次性说清楚，别等抓完一个来源才在半路上报错。
        ensure_mail_configured(config.mail)
        ensure_recipients(selected)

    for spec in config.sources:
        if not spec.enabled and not (only and spec.source_id not in only):
            log.info("[%s] 已在 watchlist 中禁用，跳过", spec.source_id)

    report = RunReport()
    for spec in selected:
        try:
            report.results.append(
                run_source(
                    config,
                    spec,
                    dry_run=dry_run,
                    force_notify=force_notify,
                    require_mail=require_mail,
                )
            )
        except Exception as exc:
            message = str(exc)
            log.error("[%s] 失败：%s", spec.source_id, message.splitlines()[0])
            log.debug("[%s] 完整错误", spec.source_id, exc_info=True)
            report.results.append(
                SourceResult(spec.source_id, spec.display_name, error=message)
            )
    return report


def ensure_recipients(specs: list[SourceSpec]) -> None:
    """定时任务里"跳过某个来源"等于悄悄不发邮件，最难发现，所以缺收件人就直接失败。"""
    missing = sorted({spec.recipients_env for spec in specs if not spec.has_recipients})
    if not missing:
        return
    names = "、".join(missing)
    affected = "、".join(
        spec.display_name for spec in specs if not spec.has_recipients
    )
    raise ConfigError(
        f"以下来源没有收件人：{affected}。\n"
        f"缺少环境变量：{names}。\n"
        "在 GitHub Actions 上到 Settings → Secrets and variables → Actions 的 Variables "
        f"里添加 {names}（值填收件邮箱）；本机运行则写进项目根目录的 .env。"
    )


def run_source(
    config: Config,
    spec: SourceSpec,
    *,
    dry_run: bool = False,
    force_notify: bool = False,
    require_mail: bool = False,
) -> SourceResult:
    result = SourceResult(spec.source_id, spec.display_name)

    if not spec.has_recipients and not dry_run:
        if require_mail:
            ensure_recipients([spec])
        result.skipped = True
        result.message = f"没有收件人，请设置环境变量 {spec.recipients_env}"
        log.warning("[%s] %s", spec.source_id, result.message)
        return result

    source = build_source(
        spec, timeout=config.request_timeout, retries=config.request_retries
    )
    jobs = source.list_jobs()
    result.total_jobs = len(jobs)
    log.info("[%s] 抓取完成，共 %s 个岗位", spec.source_id, len(jobs))

    store = JobStore(config.state_file(spec.source_id))
    diff = store.diff(jobs)
    result.new_jobs = diff.new_jobs
    result.removed_ids = diff.removed_ids

    if diff.is_first_run and not (config.notify_on_first_run or force_notify):
        store.commit(jobs)
        result.baseline_only = True
        result.message = (
            f"首次运行：已把当前 {len(jobs)} 个岗位记录为基线，不发送邮件；"
            "从下次开始只要出现新岗位就会收到邮件。"
        )
        log.info("[%s] %s", spec.source_id, result.message)
        return result

    targets = diff.new_jobs or (jobs if force_notify else [])
    if not targets:
        store.commit(jobs)
        result.message = "没有新增岗位，未发送邮件。"
        log.info("[%s] %s", spec.source_id, result.message)
        return result

    postings, engine = _build_postings(config, source, targets)
    untranslated = spec.translate and engine == "none"
    note = f"已翻译为中文（引擎：{engine}）。" if spec.translate and not untranslated else ""

    content = build_email(
        postings,
        source_name=spec.display_name,
        list_url=source.list_url,
        subject_prefix=config.mail.subject_prefix,
        translation_note=note,
        untranslated_warning=untranslated,
    )
    mailer = build_mailer(
        config.mail,
        spec.recipients,
        config.output_dir,
        dry_run=dry_run,
        require_mail=require_mail,
    )
    result.message = mailer.send(content)
    result.email_sent = not dry_run and config.mail.transport_ready and spec.has_recipients
    log.info("[%s] %s", spec.source_id, result.message)

    # 邮件发出去之后才更新状态，避免发送失败导致岗位被误标记为"已通知"。
    store.commit(jobs)
    return result


def _build_postings(
    config: Config, source: JobSource, refs: list[JobRef]
) -> tuple[list[JobPosting], str]:
    """取正文并按需翻译，返回岗位内容和实际生效的翻译引擎名。"""
    postings = [_fetch(source, ref) for ref in refs]
    if not source.translate:
        return postings, "none"

    translator = build_translator(config.translation)
    try:
        translated = []
        for posting in postings:
            texts = translator.translate_batch(_flatten(posting))
            translated.append(_unflatten(posting, texts))
        return translated, translator.name
    finally:
        translator.close()


def _flatten(posting: JobPosting) -> list[str]:
    """按行送翻译，这样官网原文里一行一条的职责/要求在邮件里仍是一条一条的。"""
    lines: list[str] = [posting.title]
    for section in posting.sections:
        lines.extend(section.body.split("\n"))
    return lines


def _unflatten(posting: JobPosting, texts: list[str]) -> JobPosting:
    cursor = 1
    bodies = [posting.title if not texts else texts[0]]
    for section in posting.sections:
        count = len(section.body.split("\n"))
        bodies.append("\n".join(texts[cursor : cursor + count]).strip() or section.body)
        cursor += count
    return posting.with_translation(bodies)


def _fetch(source: JobSource, ref: JobRef) -> JobPosting:
    log.info("[%s] 取岗位正文：%s（%s）", source.source_id, ref.title, ref.job_id)
    try:
        return source.fetch_posting(ref)
    except Exception as exc:
        # 单个岗位取不到正文不该拖垮整封邮件，退化成只给标题和链接。
        log.warning("[%s] 岗位 %s 正文获取失败：%s", source.source_id, ref.job_id, exc)
        return JobPosting(job_id=ref.job_id, title=ref.title, url=ref.url)


__all__ = ["RunReport", "SourceResult", "run_all", "run_source"]
