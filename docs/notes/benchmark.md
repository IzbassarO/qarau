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

| Video | Commit | Frames used | Registered % | Models | Points | Reproj. px | Scale err cm | Time s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mykolaiv | 667d6ee+dirty | 210 | 84.8 | 3 | 10845 | 0.638 |  | 645.2 |
| notre-dame | 667d6ee+dirty | 109 | 100.0 | 1 | 43269 | 0.395 |  | 148.9 |
| room | 667d6ee+dirty | 269 | 95.5 | 3 | 26688 | 0.931 |  | 395.3 |
| aegviidu | 667d6ee+dirty | failed: timeout after 1800 s | | | | | | |
| orono | 667d6ee+dirty | failed: timeout after 1800 s | | | | | | |
