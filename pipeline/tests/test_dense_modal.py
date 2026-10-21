"""Modal provider behind the unchanged RemoteDenseExecutor: fake Modal client and a fake
`modal` module only. No network, no token, nothing paid (ADR 0019)."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import traceback
import types
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qarau_pipeline import dense_modal
from qarau_pipeline.dense_modal import (
    ModalError,
    ModalGpuProvider,
    ModalImageSpec,
    Secret,
)
from qarau_pipeline.dense_remote import (
    DEFAULT_CONFIG,
    REMOTE_DENSE,
    RESULTS,
    DenseConfigError,
    DenseExecutorError,
    DenseRemoteConfig,
    DenseTimeoutError,
    GpuLedger,
    RemoteDenseExecutor,
    WorkerHandle,
    load_dense_remote_config,
    make_provider,
)

TOKEN_ID, TOKEN_SECRET = "fake-id-ABC987", "fake-secret-XYZ123"
REPO = Path(__file__).resolve().parents[2]


@dataclass
class FakeModalClient:
    fail: dict[str, BaseException] = field(default_factory=dict)  # step -> raised
    delay: dict[str, float] = field(default_factory=dict)  # step -> seconds it takes
    calls: list[tuple[str, ...]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _step(self, step: str, *args: str) -> None:
        with self.lock:
            self.calls.append((step, *args))
        time.sleep(self.delay.get(step, 0.0))
        if step in self.fail:
            raise self.fail[step]

    def start(self, image: ModalImageSpec, gpu: str, timeout_s: float) -> str:
        self._step("start", gpu)
        return "sb-fake-1"

    def upload(self, worker_id: str, local_dir: Path, remote_dir: str) -> None:
        self._step("upload", remote_dir)

    def run(self, worker_id: str, argv: list[str], timeout_s: float) -> int:
        self._step("run", argv[1])
        return 0

    def download(self, worker_id: str, remote_path: str, local_dir: Path) -> None:
        self._step("download", remote_path)
        (local_dir / remote_path.rsplit("/", 1)[-1]).write_bytes(b"result")

    def stop(self, worker_id: str) -> None:
        self._step("stop", worker_id)

    def steps(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    frames, sparse = tmp_path / "frames", tmp_path / "sparse/0"
    frames.mkdir()
    sparse.mkdir(parents=True)
    (frames / "000000.jpg").write_bytes(b"jpg")
    (sparse / "cameras.bin").write_bytes(b"bin")
    return {"frames": frames, "sparse": sparse, "out": tmp_path / "out", "root": tmp_path}


@pytest.fixture
def no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(spec: ModalImageSpec) -> None:
        raise AssertionError("the Modal image must not be built in this task")

    monkeypatch.setattr(dense_modal, "to_modal_image", boom)


def executor(env: dict[str, Path], provider: Any, timeout_s: float = 60.0) -> RemoteDenseExecutor:
    cfg = DenseRemoteConfig(
        enabled=True, timeout_s=timeout_s, daily_gpu_hours_budget=1.0, est_hours_per_job=0.5,
        ledger_path=env["root"] / "ledger.json", image="qarau/colmap-cuda:test", gpu_type="L4",
        provider="modal",
    )  # fmt: skip
    return RemoteDenseExecutor(provider, cfg, GpuLedger(cfg.ledger_path))


def go(ex: RemoteDenseExecutor, env: dict[str, Path]) -> Path:
    return ex.run(env["frames"], env["sparse"], env["out"])


@pytest.mark.usefixtures("no_build")
def test_success_order_and_one_stop(env: dict[str, Path]) -> None:
    client = FakeModalClient()
    ply = go(executor(env, ModalGpuProvider(client)), env)
    assert ply == env["out"] / "fused.ply" and ply.is_file()
    assert client.calls == [
        ("start", "L4"),
        ("upload", "/work/images"),
        ("upload", "/work/sparse"),
        ("run", "image_undistorter"),
        ("run", "patch_match_stereo"),
        ("run", "stereo_fusion"),
        *[("download", f"{REMOTE_DENSE}/{name}") for name in RESULTS],
        ("stop", "sb-fake-1"),
    ]


@pytest.mark.usefixtures("no_build")
@pytest.mark.parametrize("step", ["upload", "run", "download"])
def test_error_at_step_stops_worker(env: dict[str, Path], step: str) -> None:
    client = FakeModalClient(fail={step: ConnectionError(f"{step} broke")})
    with pytest.raises(ModalError, match=f"modal .*failed: ConnectionError: {step} broke"):
        go(executor(env, ModalGpuProvider(client)), env)
    assert client.steps()[-1] == "stop" and client.steps().count("stop") == 1
    assert client.steps()[-2] == step


@pytest.mark.usefixtures("no_build")
def test_error_at_start_starts_nothing_to_stop(env: dict[str, Path]) -> None:
    client = FakeModalClient(fail={"start": RuntimeError("no GPU")})
    with pytest.raises(ModalError, match="modal start_worker failed"):
        go(executor(env, ModalGpuProvider(client)), env)
    assert client.steps() == ["start"]


@pytest.mark.usefixtures("no_build")
@pytest.mark.parametrize("step", ["upload", "run", "download"])
def test_hung_call_times_out_and_stops(env: dict[str, Path], step: str) -> None:
    client = FakeModalClient(delay={step: 1.0})
    t0 = time.monotonic()
    with pytest.raises(DenseTimeoutError, match="no answer within"):
        go(executor(env, ModalGpuProvider(client), timeout_s=0.3), env)
    assert time.monotonic() - t0 < 0.9  # the deadline, not the hung call, ended the job
    assert client.steps()[-1] == "stop" and client.steps().count("stop") == 1


@pytest.mark.usefixtures("no_build")
def test_client_timeout_error_is_a_timeout(env: dict[str, Path]) -> None:
    client = FakeModalClient(fail={"run": TimeoutError("exec timed out")})
    with pytest.raises(DenseTimeoutError, match="exec timed out"):
        go(executor(env, ModalGpuProvider(client)), env)
    assert client.steps().count("stop") == 1


@pytest.mark.usefixtures("no_build")
def test_start_answering_late_is_stopped(env: dict[str, Path]) -> None:
    client = FakeModalClient(delay={"start": 0.5})
    with pytest.raises(DenseTimeoutError, match="start_worker"):
        go(executor(env, ModalGpuProvider(client), timeout_s=0.1), env)
    for _ in range(100):  # the late start finishes in its thread, then is stopped
        if "stop" in client.steps():
            break
        time.sleep(0.02)
    assert client.calls == [("start", "L4"), ("stop", "sb-fake-1")]


@pytest.mark.usefixtures("no_build")
@pytest.mark.parametrize("path", ["success", "stop_error", "error", "timeout"])
def test_token_never_leaks(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    monkeypatch.setenv("MODAL_TOKEN_ID", TOKEN_ID)
    monkeypatch.setenv("MODAL_TOKEN_SECRET", TOKEN_SECRET)
    caplog.set_level(logging.DEBUG)
    leak = f"auth {TOKEN_ID}:{TOKEN_SECRET} rejected"  # an SDK error echoing the credentials
    client = {
        "success": FakeModalClient(),
        "stop_error": FakeModalClient(fail={"stop": RuntimeError(leak)}),  # logged retries
        "error": FakeModalClient(fail={"upload": PermissionError(leak)}),
        "timeout": FakeModalClient(fail={"run": TimeoutError(leak)}),
    }[path]
    texts = []
    try:
        go(executor(env, ModalGpuProvider(client)), env)
    except DenseExecutorError as err:
        texts += [str(err), repr(err), "".join(traceback.format_exception(err))]
    assert bool(texts) == (path != "success")
    texts.append(caplog.text)
    # The run's written record: ledger and outputs, every file the job left on disk.
    texts += [p.read_bytes().decode("latin-1") for p in env["root"].rglob("*") if p.is_file()]
    for text in texts:
        assert TOKEN_SECRET not in text and TOKEN_ID not in text
    assert ("***" in "".join(texts)) == (path != "success")  # the error text kept, masked


def test_secret_prints_masked() -> None:
    s = Secret(TOKEN_SECRET)
    assert str(s) == repr(s) == f"{s}" == "***" and s.reveal() == TOKEN_SECRET


def test_missing_token_names_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    with pytest.raises(DenseConfigError, match="MODAL_TOKEN_ID and MODAL_TOKEN_SECRET"):
        ModalGpuProvider.from_env()
    monkeypatch.setenv("MODAL_TOKEN_ID", TOKEN_ID)
    with pytest.raises(DenseConfigError, match="export MODAL_TOKEN_SECRET in") as err:
        ModalGpuProvider.from_env()
    assert TOKEN_ID not in str(err.value)


def test_from_env_reads_environment_only_and_imports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODAL_TOKEN_ID", TOKEN_ID)
    monkeypatch.setenv("MODAL_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.delitem(sys.modules, "modal", raising=False)
    p = ModalGpuProvider.from_env()
    assert "modal" not in sys.modules
    assert TOKEN_SECRET not in repr(p.client) and TOKEN_SECRET not in repr(vars(p.client))


def test_module_does_not_import_modal() -> None:
    code = "import sys, qarau_pipeline.dense_modal; print('modal' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO / "pipeline", capture_output=True, text=True,
        check=True,
    )  # fmt: skip
    assert out.stdout.strip() == "False"


def test_image_spec_is_the_colmap_cuda_dockerfile_never_pushed() -> None:
    spec = ModalImageSpec()
    assert spec.dockerfile == REPO / "infra/colmap-cuda/Dockerfile" and spec.dockerfile.is_file()
    assert spec.push is False and spec.public is False
    with pytest.raises(TypeError):
        ModalImageSpec(push=True)  # type: ignore[call-arg]


class _FakeImage:
    described: list[str] = []
    build_args: list[dict[str, str]] = []

    @classmethod
    def from_dockerfile(cls, path: str, build_args: dict[str, str]) -> _FakeImage:
        cls.described.append(path)
        cls.build_args.append(build_args)
        return cls()

    def entrypoint(self, cmd: list[str]) -> _FakeImage:
        assert cmd == []
        return self

    def build(self, *_: Any, **__: Any) -> None:
        raise AssertionError("the Modal image must not be built in this task")


def test_to_modal_image_describes_without_building(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeImage.described, _FakeImage.build_args = [], []
    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(Image=_FakeImage))
    assert isinstance(dense_modal.to_modal_image(ModalImageSpec()), _FakeImage)
    dense_modal.to_modal_image(ModalImageSpec(cuda_architectures="75;86;90"))
    assert _FakeImage.described == [str(REPO / "infra/colmap-cuda/Dockerfile")] * 2
    assert _FakeImage.build_args == [  # sm_89 (L4) by default; others without code changes
        {"CUDA_ARCHITECTURES": "89"},
        {"CUDA_ARCHITECTURES": "75;86;90"},
    ]


def fake_modal_module(root: Path, log: list[tuple[str, ...]]) -> types.SimpleNamespace:
    """The few `modal` names _RealModalClient uses; a sandbox's filesystem is a directory."""

    class Proc:
        returncode = 0

        def wait(self) -> None:
            pass

    class Filesystem:
        def copy_from_local(self, local: Path, remote: str) -> None:
            log.append(("write", remote))
            (root / remote.lstrip("/")).parent.mkdir(parents=True, exist_ok=True)
            (root / remote.lstrip("/")).write_bytes(Path(local).read_bytes())

        def copy_to_local(self, remote: str, local: Path) -> None:
            log.append(("read", remote))
            Path(local).write_bytes((root / remote.lstrip("/")).read_bytes())

    class Sandbox:
        object_id = "sb-1"
        filesystem = Filesystem()

        @staticmethod
        def create(*cmd: str, **kw: Any) -> Sandbox:
            log.append(("create", *cmd, f"gpu={kw['gpu']}", f"timeout={kw['timeout']}"))
            assert kw["client"] == "client" and isinstance(kw["image"], _FakeImage)
            return Sandbox()

        @staticmethod
        def from_id(sid: str, client: Any) -> Sandbox:
            log.append(("from_id", sid))
            return Sandbox()

        def exec(self, *argv: str, timeout: int, stdout: str, stderr: str) -> Proc:
            log.append(("exec", argv[1], f"timeout={timeout}", stdout, stderr))
            if argv[1] == "stereo_fusion":
                for name in RESULTS:
                    (root / REMOTE_DENSE.lstrip("/")).mkdir(parents=True, exist_ok=True)
                    (root / REMOTE_DENSE.lstrip("/") / name).write_bytes(b"fused")
            return Proc()

        def terminate(self) -> None:
            log.append(("terminate",))

    def from_credentials(token_id: str, token_secret: str) -> str:
        assert (token_id, token_secret) == (TOKEN_ID, TOKEN_SECRET)
        log.append(("credentials",))
        return "client"

    def lookup(name: str, create_if_missing: bool, client: Any) -> str:
        return "app"

    _FakeImage.described = []
    return types.SimpleNamespace(
        Client=types.SimpleNamespace(from_credentials=from_credentials),
        App=types.SimpleNamespace(lookup=lookup),
        Sandbox=Sandbox,
        Image=_FakeImage,
    )


