"""`dense-remote --provider modal --allow-paid-gpu` (ADR 0019): the paid path behind an explicit
flag. Fake ModalClient and a fake `modal` module only; no token, no network, nothing paid."""

from __future__ import annotations

import json
import shutil
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from qarau_pipeline import dense_remote
from qarau_pipeline.cli import main
from qarau_pipeline.dense_modal import TOKEN_VARS, ModalGpuProvider, ModalImageSpec
from qarau_pipeline.dense_remote import DEFAULT_CONFIG, DenseRemoteConfig, load_dense_remote_config


class FakeModalClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start(self, image: ModalImageSpec, gpu: str, timeout_s: float, tags: dict[str, str]) -> str:
        self.calls.append("start")
        return "sb-fake-1"

    def upload(self, worker_id: str, local_dir: Path, remote_dir: str) -> None:
        self.calls.append("upload")

    def run(self, worker_id: str, argv: list[str], timeout_s: float) -> int:
        self.calls.append(argv[1])
        return 0

    def download(self, worker_id: str, remote_path: str, local_dir: Path) -> None:
        self.calls.append("download")
        (local_dir / remote_path.rsplit("/", 1)[-1]).write_bytes(b"result")

    def stop(self, worker_id: str) -> None:
        self.calls.append("stop")

    def stop_tagged(self, tags: dict[str, str]) -> None:
        self.calls.append("stop_tagged")


@pytest.fixture(autouse=True)
def no_modal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in TOKEN_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    """A copy of the real pipeline/config.yaml; its ledger_path (../data/...) lands in tmp_path."""
    cfg = tmp_path / "pipeline" / "config.yaml"
    cfg.parent.mkdir()
    shutil.copy(DEFAULT_CONFIG, cfg)
    frames, sparse = tmp_path / "frames", tmp_path / "sparse/0"
    frames.mkdir()
    sparse.mkdir(parents=True)
    (frames / "000000.jpg").write_bytes(b"jpg")
    (sparse / "cameras.bin").write_bytes(b"bin")
    return {"cfg": cfg, "frames": frames, "sparse": sparse, "out": tmp_path / "out"}


def cli(env: dict[str, Path], *extra: str) -> None:
    main(["dense-remote", "--frames", str(env["frames"]), "--sparse", str(env["sparse"]),
          "--out", str(env["out"]), "--config", str(env["cfg"]), *extra])  # fmt: skip


def raw(env: dict[str, Path]) -> dict[str, Any]:
    sec: dict[str, Any] = yaml.safe_load(env["cfg"].read_text())["dense_remote"]
    return sec


def fake_make_provider(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeModalClient, list[Any]]:
    client, seen = FakeModalClient(), []

    def make(cfg: DenseRemoteConfig) -> ModalGpuProvider:
        seen.append(cfg)
        return ModalGpuProvider(client)

    monkeypatch.setattr(dense_remote, "make_provider", make)
    return client, seen


def test_default_provider_is_fake(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert raw(env)["provider"] == "modal" and raw(env)["enabled"] is False
    monkeypatch.setattr(dense_remote, "make_provider", Mock(side_effect=AssertionError("paid")))
    cli(env)
    assert "start_worker qarau/colmap-cuda" in capsys.readouterr().out
    assert (env["out"] / "fused.ply").is_file()
    assert (env["out"] / "gpu_ledger.fake.json").is_file()
    assert not load_dense_remote_config(env["cfg"]).ledger_path.exists()


def test_fake_run_prints_patch_match_knobs(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    text = env["cfg"].read_text()
    env["cfg"].write_text(text + "  patch_match:\n    max_image_size: 960\n"
                          "    num_iterations: 3\n    num_sources: 8\n")  # fmt: skip
    monkeypatch.setattr(dense_remote, "make_provider", Mock(side_effect=AssertionError("paid")))
    cli(env)
    out = capsys.readouterr().out
    assert "run colmap image_undistorter " in out and " --max_image_size 960\n" in out
    assert "--PatchMatchStereo.max_image_size 960" in out
    assert "--PatchMatchStereo.num_iterations 3" in out
    assert "run sh -c sed -i -E 's/^__auto__, [0-9]+$/__auto__, 8/'" in out
    assert (env["out"] / "fused.ply").is_file()


def test_modal_without_flag_refuses_before_make_provider(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make = Mock()
    monkeypatch.setattr(dense_remote, "make_provider", make)
    with pytest.raises(SystemExit) as exc:
        cli(env, "--provider", "modal")
    assert exc.value.code != 0
    assert "--allow-paid-gpu" in capsys.readouterr().err
    make.assert_not_called()
    assert not env["out"].exists()


def test_modal_with_flag_uses_make_provider_and_config(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = env["cfg"].read_bytes()
    client, seen = fake_make_provider(monkeypatch)
    executors: list[dense_remote.RemoteDenseExecutor] = []

    class Spy(dense_remote.RemoteDenseExecutor):
        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            executors.append(self)

    monkeypatch.setattr(dense_remote, "RemoteDenseExecutor", Spy)
    cli(env, "--provider", "modal", "--allow-paid-gpu")

    sec, ledger = raw(env), env["cfg"].parent / raw(env)["ledger_path"]
    assert [c.provider for c in seen] == ["modal"] and seen[0].enabled is True
    (ex,) = executors
    assert ex.ledger.path == ledger
    assert ex.config.timeout_s == sec["timeout_s"]
    assert ex.config.daily_gpu_hours_budget == sec["daily_gpu_hours_budget"]
    assert ex.config.est_hours_per_job == sec["est_hours_per_job"]
    assert client.calls[0] == "start" and client.calls[-1] == "stop"
    assert ledger.is_file() and (env["out"] / "fused.ply").is_file()
    assert env["cfg"].read_bytes() == before and sec["enabled"] is False


def test_modal_missing_token_error_names_variables(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = types.ModuleType("modal")

    def untouched(name: str) -> Any:
        raise AssertionError(f"modal.{name} used without a token")

    fake.__getattr__ = untouched  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "modal", fake)

    with pytest.raises(SystemExit) as exc:
        cli(env, "--provider", "modal", "--allow-paid-gpu")
    err = capsys.readouterr().err
    assert exc.value.code == 1 and "MODAL_TOKEN_ID" in err and "MODAL_TOKEN_SECRET" in err

    sentinel = "sentinel-token-id-4711"
    monkeypatch.setenv("MODAL_TOKEN_ID", sentinel)
    with pytest.raises(SystemExit):
        cli(env, "--provider", "modal", "--allow-paid-gpu")
    out = capsys.readouterr()
    assert "MODAL_TOKEN_SECRET" in out.err and sentinel not in out.err + out.out
    assert not load_dense_remote_config(env["cfg"]).ledger_path.exists()


def test_modal_spent_budget_refuses_before_start(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = load_dense_remote_config(env["cfg"]).ledger_path
    ledger.parent.mkdir(parents=True)
    today = datetime.now(UTC).date().isoformat()
    ledger.write_text(json.dumps({today: raw(env)["daily_gpu_hours_budget"]}))
    client, _ = fake_make_provider(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        cli(env, "--provider", "modal", "--allow-paid-gpu")
    assert exc.value.code == 1 and "GPU budget" in capsys.readouterr().err
    assert client.calls == []
