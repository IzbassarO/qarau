"""segment stage: Segmenter implementations + the stage that writes `<run>/segmentation/`.

Contract: docs/notes/m2-plan.md section 2 and 3, ADR 0009.

- `ManualMasks`      masks from files (tests, hand-drawn or dataset masks).
- `GroundedSam2`     local open-vocabulary segmenter, DEVELOPMENT ONLY (Grounding DINO + SAM 2.1,
                     both Apache-2.0). Needs `uv sync --group ml`; torch is imported lazily.
- `SamApiSegmenter`  SAM 3.1 through the Meta Model API: request builder + response parser +
                     injected transport. The default transport makes no network call.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .interfaces import MaskResult, StageError, run_stage

Transport = Callable[[dict[str, Any]], dict[str, Any]]


def parse_prompt(s: str) -> dict[str, str]:
    """'rust:corrosion:watch' -> {text, class, severity}; class=text, severity=watch by default."""
    text, cls, sev = (s.split(":") + [""] * 3)[:3]
    if sev not in ("", "critical", "watch"):
        raise StageError(f"severity must be 'critical' or 'watch', got '{sev}' in prompt '{s}'")
    return {"text": text.strip(), "class": cls.strip() or text.strip(), "severity": sev or "watch"}


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _result(prompt: str, mask: np.ndarray, conf: float) -> MaskResult | None:
    mask = mask.astype(bool)
    if not mask.any():
        return None
    return MaskResult(prompt, mask, float(min(max(conf, 0.0), 1.0)), _bbox(mask))


# ---------------------------------------------------------------- ManualMasks


class ManualMasks:
    """Masks from a directory. Two layouts:

    1. The segment stage's own: `observations.json` + `masks/<frame:06d>_<k:02d>.png`
       (255 = mask, frame resolution). Prompts and confidences come from the JSON.
    2. Simple: `<frame:06d>_<k:02d>[_<prompt>].png` (png anywhere in the directory or in its
       `masks/` subdirectory). Confidence 1.0; a file without a prompt suffix belongs to the
       first requested prompt.
    """

    name = "manual"

    def __init__(self, root: Path | str):
        self.root = Path(root)
        obs_file = self.root / "observations.json"
        self._items: dict[int, list[tuple[str | None, Path, float]]] = {}
        if obs_file.exists():
            for o in json.loads(obs_file.read_text())["observations"]:
                name = Path(o["mask"]).name  # stored path is relative to the run directory
                cands = [self.root.parent / o["mask"], self.root / "masks" / name, self.root / name]
                p = next((c for c in cands if c.exists()), cands[-1])
                self._items.setdefault(o["frame"], []).append((o["prompt"], p, o["confidence"]))
        else:
            files = sorted(self.root.glob("*.png")) + sorted(self.root.glob("masks/*.png"))
            for f in files:
                parts = f.stem.split("_", 2)
                if len(parts) < 2 or not parts[0].isdigit():
                    raise StageError(f"manual mask '{f.name}' is not <frame>_<k>[_<prompt>].png")
                prompt = parts[2].replace("-", " ") if len(parts) == 3 else None
                self._items.setdefault(int(parts[0]), []).append((prompt, f, 1.0))

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "version": "1", "licence": "n/a (user-provided masks)",
                "dev_only": False}  # fmt: skip

    def segment(self, image: np.ndarray, prompts: list[str], frame: int) -> list[MaskResult]:
        out = []
        for prompt, path, conf in self._items.get(frame, []):
            prompt = prompt or prompts[0]
            if prompt not in prompts:
                continue
            m = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if m is None:
                raise StageError(f"cannot read mask {path}")
            if m.shape != image.shape[:2]:
                raise StageError(f"mask {path.name} is {m.shape}, frame is {image.shape[:2]}")
            r = _result(prompt, m > 127, conf)
            if r:
                out.append(r)
        return out


# ---------------------------------------------------------------- local model (dev only)


class GroundedSam2:
    """Grounding DINO (text -> boxes) + SAM 2.1 (box -> mask), via transformers.

    Licences (checked on the HF model cards, 2026-10): IDEA-Research/grounding-dino-tiny
    Apache-2.0, facebook/sam2.1-hiera-tiny Apache-2.0. Code in transformers: Apache-2.0.
    Not a product path: quality on thin defects (cracks, rust films) is limited; the
    product segmenter is SAM 3.1. Confidence = Grounding DINO box score.
    """

    name = "grounded-sam2"
    DINO = "IDEA-Research/grounding-dino-tiny"
    SAM = "facebook/sam2.1-hiera-tiny"

    def __init__(self, box_threshold: float = 0.3, text_threshold: float = 0.25,
                 max_area_frac: float = 0.8, device: str | None = None):  # fmt: skip
        self.box_threshold, self.text_threshold = box_threshold, text_threshold
        self.max_area_frac = max_area_frac  # drop boxes that are "the whole image"
        self.device = device or os.environ.get("QARAU_DEVICE")
        self._m: Any = None

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "version": f"{self.DINO} + {self.SAM}",
                "licence": "Apache-2.0 (code and weights)", "dev_only": True,
                "box_threshold": self.box_threshold}  # fmt: skip

    def _load(self) -> Any:
        if self._m is None:
            import torch
            from transformers import (
                AutoModelForZeroShotObjectDetection,
                AutoProcessor,
                Sam2Model,
                Sam2Processor,
            )

            dev = self.device or ("mps" if torch.backends.mps.is_available() else "cpu")
            self._m = (
                dev,
                AutoProcessor.from_pretrained(self.DINO),
                AutoModelForZeroShotObjectDetection.from_pretrained(self.DINO).to(dev).eval(),
                Sam2Processor.from_pretrained(self.SAM),
                Sam2Model.from_pretrained(self.SAM).to(dev).eval(),  # type: ignore[arg-type]
            )
        return self._m

    def segment(self, image: np.ndarray, prompts: list[str], frame: int) -> list[MaskResult]:
        import torch
        from PIL import Image

        dev, dproc, dino, sproc, sam = self._load()
        h, w = image.shape[:2]
        pil = Image.fromarray(image)
        out: list[MaskResult] = []
        for prompt in prompts:
            inp = dproc(images=pil, text=prompt.lower().rstrip(".") + ".", return_tensors="pt")
            with torch.no_grad():
                det = dino(**{k: v.to(dev) for k, v in inp.items()})
            res = dproc.post_process_grounded_object_detection(
                det, inp["input_ids"], threshold=self.box_threshold,
                text_threshold=self.text_threshold, target_sizes=[(h, w)],
            )[0]  # fmt: skip
            for box, score in zip(res["boxes"].tolist(), res["scores"].tolist(), strict=True):
                if (box[2] - box[0]) * (box[3] - box[1]) > self.max_area_frac * w * h:
                    continue
                si = sproc(images=pil, input_boxes=[[box]], return_tensors="pt")
                with torch.no_grad():
                    so = sam(**{k: v.to(dev) for k, v in si.items() if k != "original_sizes"},
                             multimask_output=False)  # fmt: skip
                m = sproc.post_process_masks(so.pred_masks.cpu(), si["original_sizes"])[0]
                r = _result(prompt, m[0, 0].numpy() > 0, score)
                if r:
                    out.append(r)
        return out


# ---------------------------------------------------------------- SAM 3.1 via API (stub)

_NO_TRANSPORT = (
    "SamApiSegmenter has no transport yet. Filling it in (hackathon 2026-10-26) needs the Meta "
    "Model API: set META_MODEL_API_KEY (and META_MODEL_API_URL if it is not the default) and "
    "pass a transport(request: dict) -> dict, or implement default_transport()."
)


def default_transport(request: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(_NO_TRANSPORT)


class SamApiSegmenter:
    """SAM 3.1 (promptable concept segmentation) through the Meta Model API.

    SAM 3 takes a short noun phrase and returns EVERY instance of it: per instance a mask, a
    box and a score. Public SAM 3 release: instance masks + scores, no per-pixel logits needed.

    ASSUMED wire format (to verify against the real API on 2026-10-26; only `build_request`
    and `parse_response` change):
      request  {"model", "image": {"format": "jpeg", "data": <base64>}, "prompts": [{"text"}]}
      response {"results": [{"prompt": str, "instances": [{"score": float, "box": [x0,y0,x1,y1],
                "mask": {"encoding": "png_base64", "data": ...}
                       | {"encoding": "rle", "size": [h, w], "counts": [int, ...]}}]}]}
    RLE is the uncompressed COCO form (column-major, alternating 0/1 run lengths starting
    with 0); COCO compressed strings are not decoded yet.
    """

    name = "sam3.1-api"

    def __init__(self, transport: Transport | None = None, model: str = "sam-3.1"):
        self.transport = transport or default_transport
        self.model = model

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.model,
                "licence": "SAM License (Meta); commercial terms pending owner decision",
                "dev_only": False}  # fmt: skip

    def build_request(self, image: np.ndarray, prompts: list[str]) -> dict[str, Any]:
        ok, buf = cv2.imencode(".jpg", image[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise StageError("cannot encode frame as JPEG")
        return {"model": self.model,
                "image": {"format": "jpeg", "data": base64.b64encode(buf.tobytes()).decode()},
                "prompts": [{"text": p} for p in prompts]}  # fmt: skip

    @staticmethod
    def decode_mask(m: dict[str, Any], shape: tuple[int, int]) -> np.ndarray:
        if m["encoding"] == "png_base64":
            arr = cv2.imdecode(np.frombuffer(base64.b64decode(m["data"]), np.uint8), 0)
            if arr is None:
                raise StageError("SAM API returned an undecodable PNG mask")
            mask = arr > 127
        elif m["encoding"] == "rle" and isinstance(m["counts"], list):
            h, w = m["size"]
            flat = np.repeat(np.arange(len(m["counts"])) % 2 == 1, m["counts"])
            mask = flat.reshape(w, h).T
        else:
            raise StageError(f"unsupported SAM API mask encoding '{m.get('encoding')}'")
        if mask.shape != shape:
            raise StageError(f"SAM API mask is {mask.shape}, frame is {shape}")
        return mask

    def parse_response(
        self, resp: dict[str, Any], shape: tuple[int, int], prompts: list[str]
    ) -> list[MaskResult]:
        out = []
        for res in resp["results"]:
            if res["prompt"] not in prompts:
                raise StageError(f"SAM API answered for unknown prompt '{res['prompt']}'")
            for inst in res["instances"]:
                r = _result(res["prompt"], self.decode_mask(inst["mask"], shape), inst["score"])
                if r:
                    out.append(r)
        return out

    def segment(self, image: np.ndarray, prompts: list[str], frame: int) -> list[MaskResult]:
        resp = self.transport(self.build_request(image, prompts))
        return self.parse_response(resp, image.shape[:2], prompts)


# ---------------------------------------------------------------- stage


def select_frames(manifest: dict[str, Any], which: str, stride: int) -> list[int]:
    fr = manifest["frames"]
    idx = [f["i"] for f in fr if (f["registered"] if which != "all" else f["kept"])]
    return idx[::stride] if which == "stride" else idx


def run_segment(
    run: Path, seg: Any, prompts: list[dict[str, str]], which: str = "registered", stride: int = 5
) -> dict[str, Any]:
    """Segment the chosen frames of a bundle; write segmentation/ (idempotent)."""
    manifest = json.loads((run / "web/manifest.json").read_text())
    out = run / "segmentation"
    shutil.rmtree(out, ignore_errors=True)
    (out / "masks").mkdir(parents=True)
    texts = [p["text"] for p in prompts]
    obs: list[dict[str, Any]] = []
    frames = select_frames(manifest, which, stride)
    for i in frames:
        bgr = cv2.imread(str(run / manifest["frames"][i]["image"]))
        if bgr is None:
            raise StageError(f"cannot read frame {i}")
        for k, r in enumerate(seg.segment(bgr[:, :, ::-1].copy(), texts, i)):
            rel = f"segmentation/masks/{i:06d}_{k:02d}.png"
            cv2.imwrite(str(run / rel), r.mask.astype(np.uint8) * 255)
            obs.append({"frame": i, "prompt": r.prompt, "mask": rel,
                        "confidence": round(r.confidence, 4), "bbox": list(r.bbox)})  # fmt: skip
    (out / "observations.json").write_text(
        json.dumps(
            {"segmenter": seg.info(), "prompts": prompts, "frames": frames, "observations": obs},
            indent=1,
        )  # fmt: skip
    )
    by_prompt = {t: sum(1 for o in obs if o["prompt"] == t) for t in texts}
    return {"frames": len(frames), "observations": len(obs), "per_prompt": by_prompt}


def segment_stage(
    run: Path, seg: Any, prompts: list[dict[str, str]], which: str, stride: int
) -> dict[str, Any]:
    info = seg.info()
    params = {"segmenter": info, "prompts": prompts, "frames": which, "stride": stride}
    res = run_stage(run, "segment", f"{info['name']} {info['version']}", params,
                    lambda: run_segment(run, seg, prompts, which, stride))  # fmt: skip
    return res.metrics
