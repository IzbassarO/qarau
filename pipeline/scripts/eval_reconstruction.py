"""Evaluate matching / mapper configs for reconstruction (ADR 0007).

    uv run python scripts/eval_reconstruction.py aegviidu orono --configs baseline,loop,ov20
    uv run python scripts/eval_reconstruction.py south-building --configs exh_inc,exh_global

One feature database per run is extracted once (data/eval/recon/<run>/features.db) and copied
for each config. Writes data/eval/recon/<run>/<config>/{sparse/0,metrics.json}; prints a table.
Runs live in <data>/runs/<run> (frames.json, kept frames); south-building is an image set.
Data dir: $QARAU_DATA or <repo>/data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pycolmap

DATA = Path(os.environ.get("QARAU_DATA") or Path(__file__).resolve().parents[2] / "data")
VOCAB = (
    "https://github.com/colmap/colmap/releases/download/3.11.1/"
    "vocab_tree_faiss_flickr100K_words256K.bin;vocab_tree_faiss_flickr100K_words256K.bin;"
    "96ca8ec8ea60b1f73465aaf2c401fd3b3ca75cdba2d3c50d6a2f6f760f275ddc"
)  # COLMAP downloads + sha256-checks this into ~/.cache/colmap on first use

# match: seq|exh; overlap; loop; mapper: incremental|global
CONFIGS: dict[str, dict[str, Any]] = {
    "baseline": dict(match="seq", overlap=10, loop=False, mapper="incremental"),
    "loop": dict(match="seq", overlap=10, loop=True, mapper="incremental"),
    "ov20": dict(match="seq", overlap=20, loop=False, mapper="incremental"),
    "ov30": dict(match="seq", overlap=30, loop=False, mapper="incremental"),
    "global": dict(match="seq", overlap=10, loop=False, mapper="global"),
    "ov20_loop": dict(match="seq", overlap=20, loop=True, mapper="incremental"),
    "ov20_loop_global": dict(match="seq", overlap=20, loop=True, mapper="global"),
    "exh_inc": dict(match="exh", overlap=0, loop=False, mapper="incremental"),
    "exh_global": dict(match="exh", overlap=0, loop=False, mapper="global"),
}


def run_inputs(run: str) -> tuple[Path, list[str], str]:
    if run == "south-building":
        d = DATA / "samples/south-building/images"
        return d, sorted(p.name for p in d.glob("*.jpg")), "images"
    d = DATA / "runs" / run
    fj = json.loads((d / "frames.json").read_text())
    return d / "frames", [Path(f["file"]).name for f in fj["frames"] if f["kept"]], "video"


def extract(run: str, out: Path) -> float:
    frames, names, mode = run_inputs(run)
    db, tf = out / "features.db", out / "features_seconds.txt"
    if db.exists() and tf.exists():
        return float(tf.read_text())
    db.unlink(missing_ok=True)
    ext = pycolmap.FeatureExtractionOptions()
    ext.max_image_size = 1600
    rd = pycolmap.ImageReaderOptions()
    rd.camera_model = "SIMPLE_RADIAL"
    t = time.perf_counter()
    pycolmap.extract_features(
        db, frames, image_names=names,
        camera_mode=pycolmap.CameraMode.SINGLE if mode == "video" else pycolmap.CameraMode.AUTO,
        reader_options=rd, extraction_options=ext,
    )  # fmt: skip
    s = time.perf_counter() - t
    tf.write_text(str(s))
    return s


def match(db: Path, cfg: dict[str, Any]) -> None:
    if cfg["match"] == "exh":
        pycolmap.match_exhaustive(db)
        return
    p = pycolmap.SequentialPairingOptions(
        overlap=cfg["overlap"], quadratic_overlap=True, loop_detection=cfg["loop"]
    )
    if cfg["loop"]:
        p.vocab_tree_path = VOCAB
    pycolmap.match_sequential(db, pairing_options=p)


def evaluate(run: str, name: str, cfg: dict[str, Any], t_feat: float, root: Path) -> dict[str, Any]:
    frames, names, _ = run_inputs(run)
    out = root / name
    shutil.rmtree(out, ignore_errors=True)
    (out / "maps").mkdir(parents=True)
    db = out / "database.db"
    shutil.copy(root / "features.db", db)
    t0 = time.perf_counter()
    match(db, cfg)
    t1 = time.perf_counter()
    if cfg["mapper"] == "global":
        models = pycolmap.global_mapping(db, frames, out / "maps")
    else:
        models = pycolmap.incremental_mapping(db, frames, out / "maps")
    t2 = time.perf_counter()
    m: dict[str, Any] = {"run": run, "config": name, **cfg, "images_in": len(names)}
    if models:
        best = max(models.values(), key=lambda r: r.num_reg_images())
        (out / "sparse/0").mkdir(parents=True)
        best.write_binary(str(out / "sparse/0"))
        m.update(
            registered=best.num_reg_images(),
            models=len(models),
            model_sizes=sorted((r.num_reg_images() for r in models.values()), reverse=True),
            points=best.num_points3D(),
            reproj_px=round(float(best.compute_mean_reprojection_error()), 3),
            track_len=round(float(best.compute_mean_track_length()), 2),
        )
    else:
        m.update(registered=0, models=0, points=0)
    m["seconds"] = {
        "features": round(t_feat, 1), "matching": round(t1 - t0, 1), "mapping": round(t2 - t1, 1),
    }  # fmt: skip
    db.unlink()
    shutil.rmtree(out / "maps")
    (out / "metrics.json").write_text(json.dumps(m, indent=1))
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--configs", default="baseline,loop,ov20,ov30,global,ov20_loop,ov20_loop_global")
    a = ap.parse_args()
    print(f"pycolmap {pycolmap.__version__}, load {os.getloadavg()}")
    for run in a.runs:
        root = DATA / "eval/recon" / run
        root.mkdir(parents=True, exist_ok=True)
        t_feat = extract(run, root)
        for name in a.configs.split(","):
            m = evaluate(run, name, CONFIGS[name], t_feat, root)
            s = m["seconds"]
            print(
                f"| {run} | {name} | {m['registered']}/{m['images_in']} | {m['models']} | "
                f"{m['points']} | {m.get('reproj_px')} | {m.get('track_len')} | "
                f"{s['features']}/{s['matching']}/{s['mapping']} | {m.get('model_sizes')} |",
                flush=True,
            )


if __name__ == "__main__":
    main()
