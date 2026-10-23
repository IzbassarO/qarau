"""refresh: every benchmark_set.toml video -> current sparse CPU pipeline -> a NEW viewer run.

`python -m qarau_pipeline refresh --data D --runs R --suffix S` writes R/<video>-<S> per video
and nothing else into R. Same child-process `run` as the benchmark (sparse COLMAP on the CPU,
never dense or dense_remote). Append-only on R: a video whose target name already exists
fails before any work and that path is never opened; each run is built in a temp dir outside
R (`--work`, same filesystem) under its final name and moved in with one rename when done, so
a failure never leaves a partial run in R and nothing already in R is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import layout
from .benchmark import MANIFEST, load_manifest, run_video
from .interfaces import StageError

COLS = ("run", "registered_pct", "points", "far_points", "keyframes", "time_s", "status")


def _row(run: str, status: str) -> dict[str, Any]:
    return {**dict.fromkeys(COLS), "run": run, "status": status}


def _ok(run: Path, m: dict[str, Any], time_s: float) -> dict[str, Any]:
    stats = json.loads(layout.manifest_path(run).read_text())["stats"]
    used, reg = m["images_in"], m["registered_images"]
    return {
        "run": run.name,
        "registered_pct": round(100 * reg / used, 1) if used else 0.0,
        "points": stats["points"],  # object cloud shown in the viewer (far layer excluded)
        "far_points": stats["far_points"],
        "keyframes": used,  # kept frames handed to COLMAP (P2b parallax keyframes)
        "time_s": round(time_s, 1),
        "status": "ok",
    }


def refresh(
    data_dir: str | Path,
    runs_dir: str | Path,
    suffix: str,
    only: list[str] | None = None,
    timeout_s: float = 1800,
    manifest: Path = MANIFEST,
    work_dir: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    data, runs = Path(data_dir).resolve(), Path(runs_dir).resolve()
    work = Path(work_dir or tempfile.gettempdir()).resolve()
    if work.is_relative_to(runs):
        raise StageError(f"--work {work} must be outside --runs {runs}")
    entries = load_manifest(manifest)
    if only:
        unknown = set(only) - {e["name"] for e in entries}
        if unknown:
            raise StageError(f"unknown benchmark video(s): {', '.join(sorted(unknown))}")
        by_name = {e["name"]: e for e in entries}
        entries = [by_name[n] for n in dict.fromkeys(only)]  # --only order: run these first
    if not runs.is_dir():
        raise StageError(f"runs dir {runs} does not exist")
    work.mkdir(parents=True, exist_ok=True)
    res: dict[str, dict[str, Any]] = {}
    for e in entries:
        name, final = e["name"], runs / f"{e['name']}-{suffix}"
        video = data / e["file"]
        if final.exists() or final.is_symlink():  # never written, renamed or deleted
            res[name] = _row(final.name, "failed: run already exists, pick another --suffix")
            continue
        if not video.is_file():
            res[name] = _row(final.name, f"failed: video missing ({e['file']})")
            continue
        print(f"refresh: {name} -> {final.name}", flush=True)
        tmp = Path(tempfile.mkdtemp(prefix="qarau-refresh-", dir=work))
        out = tmp / final.name  # export takes run_id from the dir name: already the final one
        t0 = time.monotonic()
        try:
            m = run_video(video, out, timeout_s, e.get("max_frames"))
            if final.exists() or final.is_symlink():  # appeared while we ran: never replace it
                raise StageError(f"{final.name} appeared during the run; not replaced")
            os.rename(out, final)
            res[name] = _ok(final, m, time.monotonic() - t0)
        except subprocess.TimeoutExpired:
            res[name] = _row(final.name, f"failed: timeout after {timeout_s:g} s")
        except Exception as err:  # noqa: BLE001 - one video's failure must not stop the rest
            res[name] = _row(final.name, f"failed: {type(err).__name__}: {err}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)  # our own temp dir outside runs
        print(f"refresh: {name}: {res[name]}", flush=True)
    if not only:  # image folders, archives, other clips: listed so nothing is silently ignored
        files = {e["file"] for e in load_manifest(manifest)}
        for p in sorted(data.iterdir()):
            if not p.name.startswith(".") and p.name not in files:
                res[p.name] = _row("", "skipped: not a reference video")
    return res


def markdown(res: dict[str, dict[str, Any]]) -> str:
    head = ("Video", "Run", "Registered %", "Points", "Far-layer points", "Keyframes", "Time s",
            "Status")  # fmt: skip
    rows = ["| " + " | ".join(head) + " |", "| --- " * len(head) + "|"]
    for n, r in res.items():
        cells = ("—" if r[k] in (None, "") else str(r[k]) for k in COLS)
        rows.append(f"| {n} | " + " | ".join(cells) + " |")
    return "\n".join(rows)
