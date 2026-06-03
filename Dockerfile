# Base image: boots the API, worker, and Alembic migrations. Phase 1 adds the PostGIS data
# model; its deps (sqlalchemy/geoalchemy2/alembic/psycopg/shapely/pyproj) are pure wheels in
# the base install. The heavy raster libs (rasterio/rio-tiler, the `geo` extra) install only
# where they are needed, selected per service by the INSTALL_EXTRAS build arg below.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY packages ./packages
COPY services ./services
COPY alembic ./alembic

# Per-service extras: the tiler builds with INSTALL_EXTRAS="[geo]" (rasterio/rio-tiler) and a
# COG-emitting worker with "[geo,storage]"; api/worker/migrate keep the empty default. An empty
# value resolves to ".", a populated one to ".[geo]" etc.
ARG INSTALL_EXTRAS=""

# The geo extra (rasterio/rio-tiler) links GDAL, which needs system libraries that python:3.11-slim
# omits. Install them only for geo builds so non-geo images stay lean. Without libexpat1
# (libexpat.so.1) `import rasterio` fails at runtime, so the tiler 503s on every tile and the
# COG-emitting worker cannot write rasters.
RUN if echo "$INSTALL_EXTRAS" | grep -q geo; then \
        apt-get update && \
        apt-get install -y --no-install-recommends libexpat1 && \
        rm -rf /var/lib/apt/lists/*; \
    fi

RUN pip install --no-cache-dir -e ".${INSTALL_EXTRAS}"

# Default command is overridden per-service in docker-compose.yml.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
