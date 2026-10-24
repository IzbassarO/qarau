# D1 — S3 listing of a notre-dame S1 run in the local Versity bucket

Date: 2026-10-23. `python -m qarau_pipeline run --video data/samples/notre_dame_oubiels.webm --out
<cache>/notre-dame` with `QARAU_S3_BUCKET=qarau`, `QARAU_S3_ENDPOINT=http://localhost:7070` (the
compose `s3` service, Versity S3 Gateway v1.9.0, ADR 0006), then `step dense --run <cache>/notre-dame`
with the fake dense provider (free, synthetic points; `config.yaml` ships with dense off). The run
is not in the catalog, so its prefix is `_local/_local/notre-dame`.

Listing (size in bytes, key), the 182 `frames/0*.jpg`, 182 `bundle/web/thumbs/*.jpg` and 139
`sparse/masks/*.png` collapsed to one line each:

```
     2907384  _local/_local/notre-dame/video/notre_dame_oubiels.webm

     ... x182  _local/_local/notre-dame/frames/000000.jpg … 000181.jpg
       46152  _local/_local/notre-dame/frames/frames.json
        9856  _local/_local/notre-dame/frames/manifest.json
        1644  _local/_local/notre-dame/frames/quality_report.json
        1212  _local/_local/notre-dame/frames/step.json

   122826752  _local/_local/notre-dame/sparse/database.db
     ... x139  _local/_local/notre-dame/sparse/masks/000000.jpg.png …
          64  _local/_local/notre-dame/sparse/sparse/0/cameras.bin
       10172  _local/_local/notre-dame/sparse/sparse/0/frames.bin
    16558627  _local/_local/notre-dame/sparse/sparse/0/images.bin
     5308627  _local/_local/notre-dame/sparse/sparse/0/points3D.bin
          24  _local/_local/notre-dame/sparse/sparse/0/rigs.bin
         902  _local/_local/notre-dame/sparse/step.json

     5399543  _local/_local/notre-dame/dense/fused.ply
     6264696  _local/_local/notre-dame/dense/fused.ply.vis
          28  _local/_local/notre-dame/dense/gpu_ledger.fake.json
         792  _local/_local/notre-dame/dense/step.json

      297609  _local/_local/notre-dame/bundle/cloud.laz
      643634  _local/_local/notre-dame/bundle/cloud.ply
         664  _local/_local/notre-dame/bundle/step.json
      514794  _local/_local/notre-dame/bundle/web/dense.color.u8
     3441508  _local/_local/notre-dame/bundle/web/dense.frame_ids.u32
      686396  _local/_local/notre-dame/bundle/web/dense.frame_offsets.u32
     2059176  _local/_local/notre-dame/bundle/web/dense.position.f32
       21288  _local/_local/notre-dame/bundle/web/far.color.u8
       85152  _local/_local/notre-dame/bundle/web/far.position.f32
       88518  _local/_local/notre-dame/bundle/web/manifest.json
      128691  _local/_local/notre-dame/bundle/web/points.color.u8
       85794  _local/_local/notre-dame/bundle/web/points.first_seen.u16
       42897  _local/_local/notre-dame/bundle/web/points.num_obs.u8
      514764  _local/_local/notre-dame/bundle/web/points.position.f32
     ... x182  _local/_local/notre-dame/bundle/web/thumbs/000000.jpg …
         732  _local/_local/notre-dame/bundle/web/tracks.frame_offsets.u32
     1070828  _local/_local/notre-dame/bundle/web/tracks.frame_points.u32
      535414  _local/_local/notre-dame/bundle/web/tracks.point_frames.u16
      171592  _local/_local/notre-dame/bundle/web/tracks.point_offsets.u32

537 objects, 197.1 MB
```

The pipeline log of the run shows the pushes in step order:

```
storage: pushed _local/_local/notre-dame/video/notre_dame_oubiels.webm
storage: pushed 186 objects to _local/_local/notre-dame/frames/
storage: pushed 146 objects to _local/_local/notre-dame/sparse/
storage: pushed 196 objects to _local/_local/notre-dame/bundle/
storage: pushed 4 objects to _local/_local/notre-dame/dense/        (step dense)
storage: pushed 200 objects to _local/_local/notre-dame/bundle/      (re-exported with the dense layer)
```

`probe/`, `run.json`, `log.txt` and `workflow.json` are not stored by design (ADR 0024). The
`database.db` (123 MB) is the bulk of the prefix; a lifecycle rule for `sparse/` is M5 work.

## After `rm -rf <cache>/notre-dame`

The API (`backend/app`, in-process through the ASGI app, a migrated SQLite catalog, storage on,
`QARAU_FILES_MODE=stream`) with the cache dir gone:

```
cache dir exists: False
manifest.json: 200 application/json
  version 2 points 42897 frames 182
files/export/web/points.position.f32: 200 application/octet-stream 514764 bytes
files/frames/000000.jpg: 200 image/jpeg 91963
unauthenticated: 401
traversal: 404
```

and with `QARAU_FILES_MODE=redirect` (signature shortened):

```
manifest.json: 302 http://localhost:7070/qarau/_local/_local/notre-dame/bundle/web/manifest.json?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=qarau_dev_access%2F20261024%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20261024T031458Z&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature=18b9…f430
files/export/web/points.position.f32: 302 http://localhost:7070/qarau/_local/_local/notre-dame/bundle/web/points.position.f32?X-Amz-Algorithm=AWS4-HMAC-SHA256&…
files/frames/000000.jpg: 302
unauthenticated: 401
traversal: 404
```

A signed URL fetched with plain `urllib` from Versity: `200`, 88518 bytes, manifest version 2,
42897 points; the same URL without its query string: `403`.