def test_real_client_round_trip_on_fake_modal(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """_RealModalClient's calls (credentials, sandbox create, write, exec, read, terminate)
    against a fake `modal`; the token reaches only Client.from_credentials."""
    monkeypatch.setenv("MODAL_TOKEN_ID", TOKEN_ID)
    monkeypatch.setenv("MODAL_TOKEN_SECRET", TOKEN_SECRET)
    caplog.set_level(logging.DEBUG)
    log: list[tuple[str, ...]] = []
    monkeypatch.setitem(sys.modules, "modal", fake_modal_module(env["root"] / "sandbox", log))
    stream = types.SimpleNamespace(StreamType=types.SimpleNamespace(STDOUT="STDOUT"))
    monkeypatch.setitem(sys.modules, "modal.stream_type", stream)
    cfg = replace(load_dense_remote_config(DEFAULT_CONFIG), enabled=True)
    ex = executor(env, make_provider(cfg))
    ply = go(ex, env)
    assert ply.read_bytes() == b"fused"
    assert log == [
        ("credentials",),
        ("create", "sleep", "infinity", "gpu=L4", "timeout=60"),
        ("write", "/work/images/000000.jpg"),
        ("write", "/work/sparse/cameras.bin"),
        ("exec", "image_undistorter", "timeout=60", "STDOUT", "STDOUT"),
        ("exec", "patch_match_stereo", "timeout=60", "STDOUT", "STDOUT"),
        ("exec", "stereo_fusion", "timeout=60", "STDOUT", "STDOUT"),
        *[("read", f"{REMOTE_DENSE}/{name}") for name in RESULTS],
        ("terminate",),
    ]
    assert TOKEN_SECRET not in caplog.text
    ex.provider.stop(WorkerHandle("sb-other", "modal", datetime.now(UTC)))  # not started here
    assert log[-2:] == [("from_id", "sb-other"), ("terminate",)]


def test_repo_config_selects_modal_disabled_within_budget() -> None:
    cfg = load_dense_remote_config(DEFAULT_CONFIG)
    assert cfg.provider == "modal" and cfg.enabled is False
    assert cfg.daily_gpu_hours_budget == 1.0 and cfg.est_hours_per_job == 0.5
    assert cfg.timeout_s <= 1800


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ('cuda_architectures: "75;86"', "gpu_type L4 needs CUDA arch 89"),
        ('cuda_architectures: "sm_89"', "must be like"),
    ],
)
def test_cuda_architectures_must_fit_the_gpu(tmp_path: Path, line: str, error: str) -> None:
    text = DEFAULT_CONFIG.read_text()
    assert 'cuda_architectures: "89"' in text  # the repo builds sm_89 only, for L4
    (tmp_path / "config.yaml").write_text(text.replace('cuda_architectures: "89"', line))
    with pytest.raises(DenseConfigError, match=error):
        load_dense_remote_config(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text(
        text.replace('cuda_architectures: "89"', 'cuda_architectures: "75;86;90"').replace(
            "gpu_type: L4", "gpu_type: T4"
        )
    )
    assert load_dense_remote_config(tmp_path / "config.yaml").cuda_architectures == "75;86;90"


def test_unknown_provider_is_refused(tmp_path: Path) -> None:
    text = DEFAULT_CONFIG.read_text().replace("provider: modal", "provider: runpod")
    (tmp_path / "config.yaml").write_text(text)
    with pytest.raises(DenseConfigError, match="dense_remote.provider"):
        load_dense_remote_config(tmp_path / "config.yaml")


def test_make_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODAL_TOKEN_ID", TOKEN_ID)
    monkeypatch.setenv("MODAL_TOKEN_SECRET", TOKEN_SECRET)
    cfg = load_dense_remote_config(DEFAULT_CONFIG)
    provider = make_provider(cfg)
    assert isinstance(provider, ModalGpuProvider)
    assert provider.image == ModalImageSpec(cuda_architectures="89")
    other = make_provider(replace(cfg, cuda_architectures="75;86;90"))
    assert isinstance(other, ModalGpuProvider) and other.image.cuda_architectures == "75;86;90"
    with pytest.raises(DenseConfigError, match="not a real GPU provider"):
        make_provider(replace(cfg, provider="fake"))
