# Phase 0 base image: boots the API and worker against the mock imagery adapter.
# Geospatial system libs (GDAL) are NOT needed yet - rasterio is an optional extra
# pulled in for the analysis engine / windowed_cog adapter in a later stage.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml ./
COPY packages ./packages
COPY services ./services

RUN pip install --no-cache-dir -e .

# Default command is overridden per-service in docker-compose.yml.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
