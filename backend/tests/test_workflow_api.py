"""Queue a run, watch its status, rerun one step, 403 for the viewer. RQ on fakeredis with a
SimpleWorker in a background thread (jobs run in-process, so the fake step runner below replaces
the `qarau_pipeline step` subprocesses)."""

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty
from sqlalchemy.orm import Session

from app import health, workflow
from app.db import engine_for
from app.models import Run
from app.settings import get_settings
from app.workflow import QUEUE

NAMES = ["probe", "frames", "sparse", "export"]
ENG, VIEW = bearer("engineer"), bearer("viewer")


class FakeRunner:
    """In-process stand-in for SubprocessRunner: writes the same step.json the pipeline would,
    records the (name, force) calls, can fail a step or block on an Event."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.prefixes: set[str] = set()
        self.fail: dict[str, str] = {}
        self.gate: dict[str, threading.Event] = {}
        self.entered: dict[str, threading.Event] = {}

    def names(self) -> list[str]:
        return list(NAMES)

    def run(self, run_dir: Path, name: str, force: bool, prefix: str) -> dict[str, Any]:
        self.calls.append((name, force))
        self.prefixes.add(prefix)
        if name in self.entered:
            self.entered[name].set()
        if name in self.gate:
            self.gate[name].wait(timeout=30)
        started = "2026-10-23T10:00:00+00:00"
        if name in self.fail:
            return {"name": name, "status": "failed", "started_at": started,
                    "finished_at": "2026-10-23T10:00:01+00:00", "duration_s": 1.0,
                    "error": self.fail[name]}  # fmt: skip
        return {"name": name, "status": "done", "started_at": started,
                "finished_at": "2026-10-23T10:00:02+00:00", "duration_s": 2.0, "error": None,
                "outputs": [f"{name}/"], "metrics": {}}  # fmt: skip


class ThreadWorker(SimpleWorker):
    """SimpleWorker that can run in a non-main thread (no signal handlers, timer-based timeout)."""

    death_penalty_class = TimerDeathPenalty

    def _install_signal_handlers(self) -> None:
        pass


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    fake = FakeRunner()
    monkeypatch.setattr(workflow, "make_runner", lambda: fake)
    return fake


@pytest.fixture
def work(fake_redis: fakeredis.FakeStrictRedis) -> Iterator[Any]:
    """`work()` drains the queue in a background thread and returns it (join it when done)."""
    threads: list[threading.Thread] = []

    def go() -> threading.Thread:
        q = Queue(QUEUE, connection=fake_redis)
        w = ThreadWorker([q], connection=fake_redis)
        t = threading.Thread(target=lambda: w.work(burst=True), daemon=True)
        t.start()
        threads.append(t)
        return t

    yield go
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()


class StatusLog:
    """Polls GET /api/inspections/1 in the API thread and keeps the distinct statuses seen."""

    def __init__(self, c: TestClient) -> None:
        self.c = c
        self.seen: list[str] = []

    def poll(self) -> dict[str, Any]:
        body: dict[str, Any] = self.c.get("/api/inspections/1", headers=VIEW).json()
        if not self.seen or self.seen[-1] != body["status"]:
            self.seen.append(body["status"])
        return body

    def until(self, *final: str, timeout: float = 30) -> dict[str, Any]:
        t0 = time.monotonic()
        while True:
            body = self.poll()
            if body["status"] in final or time.monotonic() - t0 > timeout:
                return body
            time.sleep(0.01)


def upload(c: TestClient) -> str:
    """The B1 upload: a 3-byte video, so the run has status 'uploaded' and a video file."""
    r = c.post("/api/inspections/1/video", json={"name": "a.mp4", "size": 3}, headers=ENG)
    upload_id, run_id = r.json()["upload_id"], r.json()["run_id"]
    c.put(f"/api/inspections/1/video/chunks?upload_id={upload_id}&offset=0", content=b"abc",
          headers={**ENG, "Content-Type": "application/octet-stream"})  # fmt: skip
    sha = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert c.post("/api/inspections/1/video/complete", json={"upload_id": upload_id, "sha256": sha},
                  headers=ENG).status_code == 200  # fmt: skip
    return str(run_id)


def step_view(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(s["name"], s["status"]) for s in body["steps"]]


def hold(runner: FakeRunner, step: str) -> None:
    """The fake blocks in `step` until released, so the API is observed while it is running."""
    runner.gate[step] = threading.Event()
    runner.entered[step] = threading.Event()


def observe_running(log: StatusLog, runner: FakeRunner, step: str) -> dict[str, Any]:
    assert runner.entered[step].wait(timeout=30)
    body = log.poll()
    runner.gate[step].set()
    return body


def test_run_goes_queued_running_done(client: TestClient, runner: FakeRunner, work: Any) -> None:
    run_id = upload(client)
    before = client.get("/api/inspections/1", headers=VIEW).json()
    assert (before["status"], before["run_id"], before["steps"]) == ("created", run_id, [])

    r = client.post("/api/inspections/1/runs", json={}, headers=ENG)
    assert r.status_code == 202, r.text
    assert r.json()["run_id"] == run_id and r.json()["status"] == "queued"
    log = StatusLog(client)
    log.poll()
    hold(runner, "export")
    work()
    mid = observe_running(log, runner, "export")
    assert (mid["status"], mid["current_step"], mid["progress"]) == ("running", "export", 75)
    body = log.until("done", "failed")
    assert log.seen == ["queued", "running", "done"]
    assert runner.calls == [("probe", False), ("frames", False), ("sparse", False),
                            ("export", False)]  # fmt: skip
    assert runner.prefixes == {"1/1/1"}  # org 1 / site 1 / inspection 1: the storage prefix
    assert (body["progress"], body["current_step"], body["error"]) == (100, None, None)
    assert step_view(body) == [(n, "done") for n in NAMES]
    assert all(set(s) == {"name", "status", "started_at", "finished_at", "duration_s", "error"}
               for s in body["steps"])  # fmt: skip
    assert body["steps"][2]["started_at"] == "2026-10-23T10:00:00Z"
    assert body["steps"][2]["finished_at"] == "2026-10-23T10:00:02Z"
    assert body["steps"][2]["duration_s"] == 2.0 and body["steps"][2]["error"] is None
    runs = client.get("/api/inspections/1/runs", headers=VIEW).json()
    assert (runs[0]["status"], runs[0]["progress"], runs[0]["current_step"]) == ("done", 100, None)
    assert runs[0]["rq_job_id"] == r.json()["job_id"]
    # The audit log has the queue action.
    log_rows = client.get("/api/audit-log", headers=bearer("admin")).json()
    assert (log_rows[0]["object_type"], log_rows[0]["object_id"], log_rows[0]["action"]) == (
        "run",
        run_id,
        "queue",
    )


def test_failed_step_ends_the_run_with_its_error(
    client: TestClient, runner: FakeRunner, work: Any
) -> None:
    upload(client)
    runner.fail["sparse"] = "StageError: COLMAP could not build a model"
    assert client.post("/api/inspections/1/runs", json={}, headers=ENG).status_code == 202
    log = StatusLog(client)
    log.poll()
    hold(runner, "sparse")
    work()
    assert observe_running(log, runner, "sparse")["current_step"] == "sparse"
    body = log.until("done", "failed")
    assert log.seen == ["queued", "running", "failed"]
    assert body["error"] == "sparse: StageError: COLMAP could not build a model"
    assert body["current_step"] == "sparse" and body["progress"] == 50
    assert step_view(body) == [("probe", "done"), ("frames", "done"), ("sparse", "failed"),
                               ("export", "pending")]  # fmt: skip
    assert body["steps"][2]["error"] == "StageError: COLMAP could not build a model"
    assert runner.calls == [("probe", False), ("frames", False), ("sparse", False)]


def test_rerun_executes_exactly_that_step_and_the_following(
    client: TestClient, runner: FakeRunner, work: Any
) -> None:
    run_id = upload(client)
    runner.fail["sparse"] = "boom"
    client.post("/api/inspections/1/runs", json={}, headers=ENG)
    work()
    StatusLog(client).until("done", "failed")
    runner.calls.clear()
    runner.fail.clear()

    r = client.post(f"/api/inspections/1/runs/{run_id}/steps/sparse/rerun", headers=ENG)
    assert r.status_code == 202, r.text
    assert r.json() == {"run_id": run_id, "status": "queued", "job_id": r.json()["job_id"],
                        "from_step": "sparse"}  # fmt: skip
    # While it is queued: a second rerun is refused, an unknown step is still a 404.
    r = client.post(f"/api/inspections/1/runs/{run_id}/steps/export/rerun", headers=ENG)
    assert r.status_code == 409 and r.json()["detail"] == "run is queued; wait for it to finish"
    r = client.post(f"/api/inspections/1/runs/{run_id}/steps/nope/rerun", headers=ENG)
    assert r.status_code == 404
    work()
    body = StatusLog(client).until("done", "failed")
    assert body["status"] == "done"
    assert runner.calls == [("sparse", True), ("export", True)]
    assert step_view(body) == [(n, "done") for n in NAMES]
    assert body["steps"][2]["error"] is None and body["progress"] == 100
    r = client.post(f"/api/inspections/1/runs/{run_id}/steps/nope/rerun", headers=ENG)
    assert r.status_code == 404
    r = client.post("/api/inspections/1/runs/zzz/steps/sparse/rerun", headers=ENG)
    assert r.status_code == 404


def test_api_answers_while_a_step_is_running(
    client: TestClient, runner: FakeRunner, work: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_db", lambda: "ok")
    monkeypatch.setattr(health, "check_storage", lambda: "ok")
    upload(client)
    runner.gate["frames"] = threading.Event()
    runner.entered["frames"] = threading.Event()
    client.post("/api/inspections/1/runs", json={}, headers=ENG)
    work()
    assert runner.entered["frames"].wait(timeout=30)

    body = client.get("/api/inspections/1", headers=VIEW).json()
    assert (body["status"], body["current_step"], body["progress"]) == ("running", "frames", 25)
    assert step_view(body) == [("probe", "done"), ("frames", "running"), ("sparse", "pending"),
                               ("export", "pending")]  # fmt: skip
    t0 = time.perf_counter()
    assert client.get("/health").status_code == 200
    assert time.perf_counter() - t0 < 1.0
    # Queueing again while running is refused without force.
    r = client.post("/api/inspections/1/runs", json={}, headers=ENG)
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    runner.gate["frames"].set()
    assert StatusLog(client).until("done", "failed")["status"] == "done"


def test_queue_needs_a_finished_upload(client: TestClient, runner: FakeRunner) -> None:
    r = client.post("/api/inspections/1/runs", json={}, headers=ENG)
    assert r.status_code == 409 and r.json()["detail"] == "no run to process: upload a video first"
    r = client.post("/api/inspections/1/video", json={"name": "a.mp4", "size": 3}, headers=ENG)
    run_id = r.json()["run_id"]
    r = client.post("/api/inspections/1/runs", json={"run_id": run_id}, headers=ENG)
    assert r.status_code == 409 and r.json()["detail"] == "the video upload is not complete"
    assert (
        client.post("/api/inspections/1/runs", json={"run_id": "nope"}, headers=ENG).status_code
        == 404
    )
    assert client.post("/api/inspections/9/runs", json={}, headers=ENG).status_code == 404
    assert runner.calls == []


def test_force_requeues_a_run_stuck_in_running(
    client: TestClient, runner: FakeRunner, work: Any, db_url: str
) -> None:
    """A worker killed mid-step leaves status 'running'; force queues it again and the pipeline
    (here: the fake) decides what is still to do."""
    run_id = upload(client)
    with Session(engine_for(db_url)) as db:
        run = db.get(Run, run_id)
        assert run is not None
        run.status, run.current_step = "running", "sparse"
        db.commit()
    assert client.post("/api/inspections/1/runs", json={}, headers=ENG).status_code == 409
    r = client.post("/api/inspections/1/runs", json={"force": True}, headers=ENG)
    assert r.status_code == 202
    work()
    assert StatusLog(client).until("done", "failed")["status"] == "done"
    assert [n for n, _ in runner.calls] == NAMES


def test_viewer_cannot_queue_or_rerun(client: TestClient, runner: FakeRunner) -> None:
    run_id = upload(client)
    assert client.post("/api/inspections/1/runs", json={}, headers=VIEW).status_code == 403
    url = f"/api/inspections/1/runs/{run_id}/steps/sparse/rerun"
    assert client.post(url, headers=VIEW).status_code == 403
    assert client.post("/api/inspections/1/runs", json={}).status_code == 401
    assert runner.calls == []


def test_subprocess_runner_reads_the_marker_or_reports_the_exit(tmp_path: Path) -> None:
    """The real runner against a tiny fake `python -m qarau_pipeline` on disk."""
    pipeline = tmp_path / "pipeline"
    (pipeline / "qarau_pipeline").mkdir(parents=True)
    (pipeline / ".venv/bin").mkdir(parents=True)
    import sys

    (pipeline / ".venv/bin/python").symlink_to(sys.executable)
    (pipeline / "qarau_pipeline/__init__.py").write_text("")
    (pipeline / "qarau_pipeline/__main__.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "a = sys.argv[1:]\n"
        "if '--list':\n"
        "    pass\n"
        "if a == ['step', '--list']:\n"
        "    print(json.dumps(['probe', 'frames']))\n"
        "elif a[1] == 'probe':\n"
        "    run = Path(a[a.index('--run') + 1]) / 'probe'\n"
        "    run.mkdir(exist_ok=True)\n"
        "    (run / 'step.json').write_text(json.dumps({'name': 'probe', 'status': 'done',\n"
        "        'started_at': '2026-10-23T10:00:00+00:00', 'finished_at': "
        "'2026-10-23T10:00:01+00:00', 'duration_s': 1.0, 'error': None, 'outputs': [],\n"
        "        'metrics': {}, 'force': '--force' in a}))\n"
        "    print('step probe: ok')\n"
        "else:\n"
        "    print('ERROR: boom', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    r = workflow.SubprocessRunner(pipeline)
    assert r.names() == ["probe", "frames"]
    doc = r.run(run_dir, "probe", force=True, prefix="7/8/9")
    assert (doc["status"], doc["duration_s"], doc["force"]) == ("done", 1.0, True)
    doc = r.run(run_dir, "frames", force=False, prefix="7/8/9")
    assert doc == {"status": "failed", "error": "step process exit 1: ERROR: boom"}
    log = (run_dir / "log.txt").read_text()
    assert "step probe --run" in log and "--force" in log and "ERROR: boom" in log
    assert "--prefix 7/8/9" in log
    assert get_settings().qarau_pipeline_dir == "../pipeline"
    assert get_settings().valkey_url == "redis://localhost:6379/0"
