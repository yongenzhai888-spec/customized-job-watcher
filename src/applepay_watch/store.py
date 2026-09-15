"""岗位快照存储：用它来判断"这次抓到的岗位里哪些是新增的"。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .apple import JobSummary

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(slots=True)
class DiffResult:
    new_jobs: list[JobSummary]
    removed_ids: list[str]
    is_first_run: bool

    @property
    def has_changes(self) -> bool:
        return bool(self.new_jobs or self.removed_ids)


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"version": SCHEMA_VERSION, "last_run": None, "jobs": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("状态文件 %s 无法读取（%s），按首次运行处理", self.path, exc)
            return {"version": SCHEMA_VERSION, "last_run": None, "jobs": {}}
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

    def diff(self, jobs: list[JobSummary]) -> DiffResult:
        known = self.known_ids
        current = {job.job_id for job in jobs}
        return DiffResult(
            new_jobs=[job for job in jobs if job.job_id not in known],
            removed_ids=sorted(known - current),
            is_first_run=self.is_first_run,
        )

    def commit(self, jobs: list[JobSummary], *, drop_removed: bool = True) -> None:
        """把本次抓到的岗位写回状态文件。"""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        previous = self._data["jobs"]
        snapshot: dict[str, dict] = {}
        for job in jobs:
            old = previous.get(job.job_id, {})
            snapshot[job.job_id] = {
                "title": job.title,
                "slug": job.slug,
                "locations": job.locations,
                "team": job.team,
                "posted_at": job.posted_at,
                "first_seen": old.get("first_seen", now),
                "last_seen": now,
            }
        if not drop_removed:
            for job_id, meta in previous.items():
                snapshot.setdefault(job_id, meta)
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
