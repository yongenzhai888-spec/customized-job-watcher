"""编排整条工作流：抓取 → 比对新增 → 抓详情 → 翻译 → 发邮件 → 落状态。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .apple import AppleJobsClient, JobSummary
from .config import Config
from .email_render import JobReport, build_email
from .mailer import build_mailer
from .store import JobStore
from .translate import build_translator, looks_chinese, translate_job

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RunResult:
    total_jobs: int
    new_jobs: list[JobSummary] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    is_first_run: bool = False
    email_sent: bool = False
    message: str = ""

    @property
    def exit_summary(self) -> str:
        return (
            f"官网当前 {self.total_jobs} 个岗位；"
            f"新增 {len(self.new_jobs)} 个，下架 {len(self.removed_ids)} 个。{self.message}"
        )


def run_once(config: Config, *, dry_run: bool = False, force_notify: bool = False) -> RunResult:
    client = AppleJobsClient(
        config.search_url,
        locale=config.locale,
        timeout=config.request_timeout,
        retries=config.request_retries,
    )
    jobs = client.search()
    log.info("抓取完成，共 %s 个岗位", len(jobs))

    store = JobStore(config.state_file)
    diff = store.diff(jobs)
    result = RunResult(
        total_jobs=len(jobs),
        new_jobs=diff.new_jobs,
        removed_ids=diff.removed_ids,
        is_first_run=diff.is_first_run,
    )

    if diff.is_first_run and not (config.notify_on_first_run or force_notify):
        store.commit(jobs)
        result.message = (
            f"首次运行：已把当前 {len(jobs)} 个岗位记录为基线，不发送邮件；"
            "从下次开始只要出现新岗位就会收到邮件。"
        )
        log.info(result.message)
        return result

    notify_targets = diff.new_jobs or (jobs if force_notify else [])
    if not notify_targets:
        store.commit(jobs)
        result.message = "没有新增岗位，未发送邮件。"
        log.info(result.message)
        return result

    reports, engine = _build_reports(client, config, notify_targets)
    translated = engine != "none" and any(
        looks_chinese(report.field("responsibilities") or report.field("description"))
        for report in reports
    )

    content = build_email(
        reports,
        search_url=config.search_url,
        subject_prefix=config.mail.subject_prefix,
        translated=translated,
        engine=engine,
    )
    mailer = build_mailer(config.mail, config.output_dir, dry_run=dry_run)
    result.message = mailer.send(content)
    result.email_sent = not dry_run and config.mail.configured
    log.info(result.message)

    # 邮件发出去之后才更新状态，避免发送失败导致岗位被误标记为"已通知"。
    store.commit(jobs)
    return result


def _build_reports(
    client: AppleJobsClient, config: Config, jobs: list[JobSummary]
) -> tuple[list[JobReport], str]:
    """抓详情并翻译，返回岗位报告和实际生效的翻译引擎名。"""
    translator = build_translator(config.translation)
    reports: list[JobReport] = []
    try:
        for job in jobs:
            log.info("抓取岗位详情：%s（%s）", job.title, job.job_id)
            detail = client.fetch_detail(job)
            translation = translate_job(detail, translator)
            reports.append(JobReport(detail=detail, translation=translation))
        return reports, translator.name
    finally:
        translator.close()
