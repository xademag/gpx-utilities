import math
import xml.etree.ElementTree as ET
from core.models import GPXPoint, GPXTrack


def write_gpx(pts: list[dict], path: str, split_idx: int = -1) -> None:
    """Write a list of point dicts to a GPX 1.1 file.
    pts items: {"lat": float, "lon": float, "ele": float|None, "time": str|None}
    split_idx >= 0 creates two <trkseg> elements, split after that index.
    """
    NS = "http://www.topografix.com/GPX/1/1"

    def _write_seg(parent, points):
        seg = ET.SubElement(parent, f"{{{NS}}}trkseg")
        for p in points:
            trkpt = ET.SubElement(seg, f"{{{NS}}}trkpt")
            trkpt.set("lat", f"{p['lat']:.8f}")
            trkpt.set("lon", f"{p['lon']:.8f}")
            if p.get("ele") is not None:
                ET.SubElement(trkpt, f"{{{NS}}}ele").text = f"{p['ele']:.1f}"
            if p.get("time"):
                ET.SubElement(trkpt, f"{{{NS}}}time").text = p["time"]
            # Preserve any raw <extensions> XML (string) by parsing and appending
            if p.get("extensions"):
                try:
                    ext_el = ET.fromstring(p["extensions"])
                    trkpt.append(ext_el)
                except Exception:
                    # If the stored string was inner XML without a wrapper, try wrapping
                    try:
                        ext_el = ET.fromstring(f"<extensions>{p['extensions']}</extensions>")
                        trkpt.append(ext_el)
                    except Exception:
                        pass

    # Register default namespace so output uses the default xmlns (no ns0 prefix)
    ET.register_namespace('', NS)
    gpx = ET.Element(f"{{{NS}}}gpx")
    gpx.set("version", "1.1")
    gpx.set("creator", "GPX Utility")

    trk = ET.SubElement(gpx, f"{{{NS}}}trk")

    if split_idx < 0 or split_idx >= len(pts) - 1:
        _write_seg(trk, pts)
    else:
        _write_seg(trk, pts[:split_idx + 1])
        _write_seg(trk, pts[split_idx + 1:])

    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def parse_gpx(path: str) -> list[GPXTrack]:
    """Parse a GPX file and return a list of GPXTrack objects."""
    tree = ET.parse(path)
    root = tree.getroot()

    # Detect namespace (GPX 1.0 or 1.1)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    tracks = []
    for trk in root.findall(f"{ns}trk"):
        name_el = trk.find(f"{ns}name")
        name = name_el.text.strip() if name_el is not None and name_el.text else "Track"

        points = []
        for seg in trk.findall(f"{ns}trkseg"):
            for pt in seg.findall(f"{ns}trkpt"):
                lat = float(pt.get("lat"))
                lon = float(pt.get("lon"))

                ele_el  = pt.find(f"{ns}ele")
                time_el = pt.find(f"{ns}time")
                ele  = float(ele_el.text)  if ele_el  is not None and ele_el.text  else None
                time = time_el.text.strip() if time_el is not None and time_el.text else None
                # Preserve <extensions> element (as raw XML string) if present
                ext_el = pt.find(f"{ns}extensions")
                extensions = ET.tostring(ext_el, encoding='unicode') if ext_el is not None else None

                points.append(GPXPoint(lat=lat, lon=lon, ele=ele, time=time, extensions=extensions))

        tracks.append(GPXTrack(name=name, points=points))

    return tracks


# ── Stats helpers ────────────────────────────────────────────────────────────

def _haversine_km(p1: GPXPoint, p2: GPXPoint) -> float:
    R = 6371.0
    dlat = math.radians(p2.lat - p1.lat)
    dlon = math.radians(p2.lon - p1.lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(p1.lat)) * math.cos(math.radians(p2.lat)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def track_stats(track: GPXTrack) -> dict:
    pts = track.points
    if not pts:
        return {}

    distance_km = sum(_haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    ele_gain = ele_loss = 0.0
    ele_values = [p.ele for p in pts if p.ele is not None]
    for i in range(1, len(ele_values)):
        diff = ele_values[i] - ele_values[i - 1]
        if diff > 0:
            ele_gain += diff
        else:
            ele_loss += abs(diff)

    duration = None
    if pts[0].time and pts[-1].time:
        from datetime import datetime, timezone
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            t0 = datetime.strptime(pts[0].time,  fmt).replace(tzinfo=timezone.utc)
            t1 = datetime.strptime(pts[-1].time, fmt).replace(tzinfo=timezone.utc)
            duration = int((t1 - t0).total_seconds())
        except ValueError:
            pass

    return {
        "points":      len(pts),
        "distance_km": distance_km,
        "ele_gain":    ele_gain,
        "ele_loss":    ele_loss,
        "ele_min":     min(ele_values) if ele_values else None,
        "ele_max":     max(ele_values) if ele_values else None,
        "duration_s":  duration,
        "time_start":  pts[0].time,
        "time_end":    pts[-1].time,
    }
