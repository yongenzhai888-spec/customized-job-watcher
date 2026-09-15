import json

from applepay_watch.apple import JobSummary
from applepay_watch.store import JobStore


def job(job_id: str, title: str = "岗位") -> JobSummary:
    return JobSummary(job_id=job_id, position_id=job_id, title=title, slug="slug")


def test_first_run_reports_everything_as_new(tmp_path):
    store = JobStore(tmp_path / "state.json")
    diff = store.diff([job("a"), job("b")])

    assert diff.is_first_run is True
    assert [j.job_id for j in diff.new_jobs] == ["a", "b"]


def test_only_unseen_jobs_are_new_after_commit(tmp_path):
    path = tmp_path / "state.json"
    JobStore(path).commit([job("a"), job("b")])

    diff = JobStore(path).diff([job("a"), job("b"), job("c", "新岗位")])

    assert diff.is_first_run is False
    assert [j.job_id for j in diff.new_jobs] == ["c"]
    assert diff.removed_ids == []


def test_removed_jobs_are_reported_and_dropped(tmp_path):
    path = tmp_path / "state.json"
    JobStore(path).commit([job("a"), job("b")])

    store = JobStore(path)
    diff = store.diff([job("a")])
    assert diff.removed_ids == ["b"]

    store.commit([job("a")])
    assert JobStore(path).known_ids == {"a"}


def test_first_seen_is_preserved_across_runs(tmp_path):
    path = tmp_path / "state.json"
    JobStore(path).commit([job("a")])
    first_seen = json.loads(path.read_text(encoding="utf-8"))["jobs"]["a"]["first_seen"]

    JobStore(path).commit([job("a")])
    data = json.loads(path.read_text(encoding="utf-8"))["jobs"]["a"]

    assert data["first_seen"] == first_seen
    assert data["last_seen"] >= first_seen


def test_corrupt_state_file_is_treated_as_first_run(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    store = JobStore(path)
    assert store.is_first_run is True
    assert store.diff([job("a")]).new_jobs
