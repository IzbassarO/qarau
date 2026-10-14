# Base: pgvector (multi-arch: amd64 + arm64). postgis/postgis has no arm64 image, which breaks
# Apple Silicon dev machines, so PostGIS is installed from the PGDG apt repo instead.
# PostGIS is not version-pinned (PGDG drops old builds, a pin breaks rebuilds); PG and pgvector are.
FROM pgvector/pgvector:0.8.0-pg16
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
COPY init/ /docker-entrypoint-initdb.d/
