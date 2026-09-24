"""编排整条工作流：逐个来源抓取 → 比对新增 → 取正文 → 按需翻译 → 发邮件 → 落状态。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config, SourceSpec
from .email_render import TopSection, build_email
from .mailer import ConfigError, build_mailer, ensure_mail_configured
from .match import (
    MatchProfile,
    TopState,
    TopUpdate,
    business_map_due,
    business_map_reminder,
    days_since,
    now_iso,
    today_str,
    update_top10,
)
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
        # 发信通道不通则一个来源都发不出去，直接中止；
        # 而某个来源缺收件人只影响它自己，配好的来源照常跑完（见 run_source）。
        ensure_mail_configured(config.mail)

    for spec in config.sources:
        if not spec.enabled and not (only and spec.source_id not in only):
            log.info("[%s] 已在 watchlist 中禁用，跳过", spec.source_id)

    report = RunReport()
    prepared_list: list[PreparedSource] = []
    for spec in selected:
        try:
            prepared_list.append(
                prepare_source(
                    config,
                    spec,
                    force_notify=force_notify,
                    dry_run=dry_run,
                    require_mail=require_mail,
                )
            )
        except Exception as exc:
            message = str(exc)
            log.error("[%s] 失败：%s", spec.source_id, message.splitlines()[0])
            log.debug("[%s] 完整错误", spec.source_id, exc_info=True)
            prepared_list.append(
                PreparedSource(
                    spec, SourceResult(spec.source_id, spec.display_name, error=message)
                )
            )
    report.results.extend(prepared.result for prepared in prepared_list)

    # 抓完之后才统一算 Top10：这样末尾那份名单看得到本轮全部来源的新岗位，
    # 而不是只看到"当前来源跑到哪一步"。
    top_section, reminder, carrier = _extras_for_run(prepared_list)
    carrier_sent = False
    for index, prepared in enumerate(prepared_list):
        if not prepared.will_send:
            continue
        is_carrier = index == carrier
        try:
            send_prepared(
                config,
                prepared,
                dry_run=dry_run,
                require_mail=require_mail,
                top_section=top_section if is_carrier else None,
                reminder=reminder if is_carrier else "",
            )
            # 只有真的发出去了才算送达：dry-run 与降级成本地预览都不算。
            if is_carrier and prepared.result.email_sent:
                carrier_sent = True
        except Exception as exc:
            message = str(exc)
            log.error("[%s] 失败：%s", prepared.spec.source_id, message.splitlines()[0])
            log.debug("[%s] 完整错误", prepared.spec.source_id, exc_info=True)
            prepared.result.error = message
            prepared.result.message = ""

    # 提醒和名单的计时都只在邮件真的发出去之后才推进，否则会被默默吃掉。
    if carrier_sent:
        _mark_delivered(reminder=bool(reminder), top10=top_section is not None)
    return report


def _extras_for_run(
    prepared_list: list[PreparedSource],
) -> tuple[TopSection | None, str, int]:
    """算本轮要挂在邮件末尾的东西：Top10 区块、月度提醒、以及挂给哪一封邮件。"""
    profile = MatchProfile.load()
    state = TopState.load()

    new_jobs = [
        (ref, prepared.spec.source_id, prepared.spec.display_name)
        for prepared in prepared_list
        for ref in prepared.targets
    ]
    removed_ids = {
        job_id for prepared in prepared_list for job_id in prepared.result.removed_ids
    }
    update = (
        update_top10(profile, state, new_jobs=new_jobs, removed_ids=removed_ids)
        if new_jobs or removed_ids
        else TopUpdate()
    )
    if update.changed:
        state.save()
        log.info(
            "[top10] 名单更新：新进 %s，掉出 %s",
            "、".join(e.title for e in update.entered) or "无",
            "、".join(e.title for e in update.dropped) or "无",
        )

    carrier = _carrier_index(prepared_list, profile)
    if carrier < 0:
        # 今天没有邮件要发（无新增），提醒留到下一轮，别把它吃掉
        return None, "", -1

    reminder = ""
    if business_map_due(profile, state):
        reminder = business_map_reminder(profile, days_since(state.business_map_reminded_at))

    # 每天只附一封：当天已经推过就不再重复，否则一天跑多次会反复打扰。
    if not state.entries:
        return None, reminder, carrier
    if state.top10_attached_on == today_str():
        log.info("[top10] 今天（%s）已经推过一次名单，这一封不再附", state.top10_attached_on)
        return None, reminder, carrier

    top_section = TopSection(
        entries=list(state.entries),
        updated_at=state.updated_at,
        entered=[entry.title for entry in update.entered],
        dropped=[entry.title for entry in update.dropped],
        watch=[entry.title for entry in state.watch],
    )
    return top_section, reminder, carrier


def _carrier_index(prepared_list: list[PreparedSource], profile: MatchProfile) -> int:
    """名单挂到哪一封邮件上：默认第一封真的发出去的，可用 attach_to 指定来源。"""
    senders = [i for i, prepared in enumerate(prepared_list) if prepared.will_send]
    if not senders:
        return -1
    if profile.attach_to:
        for index in senders:
            if prepared_list[index].spec.source_id in profile.attach_to:
                return index
        log.warning(
            "[top10] attach_to=%s 里没有今天要发信的来源，退回到第一封邮件",
            "、".join(profile.attach_to),
        )
    return senders[0]


def _mark_delivered(*, reminder: bool = False, top10: bool = False) -> None:
    """记下「月度提醒已送达」与「今天已经推过名单」。

    touch=False：这只改发送记录，不该动 updated_at——邮件里的“更新于”指的是
    名单本身最后一次变化的时间。
    """
    if not (reminder or top10):
        return
    state = TopState.load()
    if reminder:
        state.business_map_reminded_at = now_iso()
    if top10:
        state.top10_attached_on = today_str()
    state.save(touch=False)


def missing_recipients_error(recipients_env: str) -> ConfigError:
    """刻意不把来源名写进正文：多个来源共用一个变量时，
    CLI 末尾就能把这段一模一样的指引去重成一条。"""
    return ConfigError(
        f"没有收件人，需要设置环境变量 {recipients_env}。\n"
        "在 GitHub Actions 上到 Settings → Secrets and variables → Actions 的 Variables "
        f"里添加 {recipients_env}（值填收件邮箱）；本机运行则写进项目根目录的 .env。"
    )


@dataclass(slots=True)
class PreparedSource:
    """抓取与比对的结果。邮件还没发，状态也还没提交。"""

    spec: SourceSpec
    result: SourceResult
    source: JobSource | None = None
    jobs: list[JobRef] = field(default_factory=list)
    targets: list[JobRef] = field(default_factory=list)

    @property
    def will_send(self) -> bool:
        return self.source is not None and bool(self.targets)


def prepare_source(
    config: Config,
    spec: SourceSpec,
    *,
    force_notify: bool = False,
    dry_run: bool = False,
    require_mail: bool = False,
) -> PreparedSource:
    """抓取、比对新增，决定这封邮件要发哪些岗位；这一步不碰 SMTP。"""
    result = SourceResult(spec.source_id, spec.display_name)
    prepared = PreparedSource(spec, result)

    if not spec.has_recipients and not dry_run:
        if require_mail:
            # 定时任务里"跳过"等于悄悄不发邮件，最难发现，所以记成失败。
            raise missing_recipients_error(spec.recipients_env)
        result.skipped = True
        result.message = f"没有收件人，请设置环境变量 {spec.recipients_env}"
        log.warning("[%s] %s", spec.source_id, result.message)
        return prepared

    source = build_source(
        spec, timeout=config.request_timeout, retries=config.request_retries
    )
    prepared.source = source
    jobs = source.list_jobs()
    prepared.jobs = jobs
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
        return prepared

    prepared.targets = diff.new_jobs or (jobs if force_notify else [])
    if not prepared.targets:
        store.commit(jobs)
        result.message = "没有新增岗位，未发送邮件。"
        log.info("[%s] %s", spec.source_id, result.message)
    return prepared


def send_prepared(
    config: Config,
    prepared: PreparedSource,
    *,
    dry_run: bool = False,
    require_mail: bool = False,
    top_section: TopSection | None = None,
    reminder: str = "",
) -> None:
    """渲染并发出这一封邮件，成功之后才提交状态。"""
    spec, result, source = prepared.spec, prepared.result, prepared.source
    if source is None or not prepared.targets:
        return

    postings, engine = _build_postings(config, source, prepared.targets)
    untranslated = spec.translate and engine == "none"
    note = f"已翻译为中文（引擎：{engine}）。" if spec.translate and not untranslated else ""

    content = build_email(
        postings,
        source_name=spec.display_name,
        list_url=source.list_url,
        subject_prefix=config.mail.subject_prefix,
        translation_note=note,
        untranslated_warning=untranslated,
        top_section=top_section,
        reminder=reminder,
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
    JobStore(config.state_file(spec.source_id)).commit(prepared.jobs)


def run_source(
    config: Config,
    spec: SourceSpec,
    *,
    dry_run: bool = False,
    force_notify: bool = False,
    require_mail: bool = False,
    top_section: TopSection | None = None,
    reminder: str = "",
) -> SourceResult:
    """单来源跑完一整轮：抓取 → 比对 → 发信。run_all 走的是更细的两段式。"""
    prepared = prepare_source(
        config, spec, force_notify=force_notify, dry_run=dry_run, require_mail=require_mail
    )
    if prepared.will_send:
        send_prepared(
            config,
            prepared,
            dry_run=dry_run,
            require_mail=require_mail,
            top_section=top_section,
            reminder=reminder,
        )
    return prepared.result

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
