# 0018 — Modal as the first real GPU provider for the dense executor

- Status: accepted (dev team, P4a-1b); no Modal call until the owner starts P4a-2
- Date: 2026-10-20

## Context

ADR 0016 put COLMAP dense behind `GpuProvider` and shipped only a fake. P4a-1b adds the first
real provider so P4a-2 (the first real GPU run, the owner's call) needs only a token and a
go. Modal (modal.com) runs a container from a Dockerfile on a rented GPU per second, with no
VM to manage, and its Sandboxes give exactly what the interface needs: start a container,
put files, exec argv, get files, terminate. Modal is a **paid service**: no account, no
token and no call exist in this task; developers have no Modal token.

## Decision

1. **Adapter, not a new executor.** `pipeline/qarau_pipeline/dense_modal.py` →
   `ModalGpuProvider` implements `GpuProvider` unchanged; `RemoteDenseExecutor` (order,
   deadline, budget, always-stop) is used as it is. Config `pipeline/config.yaml` →
   `dense_remote.provider: modal` (`fake | modal`, absent means fake); `make_provider(cfg)`
   in `dense_remote.py` builds it. `enabled` stays `false`.
2. **Injected client, lazy import.** The provider takes a `ModalClient` (protocol: `start`,
   `upload`, `run`, `download`, `stop`; a worker is a Sandbox id). `_RealModalClient` is the
   only code that touches the `modal` package, imported inside its methods, so
   `qarau_pipeline` imports and tests without `modal` installed. Tests use a fake client and
   a fake `modal` module; none touches the network. `modal` is **not** a dependency yet:
   P4a-2 adds it as an optional extra (`uv add --optional modal modal`).
3. **Image from `infra/colmap-cuda`.** `ModalImageSpec` is a frozen description: the
   Dockerfile path, `push` and `public` fixed to `false` (not settable). `to_modal_image()`
   turns it into `modal.Image.from_dockerfile(...)` (empty entrypoint, the sandbox runs
   `sleep infinity` and each COLMAP step is an `exec`). Nothing is built or pushed here.
   GPU type comes from `dense_remote.gpu_type` (`L4`, 24 GB, CUDA arch 89 is in the image's
   build list).
4. **Always stop, also from Modal's side.** The executor's `finally` stop (ADR 0016) holds.
   On top: every client call runs in a daemon thread bounded by the remaining `timeout_s`,
   so a hung call raises `ModalTimeoutError` (a `TimeoutError`, mapped to
   `DenseTimeoutError`) and the worker is stopped; a start that answers after its timeout
   is stopped as soon as it answers; `stop` falls back to `Sandbox.from_id` for a handle this
   process did not start; and the Sandbox is created with `timeout = timeout_s`, so Modal
   itself ends it at the job deadline even if our process is killed. A job can therefore
   never bill longer than `timeout_s`, which the budget's `est_hours_per_job` covers.
5. **Budget.** `daily_gpu_hours_budget: 1.0`, `est_hours_per_job: 0.5`, `timeout_s: 1800`:
   at most two jobs per UTC day. Raise only with the owner.
6. **Token.** Read only from `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` in the environment, at
   run time, in `ModalGpuProvider.from_env()`; never from a file (`~/.modal.toml` is bypassed:
   the client is built with `modal.Client.from_credentials`). Missing variables raise an
   error naming them, never values. Values live in `Secret` (prints `***`) and are revealed
   only to `from_credentials`. Every error text is scrubbed of the token values and raised
   `from None`, so neither logs, nor exception text and tracebacks, nor anything the job
   writes (ledger, outputs) carry it; a test with a fake token checks success, error, stop
   error and timeout paths.

## Licences

Modal Python client (`modal`): Apache-2.0, commercial use allowed. Modal the service: paid,
per-second GPU billing; terms and current prices are checked by the owner before P4a-2.
COLMAP in the image: BSD-3-Clause (full list in `infra/colmap-cuda/README.md`).

## Consequences

- **Owner decisions before P4a-2:** the Modal account and its spending limit; that Modal
  builds and stores the CUDA image inside our Modal workspace (the image is not pushed to
  any registry, but it does live on Modal's side, which `infra/colmap-cuda/README.md` makes
  an owner decision); and whether customer footage may go to Modal's US/EU regions (Kazakh
  customer data stays in Kazakhstan, so Modal is for our own test footage only, and on-prem
  uses the customer's GPU through another `GpuProvider`).
- **P4a-2 must check against a real account** what no fake can: `from_dockerfile` builds the
  image (CUDA build time on Modal's builder); the container runs as root or as `colmap`
  with a writable `/work`; the Sandbox filesystem API (`mkdir`, `open`) for upload and
  download; `exec(...).wait()` and its exit code; COLMAP's stdout/stderr volume through
  `exec`; `terminate` on an ended sandbox. `_RealModalClient` is written to the documented
  API but has never run.
- File transfer is per file through the Sandbox filesystem; fine for a few hundred frames.
  If uploads dominate, switch to a Modal Volume behind the same `ModalClient`.
