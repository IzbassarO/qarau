# 0016 — Remote dense executor: provider interface, always-stop, budget before start

- Status: accepted (dev team, P4a-1)
- Date: 2026-10-17

## Context

COLMAP's dense stage (PatchMatch stereo) needs a CUDA GPU; our dev machines have none and
the sparse path runs on CPU pycolmap. A rented GPU worker costs money per hour, and a worker
that is never shut down keeps billing silently. Which provider we rent from is not decided,
and the owner has not approved a paid account.

## Decision

1. **Provider interface.** `GpuProvider` (`pipeline/qarau_pipeline/dense_remote.py`):
   `start_worker`, `upload`, `run`, `download`, `stop`. The executor owns the order, the
   deadline and the budget; a provider only moves files and runs argv on the image
   `infra/colmap-cuda` (COLMAP 4.2.1 + CUDA). Swapping providers, or running on the
   customer's own GPU server on-prem, touches nothing else. No vendor SDK is imported.
2. **Always stop.** Once `start_worker` returned a handle, `stop` runs in a `finally`:
   on success, on any exception, on timeout and on `KeyboardInterrupt`. `stop` must be
   idempotent; it is retried 3 times, then a `DenseExecutorError` naming the worker id and
   provider is raised (and logged) so a leaked worker is never silent. `start_worker` itself
   must either return a handle or leave nothing running.
3. **Budget before start.** `pipeline/config.yaml` → `dense_remote.daily_gpu_hours_budget`
   per UTC day. The ledger (`ledger_path`, JSON `{UTC date: hours}`, under gitignored
   `data/`) is checked first: `used + est_hours_per_job > budget` raises
   `BudgetExceededError` and no worker is started (equal is allowed). The estimate is booked
   before the start and settled to the measured wall-clock hours (start to stop) at the end,
   so a crashed process over-counts, never under-counts. Days are only added or updated.
4. **One deadline.** `timeout_s` is a single wall-clock deadline for the whole job. Each
   provider call gets the remaining seconds; a provider `TimeoutError`, or a call that
   returns after the deadline, raises `DenseTimeoutError` (and the worker is stopped). The
   loader requires `timeout_s <= est_hours_per_job · 3600`, so the booked estimate covers
   the longest a job can run.
5. **Off by default, fake only.** `dense_remote.enabled: false`; the executor refuses to run
   while it is false. The only provider is `FakeGpuProvider` (`dense_fake.py`: local
   directories, injected clock, injectable failures). No real provider is written until the
   owner approves a paid GPU account. The default `run` / `benchmark` path does not import
   the executor; `python -m qarau_pipeline dense-remote --provider fake` shows the round trip.
   Update (P4a-1b): a Modal provider exists (`dense_modal.py`, ADR 0018), selected by
   `dense_remote.provider: modal`; nothing calls it before the owner starts P4a-2.

## Consequences

- Owner questions: which GPU provider and account (paid service); whether the CUDA image may
  be pushed to any registry (NVIDIA CUDA EULA redistribution terms). Until then the image is
  built locally only and is not in CI.
- The ledger has no file lock: two jobs settling at the same instant can lose an update.
  Fine while one dense job runs at a time; add a lock before running them in parallel.
- Measured hours are our wall clock, not the provider's invoice; a provider that bills a
  minimum (e.g. per started hour) needs `est_hours_per_job` set to cover it.
