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
RUN pip install --no-cache-dir -e ".${INSTALL_EXTRAS}"

# Default command is overridden per-service in docker-compose.yml.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
