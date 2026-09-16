"""岗位快照存储：用它判断"这次抓到的岗位里哪些是新增的"。

每个来源一个文件（``state/<source_id>.json``），这样每天的自动提交
diff 清晰，某个来源出问题也不会牵连其它来源的历史。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import JobRef

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2


@dataclass(slots=True)
class DiffResult:
    new_jobs: list[JobRef]
    removed_ids: list[str]
    is_first_run: bool

    @property
    def has_changes(self) -> bool:
        return bool(self.new_jobs or self.removed_ids)


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _empty(self) -> dict:
        return {"version": SCHEMA_VERSION, "last_run": None, "jobs": {}}

    def _load(self) -> dict:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("状态文件 %s 无法读取（%s），按首次运行处理", self.path, exc)
            return self._empty()
        data.setdefault("jobs", {})
        data.setdefault("last_run", None)
        data["version"] = SCHEMA_VERSION
        return data

    @property
    def is_first_run(self) -> bool:
        return not self._data["jobs"] and not self._data["last_run"]

    @property
    def known_ids(self) -> set[str]:
        return set(self._data["jobs"])

    @property
    def last_run(self) -> str | None:
        return self._data["last_run"]

    @property
    def snapshot(self) -> dict[str, dict]:
        return dict(self._data["jobs"])

    def diff(self, jobs: list[JobRef]) -> DiffResult:
        known = self.known_ids
        current = {job.job_id for job in jobs}
        return DiffResult(
            new_jobs=[job for job in jobs if job.job_id not in known],
            removed_ids=sorted(known - current),
            is_first_run=self.is_first_run,
        )

    def commit(self, jobs: list[JobRef]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        previous = self._data["jobs"]
        # 刻意不记录每个岗位的 last_seen：字节那种来源有近两千个岗位，
        # 逐条写时间戳会让每天的自动提交产生近两千行无意义的 diff。
        # 顶层的 last_run 已经够用，岗位没变化时快照文件只差一行。
        snapshot = {
            job.job_id: {
                "title": job.title,
                "first_seen": previous.get(job.job_id, {}).get("first_seen", now),
            }
            for job in jobs
        }
        self._data["jobs"] = snapshot
        self._data["last_run"] = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
        log.info("状态已写入 %s（记录 %s 个岗位）", self.path, len(snapshot))
