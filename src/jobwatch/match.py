"""岗位匹配口径与 Top 10 维护。

口径来源是本地的「业务地图」文档，提炼结果放在 `local/profile.json`。
这是一份**本地口径**：`local/` 已被 .gitignore 忽略，不会进仓库、也不会被
GitHub Actions 的每日提交带上去。仓库里只保留一套通用默认词表，profile.json
存在时以它为准。

Top 10 是**跨来源**的一份名单，存在 `local/top10.json`：
每轮监测里出现的岗位都会打分，与现有名单比对后决定是否替换，替换规则见
`update_top10`。名单不进仓库，所以它只在跑得动本地的这台机器上生效。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT
from .models import JobRef

log = logging.getLogger(__name__)

STATE_VERSION = 1

#: 通用默认词表：只放招聘市场上到处都在用的方向词，不含任何业务背景信息。
#: 定向口径（跨境、分销、渠道库存、目标城市、年限上限等）请写进 local/profile.json。
DEFAULT_TITLE_WEIGHTS = {
    "商家": 12,
    "渠道": 12,
    "分销": 12,
    "经销商": 12,
    "交易": 10,
    "订单": 10,
    "履约": 12,
    "库存": 10,
    "供应链": 12,
    "物流": 9,
    "跨境": 10,
    "出海": 10,
    "国际化": 7,
    "商城": 8,
    "店铺": 7,
    "B2B": 12,
    "招商": 7,
    "开放平台": 6,
    "结算": 5,
    "支付": 4,
    "TMS": 10,
    "WMS": 10,
    "OMS": 10,
    "FBT": 12,
    "S&OP": 12,
    "进销存": 12,
    "补货": 12,
    "业财": 8,
    "计费": 6,
    "定价": 6,
    "电商": 6,
    "平台": 3,
    "行业解决方案": 6,
    "解决方案": 3,
    "经营分析": 5,
    "数据产品": 5,
    "商品": 5,
    "产品经理": 2,
    "产品专家": 2,
}

DEFAULT_NEGATIVE_WEIGHTS = {
    "内容": 10,
    "推荐": 10,
    "搜索推荐": 14,
    "直播": 8,
    "创作者": 12,
    "达人": 10,
    "广告": 10,
    "投放": 10,
    "商业化": 8,
    "游戏": 14,
    "小说": 14,
    "漫画": 14,
    "音乐": 14,
    "短剧": 14,
    "社交": 8,
    "本地生活": 14,
    "医疗": 12,
    "教育": 10,
    "用户增长": 8,
    "用户产品": 4,
    "C端": 8,
    "算法": 6,
    "大模型": 5,
    "风控": 6,
    "安全": 5,
    "硬件": 8,
    "芯片": 12,
    "机器人": 12,
    "招聘": 12,
    "HR": 12,
    "财务": 4,
    "法务": 12,
    "税务": 12,
    "采购": 4,
    "测试": 12,
    "运维": 12,
    "设计": 6,
    "运营": 3,
}

DEFAULT_LOCATION_BONUS = {
    "深圳": 8,
    "杭州": 6,
    "上海": 4,
    "广州": 3,
    "北京": 2,
    "香港": 0,
    "首尔": -8,
    "东京": -6,
    "新加坡": -4,
    "美国": -8,
    "西雅图": -8,
}

YEARS_MIN = re.compile(r"(\d+)\s*年\s*(?:以上|及以上|或以上)")
YEARS_RANGE = re.compile(r"(\d+)\s*[-~—至到]\s*\d+\s*年")
YEARS_CN = re.compile(r"([一二两三四五六七八九十]+)\s*年\s*(?:以上|及以上)")
CN_DIGITS = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str() -> str:
    """本地日期，用来实现「每天第一封邮件才附名单」。"""
    return datetime.now().strftime("%Y-%m-%d")


def match_dir() -> Path:
    """本地口径目录，可用环境变量 MATCH_DIR 改到别处。"""
    raw = os.environ.get("MATCH_DIR", "").strip()
    return Path(raw) if raw else PROJECT_ROOT / "local"


def extract_years(requirement: object) -> int | None:
    """从任职要求里抽出最低年限门槛；没写就返回 None（按未知处理）。"""
    text = requirement if isinstance(requirement, str) else str(requirement or "")
    if not text:
        return None
    for pattern in (YEARS_MIN, YEARS_RANGE):
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    match = YEARS_CN.search(text)
    if match:
        return CN_DIGITS.get(match.group(1))
    return None


@dataclass(slots=True)
class MatchProfile:
    """打分口径。默认值可以用 local/profile.json 整段覆盖。"""

    top_n: int = 10
    min_score: int = 12
    #: 新岗位要挤进名单，至少要高出末位这么多分，避免 1 分之差来回换人
    min_gain: int = 3
    #: 年限门槛超过这个数的岗位会被标成「超配」，只在名额空缺时才补位
    max_years: int = 3
    #: 岗位正文没写年限门槛时的轻微罚分：不写往往意味着门槛不低
    unknown_years_penalty: int = 3
    #: 每超一年扣多少分，以及封顶
    over_bar_penalty_per_year: int = 4
    over_bar_penalty_cap: int = 12
    title_weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_TITLE_WEIGHTS))
    negative_weights: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_NEGATIVE_WEIGHTS)
    )
    location_bonus: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LOCATION_BONUS))
    #: 名单挂到哪些来源的邮件末尾；留空 = 每轮只挂在第一封真的发出去的邮件上
    attach_to: list[str] = field(default_factory=list)
    #: 业务地图文档路径与提醒间隔（天）；path 为空则不提醒
    business_map_path: str = ""
    business_map_interval_days: int = 30
    #: 人工补充的逐岗位说明，键是岗位 id
    notes: dict[str, dict[str, str]] = field(default_factory=dict)
    source_path: Path | None = None
    #: default / file / env，用来在 status 里说清楚这份口径是从哪来的
    loaded_from: str = "default"

    @classmethod
    def load(cls, path: Path | None = None) -> MatchProfile:
        """口径的读取顺序：环境变量 > 本地文件 > 通用默认词表。

        留着环境变量这条路，是为了在 GitHub Actions 上也能排名单：
        口径（词表、年限上限、城市权重）放在仓库的 Secret 里，用
        TOP10_PROFILE_JSON 传进来——这样就不必把口径文件提交进仓库。
        """
        raw_json = os.environ.get("TOP10_PROFILE_JSON", "").strip()
        if raw_json:
            try:
                profile = cls.from_dict(json.loads(raw_json), None)
                profile.loaded_from = "env"
                return profile
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                log.warning("TOP10_PROFILE_JSON 不是合法口径（%s），改用本地口径文件", exc)

        path = path or match_dir() / "profile.json"
        profile = cls()
        if not path.is_file():
            log.info("没有找到匹配口径文件 %s，使用通用默认词表", path)
            return profile
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("匹配口径文件 %s 读不出来（%s），改用默认词表", path, exc)
            return profile
        profile = cls.from_dict(raw, path)
        profile.loaded_from = "file"
        return profile

    @classmethod
    def from_dict(cls, raw: dict, path: Path | None = None) -> MatchProfile:
        profile = cls(source_path=path)
        profile.top_n = int(raw.get("top_n", profile.top_n))
        profile.min_score = int(raw.get("min_score", profile.min_score))
        profile.min_gain = int(raw.get("min_gain", profile.min_gain))
        profile.max_years = int(raw.get("max_years", profile.max_years))
        profile.unknown_years_penalty = int(
            raw.get("unknown_years_penalty", profile.unknown_years_penalty)
        )
        profile.over_bar_penalty_per_year = int(
            raw.get("over_bar_penalty_per_year", profile.over_bar_penalty_per_year)
        )
        profile.over_bar_penalty_cap = int(
            raw.get("over_bar_penalty_cap", profile.over_bar_penalty_cap)
        )
        for key, attr in (
            ("title_weights", "title_weights"),
            ("negative_weights", "negative_weights"),
            ("location_bonus", "location_bonus"),
        ):
            if isinstance(raw.get(key), dict):
                setattr(profile, attr, {str(k): int(v) for k, v in raw[key].items()})
        top10 = raw.get("top10") or {}
        if isinstance(top10, dict) and isinstance(top10.get("attach_to"), list):
            profile.attach_to = [str(item) for item in top10["attach_to"]]
        map_cfg = raw.get("business_map") or {}
        if isinstance(map_cfg, dict):
            profile.business_map_path = str(map_cfg.get("path") or "")
            profile.business_map_interval_days = int(
                map_cfg.get("interval_days", profile.business_map_interval_days)
            )
        if isinstance(raw.get("notes"), dict):
            profile.notes = {
                str(job_id): {str(k): str(v) for k, v in value.items()}
                for job_id, value in raw["notes"].items()
                if isinstance(value, dict)
            }
        return profile


@dataclass(slots=True)
class Score:
    points: int
    hits: list[str] = field(default_factory=list)


def score_title(title: str, locations: list[str], profile: MatchProfile) -> Score:
    """标题 + 地点打分。命中词一并返回，邮件里用它解释「为什么被选中」。"""
    hits: list[str] = []
    points = 0
    lowered = (title or "").lower()
    for word, weight in profile.title_weights.items():
        if word and word.lower() in lowered:
            points += weight
            hits.append(word)
    for word, weight in profile.negative_weights.items():
        if word and word.lower() in lowered:
            points -= weight
            hits.append(f"−{word}")
    for city, bonus in profile.location_bonus.items():
        if any(city in loc for loc in locations or []):
            points += bonus
            if bonus:
                hits.append(city if bonus > 0 else f"−{city}")
            break
    return Score(points, hits)


def score_job(
    title: str, locations: list[str], requirement: object, profile: MatchProfile
) -> Score:
    """标题 + 地点 + 年限门槛一起打分。这是全流程唯一的打分入口。"""
    scored = score_title(title, locations, profile)
    years = extract_years(requirement)
    if years is None:
        if profile.unknown_years_penalty:
            scored.points -= profile.unknown_years_penalty
    elif years > profile.max_years:
        over = (years - profile.max_years) * profile.over_bar_penalty_per_year
        penalty = min(profile.over_bar_penalty_cap, over)
        if penalty:
            scored.points -= penalty
            scored.hits.append(f"−超配{years}年")
    return scored


@dataclass(slots=True)
class TopEntry:
    job_id: str
    title: str
    url: str
    source_id: str
    source_name: str
    locations: list[str] = field(default_factory=list)
    years: int | None = None
    score: int = 0
    hits: list[str] = field(default_factory=list)
    reason: str = ""
    blocker: str = ""
    entered_at: str = ""
    over_bar: bool = False

    @property
    def where(self) -> str:
        return "、".join(self.locations) or "地点未标注"

    @property
    def years_label(self) -> str:
        return "年限未标注" if self.years is None else f"{self.years} 年以上"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> TopEntry:
        return cls(
            job_id=str(raw.get("job_id", "")),
            title=str(raw.get("title", "")),
            url=str(raw.get("url", "")),
            source_id=str(raw.get("source_id", "")),
            source_name=str(raw.get("source_name", "")),
            locations=[str(x) for x in raw.get("locations") or []],
            years=raw.get("years"),
            score=int(raw.get("score", 0)),
            hits=[str(x) for x in raw.get("hits") or []],
            reason=str(raw.get("reason", "")),
            blocker=str(raw.get("blocker", "")),
            entered_at=str(raw.get("entered_at", "")),
            over_bar=bool(raw.get("over_bar", False)),
        )


@dataclass(slots=True)
class TopState:
    entries: list[TopEntry] = field(default_factory=list)
    watch: list[TopEntry] = field(default_factory=list)
    updated_at: str = ""
    business_map_reminded_at: str = ""
    #: 最近一次把名单附进邮件的日期（本地日期），用来保证每天只附一封
    top10_attached_on: str = ""
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> TopState:
        path = path or match_dir() / "top10.json"
        state = cls(path=path)
        if not path.is_file():
            return state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Top10 状态文件 %s 读不出来（%s），按空名单处理", path, exc)
            return state
        state.entries = [TopEntry.from_dict(item) for item in raw.get("entries") or []]
        state.watch = [TopEntry.from_dict(item) for item in raw.get("watch") or []]
        state.updated_at = str(raw.get("updated_at", ""))
        state.business_map_reminded_at = str(raw.get("business_map_reminded_at", ""))
        state.top10_attached_on = str(raw.get("top10_attached_on", ""))
        return state

    def save(self, *, touch: bool = True) -> None:
        """touch=False 用于「只记发送时间」的落盘，不改 updated_at（邮件里的“更新于”要指名单本身的变化）。"""
        if self.path is None:
            return
        if touch:
            self.updated_at = now_iso()
        payload = {
            "version": STATE_VERSION,
            "updated_at": self.updated_at,
            "business_map_reminded_at": self.business_map_reminded_at,
            "top10_attached_on": self.top10_attached_on,
            "entries": [entry.to_dict() for entry in self.entries],
            "watch": [entry.to_dict() for entry in self.watch],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def build_entry(
    ref: JobRef,
    *,
    source_id: str,
    source_name: str,
    profile: MatchProfile,
    entered_at: str | None = None,
) -> TopEntry:
    payload = ref.payload or {}
    years = extract_years(payload.get("requirement"))
    scored = score_job(ref.title, ref.locations, payload.get("requirement"), profile)
    note = profile.notes.get(ref.job_id, {})
    return TopEntry(
        job_id=ref.job_id,
        title=ref.title,
        url=ref.url,
        source_id=source_id,
        source_name=source_name,
        locations=list(ref.locations or []),
        years=years,
        score=scored.points,
        hits=scored.hits,
        reason=note.get("reason", ""),
        blocker=note.get("blocker", ""),
        entered_at=entered_at or now_iso(),
        over_bar=years is not None and years > profile.max_years,
    )


def sort_key(entry: TopEntry) -> tuple:
    """分数高的在前；同分时年限门槛低的在前；再同分按标题稳定排序。"""
    return (-entry.score, entry.years is None, entry.years or 0, entry.title)


@dataclass(slots=True)
class TopUpdate:
    entered: list[TopEntry] = field(default_factory=list)
    dropped: list[TopEntry] = field(default_factory=list)
    considered: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.entered or self.dropped)


def update_top10(
    profile: MatchProfile,
    state: TopState,
    *,
    new_jobs: list[tuple[JobRef, str, str]],
    removed_ids: set[str],
) -> TopUpdate:
    """把这一轮的新岗位与现有名单比对，决定是否替换。

    规则：
    1. 已下架的岗位直接出榜；
    2. 名额没满就补进去（超配的岗位只在名额空着时补，不挤掉够得着的人）；
    3. 名额满了，新岗位要高出末位 `min_gain` 分以上才替换；
    4. 分数低于 `min_score` 的岗位不进榜。
    """
    entries = list(state.entries)
    watch = list(state.watch)
    update = TopUpdate()

    if removed_ids:
        kept = []
        for entry in entries:
            if entry.job_id in removed_ids:
                update.dropped.append(entry)
            else:
                kept.append(entry)
        entries = kept

    known = {entry.job_id for entry in entries}
    known_titles = {entry.title.strip() for entry in entries}
    for ref, source_id, source_name in new_jobs:
        if ref.job_id in known:
            continue
        update.considered += 1
        candidate = build_entry(
            ref, source_id=source_id, source_name=source_name, profile=profile
        )
        if candidate.score < profile.min_score:
            continue
        if candidate.title.strip() in known_titles:
            # 同一岗位的另一个城市发布，不重复占名额
            continue
        if len(entries) < profile.top_n:
            entries.append(candidate)
            update.entered.append(candidate)
            known.add(candidate.job_id)
            known_titles.add(candidate.title.strip())
            continue
        if candidate.over_bar:
            # 方向对口但门槛超配：只在观察名单里露个面，不挤掉够得着的人
            watch.append(candidate)
            continue
        entries.sort(key=sort_key)
        weakest = entries[-1]
        if candidate.score >= weakest.score + profile.min_gain:
            entries.pop()
            update.dropped.append(weakest)
            watch.append(weakest)
            known_titles.discard(weakest.title.strip())
            entries.append(candidate)
            update.entered.append(candidate)
            known.add(candidate.job_id)
            known_titles.add(candidate.title.strip())

    entries.sort(key=sort_key)
    state.entries = entries[: profile.top_n]
    state.watch = sorted({e.job_id: e for e in watch}.values(), key=sort_key)[:5]
    return update


def dedupe_by_title(entries: list[TopEntry]) -> list[TopEntry]:
    """同一岗位常会按城市发多条（杭州一条、上海一条），名单里只留分最高的那条。

    对投递短名单来说，同一标题占两个名额没有意义；城市在卡片里就能看到。
    """
    best: dict[str, TopEntry] = {}
    for entry in sorted(entries, key=sort_key):
        best.setdefault(entry.title.strip(), entry)
    return list(best.values())


def rebuild_top10(
    profile: MatchProfile,
    state: TopState,
    *,
    candidates: list[tuple[JobRef, str, str]],
) -> list[TopEntry]:
    """全量重建：给所有岗位打分后取前 N，够得着的优先。"""
    scored = [
        build_entry(ref, source_id=sid, source_name=name, profile=profile)
        for ref, sid, name in candidates
    ]
    scored = [entry for entry in scored if entry.score >= profile.min_score]
    scored = dedupe_by_title(scored)
    scored.sort(key=sort_key)
    reachable = [entry for entry in scored if not entry.over_bar]
    over = [entry for entry in scored if entry.over_bar]
    picked = (reachable + over)[: profile.top_n]
    state.entries = picked
    state.watch = over[:5]
    return picked


def business_map_due(profile: MatchProfile, state: TopState) -> bool:
    """业务地图是否到了该更新提醒的时间。"""
    if not profile.business_map_path:
        return False
    if not state.business_map_reminded_at:
        return True
    try:
        last = datetime.fromisoformat(state.business_map_reminded_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - last
    return elapsed.days >= profile.business_map_interval_days


def business_map_reminder(profile: MatchProfile, days_since: int | None = None) -> str:
    """月度提醒的正文。只写「该更新什么」，不复制业务地图里的任何内容。"""
    path = profile.business_map_path
    waited = f"距上次更新已 {days_since} 天。" if days_since else ""
    return (
        f"业务地图该更新了。{waited}这份文档是 Top 10 匹配口径的来源，"
        f"文档变了、口径也要跟着变。\n"
        f"文档位置：{path}\n"
        f"要过的几件事：三条主线（门户 / PSI / 虚拟组织）有没有变、"
        f"我在链上的那一段有没有挪、靶心方向与城市有没有变。\n"
        f"更新后跑一次 `python -m jobwatch top10 --rebuild`，让新口径重新算名单。"
    )


def days_since(iso_text: str) -> int | None:
    if not iso_text:
        return None
    try:
        last = datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).days
