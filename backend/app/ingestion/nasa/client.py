"""NASA Earthdata / LAADS DAAC Client and Tile Grid calculations.

Handles tile coordinate math (Linear Lat/Lon grid for VNP46), CMR granule discovery,
URL construction, and downloading with Earthdata Bearer Token authentication.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import httpx

log = logging.getLogger(__name__)

LAADS_ARCHIVE_URL = "https://ladsweb.modaps.eosdis.nasa.gov/archive/allData"
CMR_SEARCH_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
EARTHDATA_TOKEN_ENV = "NASA_EARTHDATA_TOKEN"

# Pattern for Black Marble tile: e.g. "h30v05"
TILE_PATTERN = re.compile(r"^h(\d{2})v(\d{2})$")


@dataclass(frozen=True)
class GranuleInfo:
    """Metadata of an authentic NASA Earthdata granule discovered via CMR."""
    producer_granule_id: str
    tile: str
    period_start: str
    period_end: str
    size_mb: float
    download_url: str
    collection: str = "5200"


def tile_for_coords(lng: float, lat: float) -> str:
    """Compute the 10°x10° tile identifier (e.g. 'h30v05') containing the given coordinates.

    Formulas:
        h = floor((lon + 180) / 10), valid 0..35
        v = floor((90 - lat) / 10), valid 0..17
    """
    if not (-180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError(f"Coordinates out of WGS84 range: lng={lng}, lat={lat}")

    h = math.floor((lng + 180.0) / 10.0)
    # Special case: lng == 180 is in tile 35
    if h >= 36:
        h = 35

    v = math.floor((90.0 - lat) / 10.0)
    # Special case: lat == -90 is in tile 17
    if v >= 18:
        v = 17

    return f"h{h:02d}v{v:02d}"


def tile_bbox(tile: str) -> tuple[float, float, float, float]:
    """Return the geographic bounds (min_lng, min_lat, max_lng, max_lat) of a tile."""
    m = TILE_PATTERN.match(tile)
    if not m:
        raise ValueError(f"Invalid tile identifier format (expected 'hXXvYY'): {tile}")

    h = int(m.group(1))
    v = int(m.group(2))
    if not (0 <= h <= 35 and 0 <= v <= 17):
        raise ValueError(f"Tile indices out of range: h={h}, v={v}")

    lon_min = h * 10.0 - 180.0
    lon_max = (h + 1) * 10.0 - 180.0
    lat_max = 90.0 - v * 10.0
    lat_min = 90.0 - (v + 1) * 10.0
    return lon_min, lat_min, lon_max, lat_max


def tiles_for_bbox(bbox: Sequence[float]) -> list[str]:
    """Return all tile IDs intersecting the given bbox (min_lng, min_lat, max_lng, max_lat)."""
    min_lng, min_lat, max_lng, max_lat = bbox[:4]
    h_min = max(0, math.floor((min_lng + 180.0) / 10.0))
    h_max = min(35, math.floor((max_lng + 180.0) / 10.0))
    v_min = max(0, math.floor((90.0 - max_lat) / 10.0))
    v_max = min(17, math.floor((90.0 - min_lat) / 10.0))

    return [f"h{h:02d}v{v:02d}" for v in range(v_min, v_max + 1) for h in range(h_min, h_max + 1)]


def build_archive_url(
    product: str,
    year: int,
    day_of_year: int,
    collection: str = "5200",
    filename: str | None = None,
) -> str:
    """Build LAADS DAAC archive URL for a given product, year, and Julian day of year."""
    base = f"{LAADS_ARCHIVE_URL}/{collection}/{product}/{year}/{day_of_year:03d}"
    if filename:
        return f"{base}/{filename}"
    return base


def query_cmr_granules(
    product: str = "VNP46A3",
    tile: str = "h30v05",
    year_month: str | None = None,
    limit: int = 10,
    retries: int = 3,
    timeout: float = 30.0,
) -> list[GranuleInfo]:
    """Search NASA CMR API for real Black Marble granules matching product and tile.

    No authentication required for discovery metadata.
    """
    params = {
        "short_name": product,
        "producer_granule_id": f"*{tile}*",
        "options[producer_granule_id][pattern]": "true",
        "sort_key": "-start_date",
        "page_size": limit,
    }

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            log.debug("Querying CMR API (attempt %d/%d): %s with %s", attempt, retries, CMR_SEARCH_URL, params)
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
                res = client.get(CMR_SEARCH_URL, params=params)
                res.raise_for_status()
                data = res.json()
                break
        except Exception as exc:
            last_err = exc
            log.warning("CMR API query attempt %d failed: %s", attempt, exc)
            if attempt < retries:
                time.sleep(1.5 * attempt)
    else:
        raise RuntimeError(f"Failed to query CMR API after {retries} attempts: {last_err}") from last_err

    entries = data.get("feed", {}).get("entry", [])
    granules: list[GranuleInfo] = []

    for entry in entries:
        granule_id = entry.get("producer_granule_id", "")
        time_start = entry.get("time_start", "")
        time_end = entry.get("time_end", "")
        size_mb = float(entry.get("granule_size", 0.0))

        # Filter by year_month if requested (e.g. "2026-09")
        if year_month and not time_start.startswith(year_month):
            continue

        # Find direct HTTPS download link (excluding s3://)
        download_url: str | None = None
        for link in entry.get("links", []):
            href = link.get("href", "")
            if href.endswith(".h5") and href.startswith("https://"):
                download_url = href
                break

        if not download_url:
            continue

        granules.append(
            GranuleInfo(
                producer_granule_id=granule_id,
                tile=tile,
                period_start=time_start,
                period_end=time_end,
                size_mb=round(size_mb, 2),
                download_url=download_url,
                collection="5200",
            )
        )

    return granules


def get_latest_granule(
    product: str = "VNP46A3",
    tile: str = "h30v05",
    year_month: str | None = None,
) -> GranuleInfo | None:
    """Return the latest available NASA granule metadata for the given tile and month."""
    granules = query_cmr_granules(product=product, tile=tile, year_month=year_month, limit=10)
    return granules[0] if granules else None


def get_earthdata_token(token: str | None = None) -> str | None:
    """Get Earthdata token from parameter, environment, or backend/.env settings."""
    if token and token.strip():
        return token.strip()
    env_token = os.environ.get(EARTHDATA_TOKEN_ENV)
    if env_token and env_token.strip():
        return env_token.strip()
    try:
        from app.config import get_settings
        cfg_token = get_settings().nasa_earthdata_token
        if cfg_token and cfg_token.strip():
            return cfg_token.strip()
    except Exception:
        pass
    return None


def download_granule(
    url: str,
    output_path: str | Path,
    token: str | None = None,
    timeout: float = 300.0,
) -> Path:
    """Download Black Marble HDF5 granule using NASA Earthdata Bearer token.

    Raises:
        ValueError: If NASA_EARTHDATA_TOKEN is not configured.
        httpx.HTTPStatusError: If download fails.
    """
    auth_token = get_earthdata_token(token)
    if not auth_token:
        raise ValueError(
            f"{EARTHDATA_TOKEN_ENV} is required to download NASA Black Marble data from LAADS DAAC. "
            "Please generate a free Earthdata token at https://urs.earthdata.nasa.gov/ "
            f"and set {EARTHDATA_TOKEN_ENV} in your environment or backend/.env file."
        )

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "User-Agent": "SensoryMap-NASA-Ingestion/1.0",
    }

    log.info("Downloading Black Marble granule to %s", out_file)
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            with open(out_file, "wb") as f:
                downloaded = 0
                for chunk in response.iter_bytes(chunk_size=131072):
                    f.write(chunk)
                    downloaded += len(chunk)

    log.info("Successfully downloaded %s (%d bytes)", out_file, out_file.stat().st_size)
    return out_file
