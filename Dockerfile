# Base image: boots the API, worker, and Alembic migrations. Phase 1 adds the PostGIS data
# model; its deps (sqlalchemy/geoalchemy2/alembic/psycopg/shapely/pyproj) are pure wheels in
# the base install. Heavy raster system libs (GDAL) are still NOT needed - rasterio is an
# optional extra pulled in for the analysis engine / windowed_cog adapter in a later stage.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY packages ./packages
COPY services ./services
COPY alembic ./alembic

RUN pip install --no-cache-dir -e .

# Default command is overridden per-service in docker-compose.yml.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
