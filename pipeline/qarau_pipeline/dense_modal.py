"""Modal (modal.com) GpuProvider for the remote dense executor (ADR 0019, ADR 0016).

ModalGpuProvider adds what the executor expects of a provider on top of a narrow ModalClient:
every call is bounded by its timeout_s (a hung call raises ModalTimeoutError, a TimeoutError),
a start that answers too late is stopped, and error text never carries the Modal token.
The executor owns the order and the always-stop. `modal` (Apache-2.0) is imported only inside
_RealModalClient and to_modal_image, so this module loads and is tested without it; nothing
calls Modal until the owner approves P4a-2a-run (a paid service).
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

from .dense_remote import DenseConfigError, DenseExecutorError, WorkerHandle, WorkerSpec

log = logging.getLogger(__name__)

TOKEN_VARS = ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")
APP_NAME = "qarau-dense"
STOP_TIMEOUT_S = 120.0
# Modal's answer when it has no GPU of the asked type (2026-10-21: a 28 min wait for an L4).
NO_CAPACITY = re.compile(r"ResourceExhausted|Insufficient capacity", re.I)
DOCKERFILE = Path(__file__).resolve().parents[2] / "infra" / "colmap-cuda" / "Dockerfile"

T = TypeVar("T")


class ModalError(DenseExecutorError):
    """A Modal call failed; the message is scrubbed of the token."""


class ModalTimeoutError(TimeoutError):
    """A Modal call did not answer within its timeout_s (the executor maps it to
    DenseTimeoutError and stops the worker)."""


class Secret:
    """A credential that prints as ***; reveal() only where Modal's client takes it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "***"

    __str__ = __repr__


def _scrub(text: str) -> str:
    for var in TOKEN_VARS:
        if value := os.environ.get(var):
            text = text.replace(value, "***")
    return text


@dataclass(frozen=True)
class ModalImageSpec:
    """The worker image: COLMAP + CUDA from infra/colmap-cuda (ADR 0016). A description only;
    Modal builds it inside the account on the first start, nothing is pushed to a registry.
    cuda_architectures: the Dockerfile's CUDA_ARCHITECTURES build arg (dense_remote config);
    another value is another image, built once."""

    dockerfile: Path = DOCKERFILE
    cuda_architectures: str = "89"
    push: bool = field(default=False, init=False)
    public: bool = field(default=False, init=False)


def to_modal_image(spec: ModalImageSpec) -> Any:
    import modal

    # Lazy: from_dockerfile only describes the image. Empty entrypoint: the Dockerfile's
    # `colmap` entrypoint would otherwise wrap the sandbox's keep-alive command.
    return modal.Image.from_dockerfile(
        str(spec.dockerfile), build_args={"CUDA_ARCHITECTURES": spec.cuda_architectures}
    ).entrypoint([])


