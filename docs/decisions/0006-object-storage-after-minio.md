# 0006 — Object storage: S3 API only; Versity S3 Gateway replaces MinIO for dev and on-prem

- Status: accepted for development; on-prem choice to be confirmed by the owner
- Date: 2026-10-14

## Context

`CLAUDE.md` named MinIO for local and on-prem storage. In 2025 MinIO stopped publishing
binaries and container images for the community edition and marked the repository as no
longer maintained; `minio/minio` is absent from Docker Hub and Quay. Building it from
source (AGPL) is possible but means shipping an unmaintained AGPL component to customers.

## Decision

- Application code talks to storage only through the S3 API (boto3 / S3 client,
  endpoint URL from config). No vendor-specific features.
- Development and the default on-prem shape use **Versity S3 Gateway**
  (`versity/versitygw`, Apache-2.0, Go, single container, objects stored as plain files on
  a POSIX directory — easy to back up and inspect). Cloud SaaS uses the provider's S3.
- Alternatives kept in view: RustFS (Apache-2.0, MinIO-compatible, 1.0 in 2026),
  SeaweedFS (Apache-2.0), Garage (AGPL).

## Consequences

- `docker compose up` needs no MinIO; `CLAUDE.md` and `docs/architecture.md` are updated.
- Open question for the owner before M5: confirm Versity for customer installs or pick
  another Apache-licensed server. Switching costs one compose service and one env var.
