# Backlog 0019 — Orthophoto: BFF COG download endpoint

- Status: ready-for-agent
- Type: backend
- Parent: orthophoto download + natural color preview feature
- Blocked by: 0017 (rgb COG in the store); 0018 (tiler export route exists for fallback)
- Invariants / decisions: workspace BFF routes are INTERNAL (not frozen - CLAUDE.md §0); new routes
  are additive. No new credentials or env var names. Auth follows the existing Bearer pattern on
  workspace routes. `S3CogStore` in `packages/rs_core/storage.py` is the only S3 access point (port
  invariant). Presigned URL TTL: 15 minutes (900 s).

## Context

An analyst clicks "Download GeoTIFF" on a pass for a given index. The response must be a file
download, not a page load. The correct pattern for authenticated MinIO access is a short-lived
presigned URL returned as a 302 redirect: the browser follows the redirect and streams the file
directly from object storage, keeping binary data off the API process.

`S3CogStore` currently has `put`, `exists`, and `delete` but no presigned-URL method. Adding
`presigned_url()` to the store is the only change needed to the storage layer.

## What to build

### 1. `presigned_url()` on `S3CogStore` (`packages/rs_core/storage.py`)

```python
def presigned_url(
    self,
    key: str,
    *,
    filename: str,
    expires: int = 900,
) -> str:
    """Return a presigned GET URL for `key` with a Content-Disposition header set to
    `attachment; filename="{filename}"`. TTL is `expires` seconds (default 15 min)."""
```

- Uses `boto3` `generate_presigned_url` with `ResponseContentDisposition` and
  `ResponseContentType: application/octet-stream`.
- Raises `KeyError` (or a domain-specific `CogNotFoundError`) if `exists(key)` is False before
  generating the URL - fail fast rather than serving a presigned URL for a missing object.

### 2. `GET /fields/{field_id}/scenes/{scene_id}/download` (workspace BFF)

Location: `services/api/workspace/fields.py` (alongside existing field routes).

Query parameters:
- `index: str` - the index name or `"rgb"` (required)
- `geometry_version: int` (required)

Handler logic:
1. Authorize: the requesting user must have access to `field_id` (reuse existing field-access check).
2. Build COG key: `cog_key(field_id=field_id, scene_id=scene_id, index=index, geometry_version=geometry_version)`.
3. Call `cog_store.presigned_url(key, filename=f"{scene_id}_{index}.tif")`.
4. Return `302` with `Location: <presigned_url>`.

If the key does not exist, return `404` with a plain JSON body `{"detail": "COG not found"}`.

Do not stream bytes through the API process. The 302 redirect is the correct pattern.

## Acceptance criteria

- [ ] `GET /fields/{fid}/scenes/{sid}/download?index=rgb&geometry_version=1` returns 302 with a
  presigned MinIO URL as `Location`.
- [ ] The presigned URL includes `Content-Disposition: attachment; filename="..."`.
- [ ] Requesting a missing COG returns 404.
- [ ] An unauthorized user (no field access) gets 403.
- [ ] The presigned URL is valid for 15 minutes.
- [ ] No binary data passes through the API process (verified by inspecting the response: no body
  beyond the redirect, `Content-Length` is 0 or absent).
- [ ] Unit test mocks `cog_store.presigned_url`; integration test validates the 302 chain.
- [ ] ruff + ruff format + mypy + pytest green.
