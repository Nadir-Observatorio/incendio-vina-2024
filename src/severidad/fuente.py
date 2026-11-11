"""Sentinel-2 L2A scenes from the Earth Search STAC catalogue (no account needed)."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, cast

from .errors import SourceError

logger = logging.getLogger(__name__)

_Ring = list[list[float]]

CATALOGUE = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
BANDS = ("nir08", "swir22", "scl")  # B8A, B12, scene classification; all at 20 m
COLOUR_BANDS = ("visual",)  # true-colour composite (TCI, 8-bit RGB, 10 m); drawn, never measured
CHUNK = 1 << 20
ATTEMPTS = 3          # tries per address before giving up
WAIT_S = 2.0          # first wait between tries; doubles each time (2 s, 4 s)
_sleep = time.sleep   # replaced in the tests


def _transient(error: OSError) -> bool:
    """Failures worth another try: no connection, time-outs and server errors (HTTP 5xx and
    429). A client error such as 404 would fail again and is reported at once."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500 or error.code == 429
    return True


def _open(url: str, timeout: float) -> HTTPResponse:
    """HTTPS response for ``url``; any other scheme is refused before connecting. Transient
    failures are retried ``ATTEMPTS`` times with a growing wait; the last failure is reported
    as ``SourceError``."""
    if not url.startswith("https://"):
        raise SourceError(f"only https addresses are used: {url}")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = urllib.request.urlopen(url, timeout=timeout)  # noqa: S310 (https checked)
            return cast(HTTPResponse, response)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            reason = getattr(error, "reason", error)
            if attempt == ATTEMPTS or not _transient(error):
                raise SourceError(f"{url}: {reason} (attempt {attempt} of {ATTEMPTS})") from error
            wait = WAIT_S * 2 ** (attempt - 1)
            logger.warning("%s: %s; trying again in %.0f s", url, reason, wait)
            _sleep(wait)
    raise SourceError(f"{url}: no attempt was made")  # pragma: no cover (ATTEMPTS >= 1)


def _json(url: str, timeout: float) -> dict[str, Any]:
    with _open(url, timeout) as r:
        data = json.loads(r.read())
    if not isinstance(data, dict):
        raise SourceError(f"{url}: expected a JSON object")
    return data


def item(scene_id: str, timeout: float = 60.0) -> dict[str, Any]:
    """STAC item of one scene."""
    return _json(f"{CATALOGUE}/collections/{COLLECTION}/items/{scene_id}", timeout)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def download_scene(scene_id: str, folder: Path, timeout: float = 600.0,
                   bands: Sequence[str] = BANDS) -> dict[str, Path]:
    """Download the requested bands (catalogue asset names) of a scene to
    ``folder/<scene_id>/<band>.tif``. By default, the three bands the study uses.

    Existing files are kept (never overwritten). A partial download is left as ``.part`` and
    raises, so it is never read as a complete band."""
    bands = tuple(bands)
    if not bands or len(set(bands)) != len(bands):
        raise SourceError(f"bands must be a non-empty list without repeats: {list(bands)}")
    target = Path(folder) / scene_id
    target.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    missing = [b for b in bands if not (target / f"{b}.tif").exists()]
    assets: dict[str, Any] = item(scene_id)["assets"] if missing else {}
    unknown = [b for b in missing if b not in assets]
    if unknown:
        raise SourceError(f"{scene_id}: no asset {unknown} in the catalogue item "
                          f"(available: {sorted(assets)})")
    for band in bands:
        path = target / f"{band}.tif"
        paths[band] = path
        if path.exists():
            logger.info("%s %s already present (%s)", scene_id, band, sha256(path)[:12])
            continue
        part = path.with_suffix(".tif.part")
        written = 0
        with _open(str(assets[band]["href"]), timeout) as r, part.open("wb") as f:
            expected = r.headers.get("Content-Length")
            try:
                for block in iter(lambda: r.read(CHUNK), b""):
                    f.write(block)
                    written += len(block)
            except (TimeoutError, ConnectionError) as error:
                raise SourceError(f"{band}: connection lost after {written} bytes ({error}); "
                                  f"{part} kept, run again to download it whole") from error
        if expected is not None and int(expected) != written:
            raise SourceError(f"{band}: received {written} of {expected} bytes; {part} kept")
        part.replace(path)
        logger.info("%s %s: %d bytes, sha256 %s", scene_id, band, written, sha256(path)[:12])
    return paths


MUNICIPALITIES = ("https://rest-sit.mop.gob.cl/arcgis/rest/services/INTEROP/SERVICIO_DPA/"
                  "MapServer/1/query")


def esri_to_geojson(geometry: dict[str, Any]) -> dict[str, Any]:
    """Esri JSON polygon (rings) to a GeoJSON MultiPolygon. Esri outer rings are clockwise and
    holes counter-clockwise; each hole is attached to the outer ring that contains it."""
    from shapely.geometry import LinearRing, Point, Polygon, mapping
    from shapely.geometry import MultiPolygon

    outers: list[_Ring] = []
    holes: list[_Ring] = []
    for ring in geometry.get("rings", []):
        if len(ring) < 4:
            continue
        (holes if LinearRing(ring).is_ccw else outers).append(ring)
    assigned: dict[int, list[_Ring]] = {i: [] for i in range(len(outers))}
    shells = [Polygon(o) for o in outers]
    for h in holes:
        probe = Point(h[0])
        for i, s in enumerate(shells):
            if s.contains(probe):
                assigned[i].append(h)
                break
    result: dict[str, Any] = mapping(
        MultiPolygon([Polygon(o, assigned[i]) for i, o in enumerate(outers)]))
    return result


def municipalities(codes: dict[str, str], timeout: float = 120.0) -> dict[str, Any]:
    """GeoJSON FeatureCollection (EPSG:4326) of the requested Chilean municipalities (CUT codes)
    from the public MOP service. Uses Esri JSON, which this service supports."""
    import urllib.parse

    where = "CUT_COM IN (" + ",".join(f"'{c}'" for c in codes) + ")"
    query = urllib.parse.urlencode({"where": where, "outFields": "CUT_COM,COMUNA",
                                    "returnGeometry": "true", "outSR": 4326, "f": "json"})
    data = _json(f"{MUNICIPALITIES}?{query}", timeout)
    if "error" in data:   # ArcGIS reports failures with HTTP 200 and an "error" member
        raise SourceError(f"municipality service error: {data['error']}")
    features: list[dict[str, Any]] = [{"type": "Feature",
                 "properties": {"CUT_COM": str(f["attributes"]["CUT_COM"]).zfill(5),
                                "COMUNA": f["attributes"]["COMUNA"]},
                 "geometry": esri_to_geojson(f["geometry"])} for f in data.get("features", [])]
    missing = set(codes) - {f["properties"]["CUT_COM"] for f in features}
    if missing:
        raise SourceError(f"municipalities not returned by the service: {sorted(missing)}")
    return {"type": "FeatureCollection", "features": features}