class ModalClient(Protocol):
    """The only Modal operations the provider needs. A worker is a Modal Sandbox, by id."""

    def start(
        self, image: ModalImageSpec, gpu: str, timeout_s: float, tags: dict[str, str]
    ) -> str: ...

    def upload(self, worker_id: str, local_dir: Path, remote_dir: str) -> None: ...

    def run(self, worker_id: str, argv: list[str], timeout_s: float) -> int: ...

    def download(self, worker_id: str, remote_path: str, local_dir: Path) -> None: ...

    def stop(self, worker_id: str) -> None: ...

    def stop_tagged(self, tags: dict[str, str]) -> None:
        """Terminate every sandbox with these tags: one still queued for a GPU has no id yet."""
        ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ModalGpuProvider:
    name = "modal"

    def __init__(
        self,
        client: ModalClient,
        image: ModalImageSpec | None = None,
        clock: Callable[[], datetime] = _utc_now,
        fallback_gpus: tuple[str, ...] = (),
        start_timeout_s: float | None = None,
    ) -> None:
        self.client, self.image, self.clock = client, image or ModalImageSpec(), clock
        # Tried in order after spec.gpu_type when Modal has no capacity within start_timeout_s.
        self.fallback_gpus, self.start_timeout_s = fallback_gpus, start_timeout_s

    @classmethod
    def from_env(
        cls,
        image: ModalImageSpec | None = None,
        fallback_gpus: tuple[str, ...] = (),
        start_timeout_s: float | None = None,
    ) -> ModalGpuProvider:
        """The real client; the token comes only from the environment, read now."""
        missing = [v for v in TOKEN_VARS if not os.environ.get(v)]
        if missing:
            raise DenseConfigError(
                f"Modal token not set: export {' and '.join(missing)} in the environment "
                "(never in a file in the repository)"
            )
        client = _RealModalClient(*(Secret(os.environ[v]) for v in TOKEN_VARS))
        return cls(client, image, fallback_gpus=fallback_gpus, start_timeout_s=start_timeout_s)

    def _bounded(
        self,
        step: str,
        timeout_s: float,
        fn: Callable[[], T],
        on_late: Callable[[T], None] | None = None,
    ) -> T:
        """fn() in a daemon thread, waited on for timeout_s: a hung Modal call cannot hold the
        job past its deadline (the executor then stops the worker, which ends the call)."""
        fut: Future[T] = Future()

        def work() -> None:
            try:
                fut.set_result(fn())
            except BaseException as err:  # noqa: BLE001 - handed to the caller via fut
                fut.set_exception(err)

        threading.Thread(target=work, name=f"modal-{step}", daemon=True).start()
        try:
            return fut.result(timeout=max(timeout_s, 0.0))
        except TimeoutError as err:
            if not (fut.done() and fut.exception() is err):  # our wait ran out, not fn's own
                if on_late:  # runs at once if fn finished just after the wait
                    fut.add_done_callback(lambda f: f.exception() or on_late(f.result()))
                raise ModalTimeoutError(f"modal {step}: no answer within {timeout_s:g} s") from None
            raise ModalTimeoutError(f"modal {step}: {_scrub(str(err))}") from None
        except Exception as err:
            # from None: the original error's text may hold what was just scrubbed.
            raise ModalError(
                f"modal {step} failed: {type(err).__name__}: {_scrub(str(err))}"
            ) from None

    def _stop_late(self, worker_id: str) -> None:
        log.warning("modal worker %s started after its start timed out; stopping it", worker_id)
        try:
            self.client.stop(worker_id)
        except Exception as err:  # noqa: BLE001 - the sandbox timeout still ends it
            log.error("modal worker %s may still be running until its sandbox timeout: %s",
                      worker_id, _scrub(str(err)))  # fmt: skip

    def _stop_tagged(self, tags: dict[str, str]) -> None:
        try:
            self._bounded("stop", STOP_TIMEOUT_S, lambda: self.client.stop_tagged(tags))
        except (ModalError, ModalTimeoutError) as err:  # its sandbox timeout still ends it
            log.error("modal: a queued sandbox %s may start later: %s", tags, err)

    def start_worker(self, spec: WorkerSpec, timeout_s: float) -> WorkerHandle:
        """spec.gpu_type, then each fallback GPU, each given start_timeout_s to get capacity.
        Every attempt is tagged, and a failed one is terminated by its tag: a sandbox still
        queued for a GPU has no id, and would otherwise start later and bill until its timeout."""
        gpus, end = (spec.gpu_type, *self.fallback_gpus), time.monotonic() + timeout_s
        for i, gpu in enumerate(gpus):
            left = end - time.monotonic()
            wait = left if self.start_timeout_s is None else min(left, self.start_timeout_s)
            tags = {"qarau-job": uuid.uuid4().hex}
            try:
                # The sandbox's own lifetime is the job deadline: Modal ends it even if this
                # process dies.
                wid = self._bounded(
                    "start_worker",
                    wait,
                    partial(self.client.start, self.image, gpu, left, tags),
                    on_late=self._stop_late,
                )
            except (ModalError, ModalTimeoutError) as err:
                self._stop_tagged(tags)
                no_gpu = NO_CAPACITY.search(str(err)) or (
                    isinstance(err, ModalTimeoutError) and wait < left
                )
                if not no_gpu:
                    raise
                if i + 1 == len(gpus):
                    raise ModalError(
                        f"modal: no GPU capacity for {', '.join(gpus)} "
                        f"(start_timeout_s {self.start_timeout_s:g} s each); try later"
                    ) from None
                log.warning("modal: no %s capacity (%s); trying %s", gpu, err, gpus[i + 1])
                continue
            log.warning("modal worker %s started on %s", wid, gpu)
            return WorkerHandle(wid, self.name, self.clock())
        raise AssertionError("unreachable")

    def upload(
        self, handle: WorkerHandle, local_dir: Path, remote_dir: str, timeout_s: float
    ) -> None:
        self._bounded(
            "upload", timeout_s, lambda: self.client.upload(handle.id, local_dir, remote_dir)
        )

    def run(self, handle: WorkerHandle, argv: list[str], timeout_s: float) -> int:
        return self._bounded(
            argv[1], timeout_s, lambda: self.client.run(handle.id, argv, timeout_s)
        )

    def download(
        self, handle: WorkerHandle, remote_path: str, local_dir: Path, timeout_s: float
    ) -> None:
        self._bounded(
            "download", timeout_s, lambda: self.client.download(handle.id, remote_path, local_dir)
        )

    def stop(self, handle: WorkerHandle) -> None:
        self._bounded("stop", STOP_TIMEOUT_S, lambda: self.client.stop(handle.id))


