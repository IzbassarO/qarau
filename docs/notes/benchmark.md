# Benchmark

One row per (video, pipeline version). Newest version last. Full benchmark set and method:
Phase 1.1 (pending the MVP plan text). Registered = frames in the largest model.

| Video | Pipeline | Frames used | Registered | Models | Points | Reproj. px | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| room (IMG_8315.MOV, iPhone 17 HDR portrait, 47 s) | M1 (2.5 fps, sequential) | 111 | 14 | 4 | 869 | 0.98 | 2.0 min |
| room | 11cc97c (6 fps, exhaustive ≤ 300, HDR→SDR) | 269 | 244 | 3 | 25 997 | 0.93 | 5.4 min |
| notre-dame (drone, 20 s, 640×480) | M1 (2.5 fps, sequential) | 54 | 52 | 1 | 15 239 | 0.37 | 10 s |
| notre-dame | 11cc97c | 109 | 109 | 1 | 43 317 | 0.40 | 2.8 min |

## Automated benchmark (P1)

Rows appended by `python -m qarau_pipeline benchmark --record` (method: `docs/notes/benchmark-method.md`). `+dirty` = uncommitted changes on top of the commit.

| Video | Commit | Frames used | Registered % | Models | Points | Reproj. px | Scale err cm | Time s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aegviidu | a8efcc9+dirty | 150 | 88.0 | 3 | 55191 | 0.525 |  | 1322.2 | max_frames 150 |
| mykolaiv | a8efcc9+dirty | 210 | 84.8 | 3 | 13384 | 0.739 |  | 983.9 |  |
| notre-dame | a8efcc9+dirty | 109 | 100.0 | 1 | 43264 | 0.395 |  | 184.4 |  |
| orono | a8efcc9+dirty | 150 | 100.0 | 1 | 91847 | 0.568 |  | 1235.5 | max_frames 150 |
| room | a8efcc9+dirty | 269 | 95.5 | 2 | 26706 | 0.93 |  | 464.9 |  |
| notre-dame | 2ccf7f2+dirty | 109 | 100.0 | 1 | 43170 | 0.394 |  | 133.6 |  |
| room | 2ccf7f2+dirty | 269 | 100.0 | 1 | 26482 | 0.936 |  | 348.3 |  |

| Video | Commit | Frames used | Registered % | Models | Points | Points total | Low parallax | Reproj. drop | Reproj. px | Scale err cm | Time s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aegviidu | 4087789+dirty | 150 | 100.0 | 1 | 77545 | 84915 | 905 | 6465 | 0.661 |  | 1526.5 | max_frames 150 |
| notre-dame | 4087789+dirty | 109 | 100.0 | 1 | 43298 | 43298 | 0 | 0 | 0.395 |  | 141.3 |  |
| aegviidu | 4087789+dirty | 150 | 100.0 | 1 | 60728 | 61821 | 1093 | 0 | 0.532 |  | 1722.4 | max_frames 150; same code, re-run (`run`, not `--record`): GLOMAP join variance, ADR 0013 |
