"""Productivity / management zones (improvement plan Tier 1, T1.3).

Cluster a field's multi-temporal index stack into N zones for variable-rate application. The feature
per pixel is its temporal mean over the cloud-free observations (each pass is already SCL-masked, so
masked observations are NaN). Zones are relabeled ascending by mean, so zone 0 is the lowest and
zone k-1 the highest productivity, which is what a variable-rate controller expects.

The clustering is a small, deterministic NumPy k-means (k-means++ seeding, Lloyd iteration), so the
core is pure (no scikit-learn dependency, no network, no DB) and unit-tested with synthetic stacks.
Vectorising the zone raster to polygons needs rasterio (the `geo` extra) and is isolated behind
`zone_polygons`, in-container only."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NODATA_ZONE = -1


@dataclass(frozen=True)
class ZoneResult:
    """The zoning of one field. `labels` is the per-pixel zone id (NODATA_ZONE where the pixel had
    no usable observation); `k` is the number of zones actually formed; `zone_means` and
    `zone_pixel_counts` are indexed by zone id, ascending by productivity."""

    labels: np.ndarray
    k: int
    zone_means: list[float]
    zone_pixel_counts: list[int]
    nodata: int = NODATA_ZONE


def _kmeanspp_init(x: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ seeding: spread the initial centroids by distance, for a stable result."""
    n = x.shape[0]
    centroids = [x[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(((x[:, None, :] - np.asarray(centroids)[None, :, :]) ** 2).sum(axis=2), axis=1)
        total = d2.sum()
        probs = d2 / total if total > 0 else np.full(n, 1.0 / n)
        centroids.append(x[rng.choice(n, p=probs)])
    return np.asarray(centroids, dtype="float64")


def kmeans(
    x: np.ndarray, k: int, *, seed: int = 0, max_iter: int = 100, tol: float = 1e-6
) -> tuple[np.ndarray, np.ndarray]:
    """Cluster the rows of `x` (shape (N, F)) into `k` groups. Returns (labels (N,), centroids
    (k, F)). Deterministic for a given `seed`. Empty clusters keep their previous centroid."""
    if x.ndim != 2:
        raise ValueError(f"kmeans expects a 2-D feature matrix, got shape {x.shape}")
    if k < 1:
        raise ValueError("k must be >= 1")
    rng = np.random.default_rng(seed)
    centroids = _kmeanspp_init(x, k, rng)
    labels = np.zeros(x.shape[0], dtype=int)
    for _ in range(max_iter):
        dists = ((x[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        labels = dists.argmin(axis=1)
        new = np.stack(
            [x[labels == j].mean(axis=0) if np.any(labels == j) else centroids[j] for j in range(k)]
        )
        if np.allclose(new, centroids, atol=tol):
            centroids = new
            break
        centroids = new
    return labels, centroids


def productivity_zones(
    stack: np.ndarray, *, k: int = 3, seed: int = 0, min_valid_obs: int = 1
) -> ZoneResult:
    """Zone a field from its index stack (shape (T, H, W), NaN where a pixel was masked on a pass).

    A pixel is zoned on its temporal mean over its >= `min_valid_obs` cloud-free observations;
    pixels with too few observations are NODATA_ZONE. `k` is reduced to the number of distinct valid
    pixels when a field is tiny. Zones are returned ascending by mean (zone 0 lowest)."""
    if stack.ndim != 3:
        raise ValueError(f"productivity_zones expects a (T, H, W) stack, got shape {stack.shape}")
    _, h, w = stack.shape
    valid_counts = np.sum(~np.isnan(stack), axis=0)
    totals = np.nansum(stack, axis=0)
    mean = totals / np.maximum(valid_counts, 1)
    pixel_valid = valid_counts >= min_valid_obs

    labels = np.full((h, w), NODATA_ZONE, dtype=int)
    feat = mean[pixel_valid].reshape(-1, 1)
    m = feat.shape[0]
    if m == 0:
        return ZoneResult(labels=labels, k=0, zone_means=[], zone_pixel_counts=[])

    effective_k = max(1, min(k, m))
    if effective_k == 1:
        flat = np.zeros(m, dtype=int)
        centroids = feat.mean(axis=0, keepdims=True)
    else:
        flat, centroids = kmeans(feat, effective_k, seed=seed)

    # Relabel ascending by centroid mean so zone ids are ordered by productivity.
    order = np.argsort(centroids[:, 0])
    remap = np.empty(effective_k, dtype=int)
    remap[order] = np.arange(effective_k)
    labels[pixel_valid] = remap[flat]

    zone_means = [float(mean[labels == z].mean()) for z in range(effective_k)]
    zone_counts = [int(np.count_nonzero(labels == z)) for z in range(effective_k)]
    return ZoneResult(
        labels=labels, k=effective_k, zone_means=zone_means, zone_pixel_counts=zone_counts
    )


def zone_polygons(
    labels: np.ndarray,
    *,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
) -> list[dict]:
    """Vectorise a zone-label raster to per-zone polygons (GeoJSON geometries) for a variable-rate
    map. Needs `rasterio` (the `geo` extra), in-container only. NODATA_ZONE pixels are excluded."""
    from rasterio.features import shapes
    from rasterio.transform import Affine

    grid = labels.astype("int32")
    mask = grid != NODATA_ZONE
    out: list[dict] = []
    for geometry, value in shapes(grid, mask=mask, transform=Affine(*transform)):
        out.append({"zone": int(value), "geometry": geometry, "crs": crs})
    return out
