"""Remote dense executor with the fake provider: always-stop, timeout, budget, config."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qarau_pipeline.dense_fake import FakeClock, FakeGpuProvider
from qarau_pipeline.dense_remote import (
    DEFAULT_CONFIG,
    BudgetExceededError,
    DenseConfigError,
    DenseExecutorError,
    DenseRemoteConfig,
    DenseTimeoutError,
    GpuLedger,
    RemoteDenseExecutor,
    WorkerHandle,
    dense_commands,
    load_dense_remote_config,
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    frames, sparse = tmp_path / "frames", tmp_path / "sparse/0"
    frames.mkdir()
    sparse.mkdir(parents=True)
    (frames / "000000.jpg").write_bytes(b"jpg")
    (sparse / "cameras.bin").write_bytes(b"bin")
    return {"frames": frames, "sparse": sparse, "out": tmp_path / "out", "root": tmp_path}


def make(env: dict[str, Path], **fake: object) -> tuple[RemoteDenseExecutor, FakeGpuProvider]:
    clock = FakeClock()
    provider = FakeGpuProvider(env["root"] / "remote", clock, **fake)  # type: ignore[arg-type]
    cfg = DenseRemoteConfig(
        enabled=True, timeout_s=3600, daily_gpu_hours_budget=4.0, est_hours_per_job=1.0,
        ledger_path=env["root"] / "ledger.json", image="qarau/colmap-cuda:test", gpu_type="t",
    )  # fmt: skip
    return RemoteDenseExecutor(provider, cfg, GpuLedger(cfg.ledger_path), clock), provider


def go(ex: RemoteDenseExecutor, env: dict[str, Path]) -> Path:
    return ex.run(env["frames"], env["sparse"], env["out"])


def stops(p: FakeGpuProvider) -> int:
    return sum(c[0] == "stop" for c in p.calls)


def test_success_downloads_results_and_stops_once(env: dict[str, Path]) -> None:
    ex, p = make(env)
    ply = go(ex, env)
    assert ply == env["out"] / "fused.ply" and ply.is_file()
    assert (env["out"] / "fused.ply.vis").is_file()
    assert stops(p) == 1 and not p.running
    assert [c[0] for c in p.calls] == ["start_worker", "upload", "upload", "run", "run", "run",
                                       "download", "download", "stop"]  # fmt: skip
    # uploaded files really reached the worker
    assert (env["root"] / "remote/fake-1/work/images/000000.jpg").is_file()
    # actual hours (8 fake 1 s calls; stop takes none) replace the 1 h estimate
    assert GpuLedger(env["root"] / "ledger.json").used(FakeClock()().date()) == pytest.approx(
        8 / 3600, abs=1e-6
    )


def test_start_failure_needs_no_stop_and_leaks_nothing(env: dict[str, Path]) -> None:
    ex, p = make(env, fail={"start_worker": RuntimeError("no capacity")})
    with pytest.raises(RuntimeError, match="no capacity"):
        go(ex, env)
    assert stops(p) == 0 and not p.running


@pytest.mark.parametrize(
    ("fake", "error"),
    [
        ({"fail": {"upload": OSError("disk full")}}, OSError),
        ({"exit_code": 1}, DenseExecutorError),
        ({"fail": {"run": RuntimeError("ssh dropped")}}, RuntimeError),
        ({"fail": {"download": OSError("network")}}, OSError),
        ({"hang": {"run": 7200.0}}, DenseTimeoutError),  # provider raises TimeoutError
        ({"fail": {"run": KeyboardInterrupt()}}, KeyboardInterrupt),
    ],
    ids=[
        "upload_error",
        "run_nonzero_exit",
        "run_error",
        "download_error",
        "timeout",
        "keyboard_interrupt",
    ],  # fmt: skip
)
def test_failure_stops_exactly_once_and_propagates(
    env: dict[str, Path], fake: dict[str, object], error: type[BaseException]
) -> None:
    ex, p = make(env, **fake)
    with pytest.raises(error):
        go(ex, env)
    assert stops(p) == 1 and not p.running


def test_nonzero_exit_names_command_and_worker(env: dict[str, Path]) -> None:
    ex, _ = make(env, exit_code=137)
    with pytest.raises(DenseExecutorError, match="image_undistorter exited with 137 on worker"):
        go(ex, env)


def test_call_returning_past_deadline_is_a_timeout(env: dict[str, Path]) -> None:
    # A provider that ignores its timeout_s: the call returns, but after the deadline.
    ex, p = make(env, hang={"upload": 4000.0}, respect_timeout=False)
    with pytest.raises(DenseTimeoutError, match="timeout_s 3600"):
        go(ex, env)
    assert stops(p) == 1 and not p.running
    assert not any(c[0] == "run" for c in p.calls)


def test_slow_start_past_deadline_still_stops_worker(env: dict[str, Path]) -> None:
    ex, p = make(env, hang={"start_worker": 5000.0}, respect_timeout=False)
    with pytest.raises(DenseTimeoutError):
        go(ex, env)
    assert stops(p) == 1 and not p.running


def test_missing_result_is_an_error(env: dict[str, Path]) -> None:
    ex, p = make(env)
    p.run = lambda handle, argv, timeout_s: 0  # type: ignore[method-assign]  # writes nothing
    with pytest.raises(DenseExecutorError, match="fused.ply missing from worker fake-1"):
        go(ex, env)
    assert stops(p) == 1


def test_failing_stop_is_retried_then_names_worker(env: dict[str, Path]) -> None:
    ex, p = make(env, stop_failures=99)
    with pytest.raises(DenseExecutorError, match="could not stop worker fake-1 .*after 3"):
        go(ex, env)
    assert stops(p) == 3 and p.running == {"fake-1"}


def test_stop_recovers_on_retry(env: dict[str, Path]) -> None:
    ex, p = make(env, stop_failures=1)
    go(ex, env)
    assert stops(p) == 2 and not p.running


def test_failing_stop_after_job_error_still_names_worker(env: dict[str, Path]) -> None:
    ex, _ = make(env, stop_failures=99, fail={"upload": OSError("disk full")})
    with pytest.raises(DenseExecutorError, match="fake-1") as info:
        go(ex, env)
    assert isinstance(info.value.__context__, OSError)  # the job error is kept as context


def test_budget_exceeded_never_starts_worker(env: dict[str, Path]) -> None:
    ex, p = make(env)
    ex.ledger.add(ex.clock().date(), 3.5)  # 3.5 + 1.0 > 4.0
    with pytest.raises(BudgetExceededError, match="no worker started"):
        go(ex, env)
    assert p.calls == []
    assert ex.ledger.used(ex.clock().date()) == 3.5


def test_budget_exactly_equal_is_allowed(env: dict[str, Path]) -> None:
    ex, p = make(env)
    ex.ledger.add(ex.clock().date(), 3.0)  # 3.0 + 1.0 == 4.0
    go(ex, env)
    assert stops(p) == 1


def test_zero_budget_refuses(env: dict[str, Path]) -> None:
    ex, p = make(env)
    ex.config = replace(ex.config, daily_gpu_hours_budget=0.0)
    with pytest.raises(BudgetExceededError):
        go(ex, env)
    assert p.calls == []


def test_ledger_resets_on_new_utc_day(env: dict[str, Path]) -> None:
    ex, p = make(env)
    clock = p.clock
    clock.now = datetime(2026, 10, 17, 23, 0, tzinfo=UTC)
    ex.ledger.add(clock().date(), 4.0)
    with pytest.raises(BudgetExceededError):
        go(ex, env)
    clock.now = datetime(2026, 10, 18, 0, 0, 1, tzinfo=UTC)
    go(ex, env)
    data = json.loads((env["root"] / "ledger.json").read_text())
    assert data["2026-10-17"] == 4.0  # yesterday kept, not reset in place
    assert 0 < data["2026-10-18"] < 1.0


def test_estimate_is_booked_while_the_job_runs(env: dict[str, Path]) -> None:
    # A process killed mid-job never settles: the estimate is already on the ledger by then.
    ex, p = make(env)
    day = p.clock().date()
    seen: list[float] = []
    real_run = p.run

    def run(handle: WorkerHandle, argv: list[str], timeout_s: float) -> int:
        seen.append(ex.ledger.used(day))
        return real_run(handle, argv, timeout_s)

    p.run = run  # type: ignore[method-assign]
    go(ex, env)
    assert seen == [1.0, 1.0, 1.0]
    assert ex.ledger.used(day) < 1.0


def test_disabled_config_refuses(env: dict[str, Path]) -> None:
    ex, p = make(env)
    ex.config = replace(ex.config, enabled=False)
    with pytest.raises(DenseExecutorError, match="enabled is false"):
        go(ex, env)
    assert p.calls == []


def test_dense_commands_snapshot() -> None:
    assert dense_commands("/w/images", "/w/sparse", "/w/dense") == [
        ["colmap", "image_undistorter", "--image_path", "/w/images", "--input_path", "/w/sparse",
         "--output_path", "/w/dense", "--output_type", "COLMAP"],
        ["colmap", "patch_match_stereo", "--workspace_path", "/w/dense",
         "--workspace_format", "COLMAP", "--PatchMatchStereo.geom_consistency", "true"],
        ["colmap", "stereo_fusion", "--workspace_path", "/w/dense", "--workspace_format",
         "COLMAP", "--input_type", "geometric", "--output_path", "/w/dense/fused.ply"],
    ]  # fmt: skip


def test_repo_config_loads_disabled() -> None:
    cfg = load_dense_remote_config(DEFAULT_CONFIG)
    assert cfg.enabled is False
    assert cfg.timeout_s > 0 and cfg.daily_gpu_hours_budget >= 0
    assert cfg.ledger_path.resolve().parent.parent.name == "data"


VALID = """dense_remote:
  enabled: false
  timeout_s: 3600
  est_hours_per_job: 1.0
  daily_gpu_hours_budget: 4
  gpu_type: t
  image: img
  ledger_path: ledger.json
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_valid_config_loads(tmp_path: Path) -> None:
    cfg = load_dense_remote_config(write(tmp_path, VALID))
    assert cfg.daily_gpu_hours_budget == 4.0
    assert cfg.ledger_path == tmp_path / "ledger.json"


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("  timeout_s: 3600\n", "", "timeout_s"),
        ("  timeout_s: 3600\n", "  timeout_s: -5\n", "timeout_s"),
        ("  timeout_s: 3600\n", "  timeout_s: 0\n", "timeout_s"),
        ("  daily_gpu_hours_budget: 4\n", "", "daily_gpu_hours_budget"),
        (
            "  daily_gpu_hours_budget: 4\n",
            "  daily_gpu_hours_budget: -1\n",
            "daily_gpu_hours_budget",
        ),  # fmt: skip
        ("  est_hours_per_job: 1.0\n", "  est_hours_per_job: -1\n", "est_hours_per_job"),
        ("  est_hours_per_job: 1.0\n", "  est_hours_per_job: 0.5\n", "timeout_s"),
        ("  enabled: false\n", "  enabled: maybe\n", "enabled"),
        ("  image: img\n", "", "image"),
    ],
)
def test_bad_config_names_key(tmp_path: Path, old: str, new: str, key: str) -> None:
    with pytest.raises(DenseConfigError, match=f"dense_remote.{key}"):
        load_dense_remote_config(write(tmp_path, VALID.replace(old, new)))


def test_zero_budget_is_valid(tmp_path: Path) -> None:
    text = VALID.replace("daily_gpu_hours_budget: 4", "daily_gpu_hours_budget: 0")
    assert load_dense_remote_config(write(tmp_path, text)).daily_gpu_hours_budget == 0.0


def test_missing_section(tmp_path: Path) -> None:
    with pytest.raises(DenseConfigError, match="section dense_remote"):
        load_dense_remote_config(write(tmp_path, "other: 1\n"))


def test_default_pipeline_does_not_import_executor() -> None:
    import subprocess
    import sys

    code = (
        "import sys, qarau_pipeline.cli, qarau_pipeline.benchmark, qarau_pipeline.reconstruction; "
        "print('qarau_pipeline.dense_remote' in sys.modules)"
    )
    cwd = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
