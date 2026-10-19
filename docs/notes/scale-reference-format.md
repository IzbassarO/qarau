# Scale reference file (version 1)

How a user tells the pipeline the real size of something in the video: an A4 sheet lying in
the scene, or one distance they measured (e.g. with a tape). The file is read by
`qarau_pipeline run --scale-ref FILE` and `qarau_pipeline scale --run RUN --ref FILE`
(ADR 0018). The web marking UI that will write it is a later task.

```json
{
  "version": 1,
  "kind": "a4",
  "points": [
    {"id": "c1", "observations": [{"frame": "000012.jpg", "x": 812.5, "y": 640.0},
                                  {"frame": "000030.jpg", "x": 701.0, "y": 655.5}]},
    {"id": "c2", "observations": [...]},
    {"id": "c3", "observations": [...]},
    {"id": "c4", "observations": [...]}
  ]
}
```

```json
{"version": 1, "kind": "distance", "distance_m": 2.35,
 "points": [{"id": "a", "observations": [...]}, {"id": "b", "observations": [...]}]}
```

| Field | Rule |
| --- | --- |
| `version` | `1` |
| `kind` | `"distance"` (exactly 2 points) or `"a4"` (exactly 4 points: the sheet's corners in order around it, either direction, starting at any corner) |
| `distance_m` | required for `distance`: the true distance between the two points in metres, finite and > 0. Not allowed for `a4` |
| `points[].id` | any label, used in error messages |
| `points[].observations` | the same physical point marked in at least 2 **different** frames; more views = better |
| `frame` | frame file name as in the run's `frames.json` (`000123.jpg`); the frame must be registered (have a camera pose) |
| `x`, `y` | pixels in the extracted, rotation-corrected frame (`<run>/frames/…`, not the source video), origin at the top-left corner of the image, x right, y down; finite and inside the image |

Errors are refused with a message that names the field: unknown frame, unregistered frame,
fewer than 2 frames for a point, non-finite or non-numeric coordinates, a wrong point count,
a point outside the image, or marks that triangulate behind a camera.

What the pipeline does with it: each point is triangulated (multi-view DLT) from the marked
pixels and the camera poses of `colmap/sparse/0`, then
- `distance`: scale = `distance_m` / |p1 − p2|; no error estimate (`scale_error_cm` = null);
- `a4`: least-squares scale over the 4 edges (0.210 / 0.297 m) and 2 diagonals (0.3638 m);
  `scale_error_cm` = RMS of the 6 scaled lengths minus the true ones, in cm. A large value
  means the corners were marked badly or out of order.

The run keeps a copy as `<run>/scale_ref.json`; a later `export` of that run reuses it.
Tips for users: lay the sheet flat, film it from 2–3 directions, mark the corners where the
paper edges meet.
