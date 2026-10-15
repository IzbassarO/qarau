"""benchmark: every reference video (benchmark_set.toml) -> pipeline -> one metrics table.

Contract (merge gate): `python -m qarau_pipeline benchmark --data D --out O` reads videos from
D (read-only), writes everything under O, O/results.json = {"videos": {name: {...}}, ...};
exit 0 only if every required video and every present optional video ran.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from .interfaces import StageError, read_run

PKG = Path(__file__).resolve().parent
MANIFEST = PKG / "benchmark_set.toml"
REPO = PKG.parents[1]
SECTION = "## Automated benchmark (P1)"
HEAD = (
    "| Video | Commit | Frames used | Registered % | Models | Points | Reproj. px "
    "| Scale err cm | Time s | Notes |\n" + "| --- " * 10 + "|"
)


def load_manifest(path: Path = MANIFEST) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = tomllib.loads(path.read_text())["video"]
    return entries


def run_video(
    video: Path, out: Path, timeout_s: float, max_frames: int | None = None
) -> dict[str, Any]:
    """`qarau_pipeline run` in a child process: pycolmap cannot be interrupted in-process and
    a COLMAP crash must not take the other videos down. Returns the reconstruction metrics."""
    out.mkdir(parents=True, exist_ok=True)
    log = out / "log.txt"
    with log.open("w") as f:
        cmd = [sys.executable, "-m", "qarau_pipeline", "run", "--video", str(video)]
        if max_frames:
            cmd += ["--max-frames", str(max_frames)]
        p = subprocess.run(
            [*cmd, "--out", str(out)], cwd=PKG.parent, stdout=f, stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )  # fmt: skip
    if p.returncode != 0:
        lines = log.read_text().strip().splitlines()
        raise StageError(f"exit {p.returncode}: {lines[-1] if lines else 'no output'}")
    metrics: dict[str, Any] = next(
        s["metrics"] for s in read_run(out) if s["name"] == "reconstruction"
    )
    return metrics


def video_row(m: dict[str, Any], time_s: float, max_frames: int | None = None) -> dict[str, Any]:
    used, reg = m["images_in"], m["registered_images"]
    return {
        "registered_pct": round(100 * reg / used, 1) if used else 0.0,
        "models": m["models"],
        "reproj_px": m["mean_reproj_error_px"],
        "points": m["points"],
        "scale_err_cm": None,  # P5/P7: needs a reference scale
        "time_s": round(time_s, 1),
        "frames_used": used,
        "registered": reg,
        "max_frames": max_frames,  # benchmark profile from benchmark_set.toml; None = default
    }


def md_rows(res: dict[str, Any]) -> list[str]:
    tag = res["commit"] + ("+dirty" if res["dirty"] else "")
    keys = "frames_used registered_pct models points reproj_px scale_err_cm time_s".split()
    rows = []
    for n, v in res["videos"].items():
        cells = ["" if v[k] is None else str(v[k]) for k in keys]
        note = f"max_frames {v['max_frames']}" if v.get("max_frames") else ""
        rows.append(f"| {n} | {tag} | " + " | ".join([*cells, note]) + " |")
    dash = " | ".join(["—"] * len(keys))
    return rows + [f"| {n} | {tag} | {dash} | FAILED: {r} |" for n, r in res["errors"].items()]


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _version() -> str:
    try:
        return metadata.version("qarau-pipeline")
    except metadata.PackageNotFoundError:  # run from source: the uv project is not installed
        project: dict[str, Any] = tomllib.loads((PKG.parent / "pyproject.toml").read_text())
        return str(project["project"]["version"])


def _write(out: Path, res: dict[str, Any]) -> None:
    (out / "results.json").write_text(json.dumps(res, indent=2) + "\n")
    skipped = [f"- skipped: {n}" for n in res["skipped"]]
    (out / "results.md").write_text("\n".join([HEAD, *md_rows(res), "", *skipped]) + "\n")


def _record(res: dict[str, Any], repo: Path) -> None:
    """docs/benchmarks/<sha>.json + rows appended to docs/notes/benchmark.md (append only)."""
    bdir = repo / "docs/benchmarks"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / f"{res['commit']}.json").write_text(json.dumps(res, indent=2) + "\n")
    md = repo / "docs/notes/benchmark.md"
    text = md.read_text().rstrip("\n") + "\n"
    if SECTION not in text:
        # ponytail: rows go to the end of the file, so this section must stay the last one
        text += (
            f"\n{SECTION}\n\nRows appended by `python -m qarau_pipeline benchmark --record`"
            " (method: `docs/notes/benchmark-method.md`). `+dirty` = uncommitted changes on"
            " top of the commit.\n\n" + HEAD + "\n"
        )
    md.write_text(text + "".join(r + "\n" for r in md_rows(res)))


def run_benchmark(
    data_dir: str | Path,
    out_dir: str | Path,
    only: list[str] | None = None,
    timeout_s: float = 1800,
    record: bool = False,
    repo_root: Path = REPO,
    manifest: Path = MANIFEST,
) -> int:
    data, out = Path(data_dir).resolve(), Path(out_dir).resolve()
    if out.is_relative_to(data):
        raise StageError("--out must not be inside --data (the data dir is read-only)")
    entries = load_manifest(manifest)
    if only:
        unknown = set(only) - {e["name"] for e in entries}
        if unknown:
            raise StageError(f"unknown benchmark video(s): {', '.join(sorted(unknown))}")
        entries = [e for e in entries if e["name"] in only]
    res: dict[str, Any] = {
        "commit": _git(repo_root, "rev-parse", "--short", "HEAD") or "unknown",
        "dirty": bool(_git(repo_root, "status", "--porcelain")),
        "pipeline_version": _version(),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "videos": {},
        "errors": {},
        "skipped": [],
    }
    out.mkdir(parents=True, exist_ok=True)
    for e in entries:
        name, video = e["name"], data / e["file"]
        if not video.is_file():
            if e["required"]:
                res["errors"][name] = f"required video missing: {e['file']}"
            else:
                res["skipped"].append(name)
            continue
        print(f"benchmark: {name} ({e['file']})", flush=True)
        t0 = time.perf_counter()
        try:
            m = run_video(video, out / name, timeout_s, e.get("max_frames"))
            res["videos"][name] = video_row(m, time.perf_counter() - t0, e.get("max_frames"))
        except subprocess.TimeoutExpired:
            res["errors"][name] = f"timeout after {timeout_s:g} s"
        except Exception as err:  # noqa: BLE001 - one video's failure must not stop the rest
            res["errors"][name] = f"{type(err).__name__}: {err}"
        print(f"benchmark: {name}: {res['videos'].get(name) or res['errors'][name]}", flush=True)
        _write(out, res)  # partial results survive an interrupted run
    _write(out, res)
    if record:
        _record(res, repo_root)
    return 1 if res["errors"] else 0
