# P4a-2a-run — first real COLMAP dense run on Modal (plan)

Interactive, owner present. Worktree `../qarau-dense`, branch `feat/p4a-2a-run`. Kerege not started.
Every paid step waits for the owner's go.

## 0. Found before spending (free)

- `_RealModalClient` used `Sandbox.open` / `Sandbox.mkdir`, which `modal` 1.6.2 no longer has:
  the first run would have started a GPU and failed at upload. Fixed (1311b3a): upload/download
  through `sandbox.filesystem.copy_from_local` / `copy_to_local` (read limit 5 GB per file),
  COLMAP output to our stdout (`StreamType.STDOUT`) instead of an unread pipe.
- `from_dockerfile`: `COPY --from=builder` needs no context upload; no Python in the image and no
  `add_python` (Sandboxes run argv directly; the fs tools are Modal's own binary).
- Image is built lazily inside `Sandbox.create`, which runs under the job's 1800 s deadline; a CUDA
  build of COLMAP can take longer, so the image is built first, separately (step 2).

## Steps

1. Setup (free): `uv sync --extra modal`; owner logs in (`modal token new` → `~/.modal.toml`,
   outside the repo); token passed to the CLI only as env vars read from that file, never printed;
   `modal workspace settings list` / dashboard shows the $30 limit.
2. Build image (paid?, ask): `to_modal_image(ModalImageSpec()).build(app)` with output on — same
   spec as the run, so the run hits the cache. Report time and `modal billing report`.
3. Dense runs (paid, ask each): notre-dame-p3e, then orono-p3e; L4, timeout 1800 s, budget
   1.0 GPU-h/day (two jobs fit). Output to `data/runs/<run>/dense-modal/`.
4. After each: points, runtime, cost (billing report), `.vis` links, files; `modal container list`
   empty. Export with `--dense` into a new run dir for the viewer.
5. Viewer route for the owner.

## Cost (estimate)

L4 $0.000222/s ($0.80/h) + sandbox CPU/memory. Hard ceiling per run: 1800 s ≈ $0.40 GPU.
