"""refresh: every benchmark_set.toml video -> current sparse CPU pipeline -> a NEW viewer run.

`python -m qarau_pipeline refresh --data D --runs R --suffix S` writes R/<video>-<S> per video.
Same child-process `run` as the benchmark (sparse COLMAP on the CPU, never dense or
dense_remote). Append-only on R: a target name that already exists stops the command before any
work; each run is built in a hidden temp dir inside R and renamed into place only when done, so
a failure never leaves a partial run under the final name and nothing already in R is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .benchmark import MANIFEST, load_manifest, run_video
from .interfaces import StageError

COLS = ("run", "registered_pct", "points", "far_points", "keyframes", "status")


def _row(run: Path, m: dict[str, Any]) -> dict[str, Any]:
    stats = json.loads((run / "web/manifest.json").read_text())["stats"]
    used, reg = m["images_in"], m["registered_images"]
    return {
        "run": run.name,
        "registered_pct": round(100 * reg / used, 1) if used else 0.0,
        "points": stats["points"],  # object cloud shown in the viewer (far layer excluded)
        "far_points": stats["far_points"],
        "keyframes": used,  # kept frames handed to COLMAP (P2b parallax keyframes)
        "status": "ok",
    }


def refresh(
    data_dir: str | Path,
    runs_dir: str | Path,
    suffix: str,
    only: list[str] | None = None,
    timeout_s: float = 1800,
    manifest: Path = MANIFEST,
) -> dict[str, dict[str, Any]]:
    data, runs = Path(data_dir).resolve(), Path(runs_dir).resolve()
    entries = load_manifest(manifest)
    if only:
        unknown = set(only) - {e["name"] for e in entries}
        if unknown:
            raise StageError(f"unknown benchmark video(s): {', '.join(sorted(unknown))}")
        entries = [e for e in entries if e["name"] in only]
    targets = {e["name"]: runs / f"{e['name']}-{suffix}" for e in entries}
    taken = [t.name for t in targets.values() if t.exists() or t.is_symlink()]
    if taken:
        raise StageError(f"run(s) already exist in {runs}, pick another --suffix: {taken}")
    runs.mkdir(parents=True, exist_ok=True)
    res: dict[str, dict[str, Any]] = {}
    for e in entries:
        name, final = e["name"], targets[e["name"]]
        video = data / e["file"]
        if not video.is_file():
            res[name] = {"run": final.name, "status": f"skipped: video missing ({e['file']})"}
            continue
        print(f"refresh: {name} -> {final.name}", flush=True)
        # leading dot: the API lists only names matching [A-Za-z0-9]..., so a run being built
        # never shows up in the web app
        tmp = Path(tempfile.mkdtemp(prefix=f".{final.name}.", dir=runs))
        try:
            m = run_video(video, tmp, timeout_s, e.get("max_frames"))
            mf = tmp / "web/manifest.json"
            # export took run_id from the temp dir name; created_at it set at export time
            mf.write_text(json.dumps({**json.loads(mf.read_text()), "run_id": final.name}))
            if final.exists() or final.is_symlink():  # appeared while we ran: never replace it
                raise StageError(f"{final.name} appeared during the run; not replaced")
            os.rename(tmp, final)
            res[name] = _row(final, m)
        except subprocess.TimeoutExpired:
            res[name] = {"run": final.name, "status": f"failed: timeout after {timeout_s:g} s"}
        except Exception as err:  # noqa: BLE001 - one video's failure must not stop the rest
            res[name] = {"run": final.name, "status": f"failed: {type(err).__name__}: {err}"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)  # our own temp dir, gone after a rename
        print(f"refresh: {name}: {res[name]}", flush=True)
    return res


def markdown(res: dict[str, dict[str, Any]]) -> str:
    head = "| Video | Run | Registered % | Points | Far-layer points | Keyframes | Status |"
    rows = [head, "| --- " * 7 + "|"]
    for n, r in res.items():
        rows.append(f"| {n} | " + " | ".join(str(r.get(k, "—")) for k in COLS) + " |")
    return "\n".join(rows)
