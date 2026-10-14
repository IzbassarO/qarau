# Roadmap to pilot-ready (Oct 2026 → spring 2028)

Budget rule: $0–30/month until the first paying pilot. Local machine + free GPU
(Kaggle, Colab, university cluster if available) + hackathon credits.

| Period | Milestone | Outcome | Gate to the next |
| --- | --- | --- | --- |
| Oct 2026 | M0 | Repo, docs, docker compose (Postgres + MinIO), API health check | Dev environment runs on a clean machine |
| Oct–Dec 2026 | M1 | Phone video → COLMAP point cloud → web viewer (black background, height-coloured points) | Works on 3 real videos |
| Nov 2026 | Hackathon | Quick Scan demo with SAM 3.1 defects on the map | Demo works on someone else's video |
| Jan–Mar 2027 | M2–M3 | Defects in 3D with evidence panel, coverage states, quality check, measurement, PDF report, decision log | An engineer can review an inspection without help |
| Apr–Jun 2027 | Discovery | 10–15 remote interviews with engineers and drone contractors in Kazakhstan; test on their real footage | 2+ say "we would pilot this" |
| Jul–Dec 2027 | M4 | Monitoring mode (baseline, registration, change detection), Tauri desktop, mobile capture app | Change detection reliable on repeated scans |
| Jan–Apr 2028 | M5 | One-command on-prem install, roles/SSO, audit log, subscription billing | Signed pilot agreement |

What is deliberately postponed: individual (consumer) users, construction and residential
verticals, model fine-tuning (needs the decision log from real use first), Kubernetes.
