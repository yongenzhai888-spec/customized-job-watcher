import json

from jobwatch.models import JobRef
from jobwatch.store import JobStore


def ref(job_id: str, title: str = "岗位") -> JobRef:
    return JobRef(job_id=job_id, title=title, url=f"https://example.com/{job_id}")


def test_first_run_reports_everything_as_new(tmp_path):
    diff = JobStore(tmp_path / "s.json").diff([ref("a"), ref("b")])

    assert diff.is_first_run is True
    assert [j.job_id for j in diff.new_jobs] == ["a", "b"]


def test_only_unseen_jobs_are_new_after_commit(tmp_path):
    path = tmp_path / "s.json"
    JobStore(path).commit([ref("a"), ref("b")])

    diff = JobStore(path).diff([ref("a"), ref("b"), ref("c", "新岗位")])

    assert diff.is_first_run is False
    assert [j.job_id for j in diff.new_jobs] == ["c"]
    assert diff.removed_ids == []


def test_removed_jobs_are_reported_and_dropped(tmp_path):
    path = tmp_path / "s.json"
    JobStore(path).commit([ref("a"), ref("b")])

    store = JobStore(path)
    assert store.diff([ref("a")]).removed_ids == ["b"]

    store.commit([ref("a")])
    assert JobStore(path).known_ids == {"a"}


def test_first_seen_is_preserved_across_runs(tmp_path):
    path = tmp_path / "s.json"
    JobStore(path).commit([ref("a")])
    first_seen = json.loads(path.read_text(encoding="utf-8"))["jobs"]["a"]["first_seen"]

    JobStore(path).commit([ref("a")])

    assert json.loads(path.read_text(encoding="utf-8"))["jobs"]["a"]["first_seen"] == first_seen


def test_unchanged_jobs_produce_a_one_line_diff(tmp_path):
    """快照文件每天都会提交回仓库，岗位没变时不该产生成片的 diff。"""
    path = tmp_path / "s.json"
    jobs = [ref(f"job-{i}") for i in range(50)]
    JobStore(path).commit(jobs)
    before = path.read_text(encoding="utf-8").splitlines()

    JobStore(path).commit(jobs)
    after = path.read_text(encoding="utf-8").splitlines()

    changed = [a for a, b in zip(before, after, strict=True) if a != b]
    # 只有顶层的 last_run 允许变；岗位条目本身必须逐字节相同
    assert all("last_run" in line for line in changed)
    assert json.loads(path.read_text(encoding="utf-8"))["jobs"] == json.loads(
        "\n".join(before)
    )["jobs"]


def test_corrupt_state_file_is_treated_as_first_run(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    store = JobStore(path)
    assert store.is_first_run is True
    assert store.diff([ref("a")]).new_jobs
