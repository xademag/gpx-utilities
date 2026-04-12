import os
from core.models import GPXPoint, GPXTrack


_SEMI = 180.0 / (2 ** 31)   # FIT semicircles → decimal degrees


def parse_fit(path: str) -> list[GPXTrack]:
    """Parse a FIT file and return a list of GPXTrack objects.

    Requires the `fitparse` package (pip install fitparse).
    Each FIT session becomes one GPXTrack.  When there is only one session
    (the typical case for a single activity file) the result is a list with
    one element — matching the behaviour of parse_gpx().
    """
    try:
        from fitparse import FitFile
    except ImportError:
        raise RuntimeError("fitparse is not installed. Run: pip install fitparse")

    ff = FitFile(path)

    # ── Try to derive a human-readable name ───────────────────────────────────
    # Prefer the activity sport field; fall back to the bare filename.
    name = None
    for msg in ff.get_messages(['session', 'sport']):
        for field in msg:
            if field.name == 'sport' and field.value:
                name = str(field.value).replace('_', ' ').title()
                break
        if name:
            break
    if not name:
        name = os.path.splitext(os.path.basename(path))[0]

    # ── Collect track points ───────────────────────────────────────────────────
    points = []
    for record in ff.get_messages('record'):
        data = {f.name: f.value for f in record}

        lat_sc = data.get('position_lat')
        lon_sc = data.get('position_long')
        if lat_sc is None or lon_sc is None:
            continue

        lat = lat_sc * _SEMI
        lon = lon_sc * _SEMI

        # Prefer enhanced_altitude (higher precision) when available
        ele = data.get('enhanced_altitude') or data.get('altitude')

        ts = data.get('timestamp')   # fitparse returns a naive datetime (UTC)
        time_str = ts.strftime('%Y-%m-%dT%H:%M:%SZ') if ts is not None else None

        points.append(GPXPoint(lat=lat, lon=lon, ele=ele, time=time_str))

    if not points:
        return []

    return [GPXTrack(name=name, points=points)]
