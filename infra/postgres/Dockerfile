FROM postgis/postgis:16-3.5
# pgvector from the PGDG repo already configured in the base image.
# Not version-pinned: PGDG drops old builds, so a pin breaks rebuilds. The base image tag pins PG/PostGIS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*
COPY init/ /docker-entrypoint-initdb.d/