class _RealModalClient:
    """Modal Sandboxes through the `modal` client. Never exercised before P4a-2a-run: no token
    and no call in tests (ADR 0019 lists what P4a-2a-run must check against a real account)."""

    def __init__(self, token_id: Secret, token_secret: Secret) -> None:
        self._token_id, self._token_secret = token_id, token_secret
        self._client: Any = None
        self._sandboxes: dict[str, Any] = {}

    def __repr__(self) -> str:
        return "_RealModalClient(token=***)"

    def _modal_client(self) -> Any:
        import modal

        # Explicit credentials, so a ~/.modal.toml profile is never picked up instead.
        if self._client is None:
            self._client = modal.Client.from_credentials(
                self._token_id.reveal(), self._token_secret.reveal()
            )
        return self._client

    def _app(self) -> Any:
        import modal

        return modal.App.lookup(APP_NAME, create_if_missing=True, client=self._modal_client())

    def start(self, image: ModalImageSpec, gpu: str, timeout_s: float, tags: dict[str, str]) -> str:
        import modal

        sb = modal.Sandbox.create(
            "sleep", "infinity", app=self._app(), image=to_modal_image(image), gpu=gpu,
            timeout=math.ceil(timeout_s), tags=tags, client=self._modal_client(),
        )  # fmt: skip
        self._sandboxes[sb.object_id] = sb
        return str(sb.object_id)

    def upload(self, worker_id: str, local_dir: Path, remote_dir: str) -> None:
        fs = self._sandboxes[worker_id].filesystem  # copy_from_local creates parent dirs
        for path in sorted(p for p in local_dir.rglob("*") if p.is_file()):
            fs.copy_from_local(path, f"{remote_dir}/{path.relative_to(local_dir).as_posix()}")

    def run(self, worker_id: str, argv: list[str], timeout_s: float) -> int:
        from modal.stream_type import StreamType

        # COLMAP's log goes straight to our stdout: an unread PIPE would only pile up.
        proc = self._sandboxes[worker_id].exec(
            *argv, timeout=math.ceil(timeout_s), stdout=StreamType.STDOUT, stderr=StreamType.STDOUT
        )
        proc.wait()
        return int(proc.returncode)

    def download(self, worker_id: str, remote_path: str, local_dir: Path) -> None:
        local = local_dir / remote_path.rsplit("/", 1)[-1]
        self._sandboxes[worker_id].filesystem.copy_to_local(remote_path, local)

    def stop(self, worker_id: str) -> None:
        import modal

        # Idempotent: terminating an ended sandbox is harmless. from_id covers a handle this
        # client did not start, so a stop is never silently skipped.
        sb = self._sandboxes.get(worker_id) or modal.Sandbox.from_id(
            worker_id, client=self._modal_client()
        )
        sb.terminate()

    def stop_tagged(self, tags: dict[str, str]) -> None:
        import modal

        app_id, client = self._app().app_id, self._modal_client()
        for sb in modal.Sandbox.list(app_id=app_id, tags=tags, client=client):
            sb.terminate()
