import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Uri, Array, Object, Action
from System.Windows import FontWeights, Thickness, VerticalAlignment, HorizontalAlignment
from System.Windows.Controls import (
    Border, StackPanel, TextBlock, Button, ColumnDefinition, Grid
)
from System.Windows.Media import SolidColorBrush, Color, Brushes, ScaleTransform
from System.Windows.Threading import DispatcherTimer
from System import TimeSpan
from Microsoft.Win32 import SaveFileDialog
import wpf
import os
import json
import math
import threading
import urllib.request
import xml.etree.ElementTree as ET

from core.gpx_parser import track_stats
from core import settings as settings_mod
from core import tile_server as tile_server_mod


# ── Frozen brushes ────────────────────────────────────────────────────────────
def _fb(r, g, b):
    b_ = SolidColorBrush(Color.FromRgb(r, g, b))
    b_.Freeze()
    return b_

_C_DARK    = _fb(0x18, 0x18, 0x1B)
_C_MUTED   = _fb(0x71, 0x71, 0x7A)
_C_BLUE    = _fb(0x25, 0x63, 0xEB)
_C_GREEN   = _fb(0x16, 0xA3, 0x4A)
_C_RED     = _fb(0xDC, 0x26, 0x26)
_C_BORDER  = _fb(0xE4, 0xE4, 0xE7)
_C_BGLIGHT = _fb(0xF4, 0xF4, 0xF5)
_C_AMBER   = _fb(0xD9, 0x77, 0x06)   # draw-mode accent

# ── OSRM base URLs per profile ────────────────────────────────────────────────
# router.project-osrm.org only serves driving; use routing.openstreetmap.de
# for foot and bike which runs separate OSRM instances per mode.
_OSRM_BASE = {
    'foot': 'https://routing.openstreetmap.de/routed-foot/route/v1/foot',
    'bike': 'https://routing.openstreetmap.de/routed-bike/route/v1/bike',
    'car':  'https://router.project-osrm.org/route/v1/driving',
}

# Cycle order for segment connector clicks (draw mode segments become 'foot' when clicked)
_MODE_CYCLE  = ['foot', 'bike', 'car', 'direct']
_MODE_LABELS = {
    'foot':   '🚶  Foot',
    'bike':   '🚴  Bike',
    'car':    '🚗  Car',
    'direct': '⟶  Direct',
    'draw':   '✏  Draw',
}


def _haversine_m(a, b):
    """Haversine distance in metres between [lat,lon] pairs."""
    R = 6_371_000.0
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0]))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


class RoutePage:
    def __init__(self, settings=None, on_settings_changed=None):
        wpf.LoadComponent(self, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "page_route.xaml"))

        self._settings            = settings or settings_mod.load()
        self._on_settings_changed = on_settings_changed

        self._root    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        html_path     = os.path.join(self._root, "assets", "route.html")
        self.MapBrowser.Navigate(Uri(html_path))
        self._loaded  = False
        self.MapBrowser.LoadCompleted += self._on_loaded

        self._profile    = 'foot'
        self._waypoints  = []     # [{lat, lon}, ...]
        self._segments   = []     # [{'mode': str, 'coords': [[lat,lon],...] | None}, ...]
        self._route_coords = []   # concatenated full route for export

        # Profile pill references
        self._prof_borders = {
            'foot':   self.BtnFoot,
            'bike':   self.BtnBike,
            'car':    self.BtnCar,
            'direct': self.BtnDirect,
            'draw':   self.BtnDraw,
        }
        self._prof_texts = {
            'foot':   self.BtnFootTxt,
            'bike':   self.BtnBikeTxt,
            'car':    self.BtnCarTxt,
            'direct': self.BtnDirectTxt,
            'draw':   self.BtnDrawTxt,
        }

        self.BtnFoot.MouseLeftButtonDown   += lambda s, e: self._set_profile('foot')
        self.BtnBike.MouseLeftButtonDown   += lambda s, e: self._set_profile('bike')
        self.BtnCar.MouseLeftButtonDown    += lambda s, e: self._set_profile('car')
        self.BtnDirect.MouseLeftButtonDown += lambda s, e: self._set_profile('direct')
        self.BtnDraw.MouseLeftButtonDown   += lambda s, e: self._set_profile('draw')
        self.BtnClear.Click  += self._on_clear
        self.BtnExport.Click += self._on_export

        # Poll JS callback queue every 150 ms
        self._timer = DispatcherTimer()
        self._timer.Interval = TimeSpan.FromMilliseconds(150)
        self._timer.Tick += self._poll
        self._timer.Start()

    # ── Map ready ────────────────────────────────────────────────────────────

    def _on_loaded(self, _s, _e):
        self._loaded = True
        s = self._settings
        try:
            self.MapBrowser.InvokeScript(
                "setDefaultView",
                Array[Object]([str(s['map_lat']), str(s['map_lon']), str(s['map_zoom'])]))
        except Exception:
            pass
        self.apply_settings(self._settings)

    # ── Apply settings ────────────────────────────────────────────────────────

    def apply_settings(self, settings):
        self._settings = settings
        scale = settings_mod.font_scale(settings)
        self.LeftPanel.LayoutTransform = ScaleTransform(scale, scale)
        if self._loaded:
            try:
                self.MapBrowser.InvokeScript(
                    "setUIScale", Array[Object]([str(scale)]))
            except Exception:
                pass
            try:
                self.MapBrowser.InvokeScript(
                    "setTileServerPort", Array[Object]([str(tile_server_mod.get().port)]))
            except Exception:
                pass
            style = settings.get('map_style', 'map')
            try:
                self.MapBrowser.InvokeScript(
                    "setTileLayer", Array[Object]([style]))
            except Exception:
                pass

    # ── Poll JS callback queue ────────────────────────────────────────────────

    def _poll(self, _s, _e):
        if not self._loaded:
            return
        try:
            cb = self.MapBrowser.InvokeScript("dequeueCallback")
            if cb:
                self._handle_cb(str(cb))
        except Exception:
            pass

    def _handle_cb(self, cb):
        sep    = cb.index('|')
        action = cb[:sep]
        data   = json.loads(cb[sep + 1:])

        if action == 'add':
            # JS always appends (idx == current length); append here too.
            self._waypoints.append({'lat': data['lat'], 'lon': data['lon']})
            # Only add a segment once there are 2+ waypoints to connect.
            if len(self._waypoints) >= 2:
                self._segments.append({'mode': self._profile, 'coords': None})
            self._sync_waypoints()
            if len(self._waypoints) >= 2:
                self._route_async()

        elif action == 'move':
            idx = data['idx']
            if 0 <= idx < len(self._waypoints):
                self._waypoints[idx] = {'lat': data['lat'], 'lon': data['lon']}
                # Invalidate coords for adjacent segments (except manual draw segments)
                for si in [idx - 1, idx]:
                    if 0 <= si < len(self._segments):
                        if self._segments[si]['mode'] != 'draw':
                            self._segments[si]['coords'] = None
                self._sync_waypoints()
                if len(self._waypoints) >= 2:
                    self._route_async()

        elif action == 'remove':
            idx = data['idx']
            if 0 <= idx < len(self._waypoints):
                self._remove_waypoint(idx)
                self._sync_waypoints()
                if len(self._waypoints) >= 2:
                    self._route_async()
                else:
                    self._clear_route_display()

        elif action == 'manual_finish':
            self._on_manual_finish(data)

        elif action == 'save_view':
            self._settings['map_lat']  = data['lat']
            self._settings['map_lon']  = data['lon']
            self._settings['map_zoom'] = int(data['zoom'])
            settings_mod.save(self._settings)
            if self._on_settings_changed:
                self._on_settings_changed(self._settings)

    # ── Manual draw finish ────────────────────────────────────────────────────

    def _on_manual_finish(self, data):
        verts = data.get('verts', [])   # [[lat, lon], ...]
        if len(verts) < 2:
            return

        end_lat, end_lon = verts[-1]

        if not self._waypoints:
            # No waypoints yet: first vertex = anchor (W1), last vertex = W2
            start = {'lat': verts[0][0], 'lon': verts[0][1]}
            self._waypoints.append(start)
            seg_coords = [[v[0], v[1]] for v in verts]
        else:
            # Connect drawn polyline from last waypoint through drawn vertices
            lw = self._waypoints[-1]
            seg_coords = [[lw['lat'], lw['lon']]] + [[v[0], v[1]] for v in verts]

        # Add endpoint as new waypoint
        self._waypoints.append({'lat': end_lat, 'lon': end_lon})
        self._segments.append({'mode': 'draw', 'coords': seg_coords})

        self._sync_waypoints()
        self._route_async()

    # ── Remove waypoint + adjust segments ────────────────────────────────────

    def _remove_waypoint(self, idx):
        n = len(self._waypoints)
        self._waypoints.pop(idx)
        if n <= 1:
            self._segments = []
        elif idx == 0:
            if self._segments:
                self._segments.pop(0)
        elif idx == n - 1:
            if self._segments:
                self._segments.pop()
        else:
            # Remove two adjacent segments, insert a new one with current profile
            if len(self._segments) > idx:
                self._segments.pop(idx)
            if len(self._segments) >= idx:
                self._segments.pop(idx - 1)
            self._segments.insert(idx - 1, {'mode': self._profile, 'coords': None})

    # ── Profile selector ─────────────────────────────────────────────────────

    def _set_profile(self, profile):
        self._profile = profile
        for key, border in self._prof_borders.items():
            active = (key == profile)
            if key == 'draw':
                border.Background    = _C_AMBER if active else Brushes.Transparent
                border.BorderBrush   = _C_AMBER if active else _C_BORDER
            else:
                border.Background    = _C_DARK if active else Brushes.Transparent
                border.BorderBrush   = _C_DARK if active else _C_BORDER
            border.BorderThickness = Thickness(0) if active else Thickness(1)
            self._prof_texts[key].Foreground = Brushes.White if active else _C_MUTED
        # Inform JS so it can switch click behaviour
        try:
            self.MapBrowser.InvokeScript("setMode", Array[Object]([profile]))
        except Exception:
            pass

    # ── Sync JS waypoint markers ──────────────────────────────────────────────

    def _sync_waypoints(self):
        self._rebuild_wpt_panel()
        try:
            self.MapBrowser.InvokeScript(
                "setWaypoints", Array[Object]([json.dumps(self._waypoints)]))
        except Exception:
            pass

    # ── Waypoints panel ───────────────────────────────────────────────────────

    def _rebuild_wpt_panel(self):
        self.WptStack.Children.Clear()
        n = len(self._waypoints)
        self.LblWptCount.Text = f"{n} point{'s' if n != 1 else ''}"

        for i, wp in enumerate(self._waypoints):
            self.WptStack.Children.Add(self._make_wpt_item(i, wp))
            # Segment connector between consecutive waypoints
            if i < n - 1 and i < len(self._segments):
                self.WptStack.Children.Add(self._make_seg_connector(i))

    def _make_wpt_item(self, idx, wp):
        n = len(self._waypoints)
        if idx == 0:       clr = _C_GREEN
        elif idx == n - 1: clr = _C_RED
        else:              clr = _C_BLUE

        outer = Border()
        outer.Margin          = Thickness(0, 0, 0, 0)
        outer.Padding         = Thickness(8, 6, 8, 6)
        outer.CornerRadius    = System_CornerRadius(6)
        outer.BorderBrush     = _C_BORDER
        outer.BorderThickness = Thickness(1)

        row = Grid()
        c0 = ColumnDefinition(); c0.Width = System_GridLength(28)
        c1 = ColumnDefinition()
        c2 = ColumnDefinition(); c2.Width = System_GridLength(24)
        row.ColumnDefinitions.Add(c0)
        row.ColumnDefinitions.Add(c1)
        row.ColumnDefinitions.Add(c2)

        badge = Border()
        badge.Width  = 22; badge.Height = 22
        badge.CornerRadius = System_CornerRadius(11)
        badge.Background   = clr
        badge.HorizontalAlignment = HorizontalAlignment.Left
        badge.VerticalAlignment   = VerticalAlignment.Center
        num = TextBlock()
        num.Text = str(idx + 1); num.FontSize = 10.0
        num.FontWeight = FontWeights.Bold; num.Foreground = Brushes.White
        num.HorizontalAlignment = HorizontalAlignment.Center
        num.VerticalAlignment   = VerticalAlignment.Center
        badge.Child = num
        Grid.SetColumn(badge, 0); row.Children.Add(badge)

        lbl = TextBlock()
        lbl.Text  = f"{wp['lat']:.5f}, {wp['lon']:.5f}"
        lbl.FontSize = 11.5; lbl.Foreground = _C_DARK
        lbl.VerticalAlignment = VerticalAlignment.Center
        lbl.Margin = Thickness(8, 0, 0, 0)
        Grid.SetColumn(lbl, 1); row.Children.Add(lbl)

        delbtn = Button()
        delbtn.Content = "×"; delbtn.FontSize = 14.0
        delbtn.Width = 20; delbtn.Height = 20
        delbtn.Background = Brushes.Transparent
        delbtn.BorderThickness = Thickness(0); delbtn.Foreground = _C_MUTED
        delbtn.VerticalAlignment   = VerticalAlignment.Center
        delbtn.HorizontalAlignment = HorizontalAlignment.Right
        delbtn.Tag   = idx
        delbtn.Click += self._on_delete_wpt
        Grid.SetColumn(delbtn, 2); row.Children.Add(delbtn)

        outer.Child = row
        return outer

    def _make_seg_connector(self, seg_idx):
        seg  = self._segments[seg_idx]
        mode = seg['mode']
        label = _MODE_LABELS.get(mode, mode)
        is_draw = (mode == 'draw')

        row = Border()
        row.Margin          = Thickness(12, 2, 12, 2)
        row.Padding         = Thickness(6, 3, 6, 3)
        row.CornerRadius    = System_CornerRadius(4)
        row.BorderThickness = Thickness(1)
        row.BorderBrush     = _C_AMBER if is_draw else _C_BORDER
        row.Background      = _C_BGLIGHT
        row.Tag             = seg_idx
        row.MouseLeftButtonDown += self._on_seg_click

        lbl = TextBlock()
        lbl.Text = label
        lbl.FontSize = 10.0
        lbl.Foreground = _C_AMBER if is_draw else _C_MUTED
        lbl.HorizontalAlignment = HorizontalAlignment.Center
        row.Child = lbl
        return row

    def _on_seg_click(self, sender, e):
        idx = int(str(sender.Tag))
        if idx >= len(self._segments):
            return
        cur = self._segments[idx]['mode']
        if cur in _MODE_CYCLE:
            nxt = _MODE_CYCLE[(_MODE_CYCLE.index(cur) + 1) % len(_MODE_CYCLE)]
        else:
            # 'draw' segment → switch to current profile (OSRM/direct)
            nxt = self._profile if self._profile != 'draw' else 'foot'
        self._segments[idx] = {'mode': nxt, 'coords': None}
        self._rebuild_wpt_panel()
        if len(self._waypoints) >= 2:
            self._route_async()

    def _on_delete_wpt(self, sender, e):
        idx = int(str(sender.Tag))
        if 0 <= idx < len(self._waypoints):
            self._remove_waypoint(idx)
            self._sync_waypoints()
            if len(self._waypoints) >= 2:
                self._route_async()
            else:
                self._clear_route_display()

    # ── Clear ─────────────────────────────────────────────────────────────────

    def _on_clear(self, _s, _e):
        self._waypoints    = []
        self._segments     = []
        self._route_coords = []
        self._rebuild_wpt_panel()
        self.LblDistance.Text = "—"
        self.LblDuration.Text = "—"
        self.BtnExport.IsEnabled = False
        try:
            self.MapBrowser.InvokeScript("clearAll")
        except Exception:
            pass

    def _clear_route_display(self):
        self._route_coords = []
        self.LblDistance.Text = "—"
        self.LblDuration.Text = "—"
        self.BtnExport.IsEnabled = False
        try:
            self.MapBrowser.InvokeScript("drawRoute", Array[Object](["[]"]))
            self.MapBrowser.InvokeScript("setStatus", Array[Object](["", "false"]))
        except Exception:
            pass

    # ── Per-segment routing ───────────────────────────────────────────────────

    def _route_async(self):
        wpts = list(self._waypoints)
        segs = list(self._segments)
        # Pad if somehow misaligned
        while len(segs) < len(wpts) - 1:
            segs.append({'mode': self._profile, 'coords': None})

        try:
            self.MapBrowser.InvokeScript(
                "setStatus", Array[Object](["Calculating route…", "false"]))
        except Exception:
            pass

        def worker():
            seg_coords_list = []
            total_dist = 0.0
            total_dur  = 0.0
            has_dur    = True
            err        = None

            for i in range(len(wpts) - 1):
                a, b = wpts[i], wpts[i + 1]
                seg  = segs[i]
                mode = seg['mode']

                if mode == 'draw' and seg.get('coords'):
                    coords = seg['coords']
                    for j in range(len(coords) - 1):
                        total_dist += _haversine_m(coords[j], coords[j + 1])
                    has_dur = False
                    seg_coords_list.append(coords)

                elif mode == 'direct':
                    coords = [[a['lat'], a['lon']], [b['lat'], b['lon']]]
                    total_dist += _haversine_m(coords[0], coords[1])
                    has_dur = False
                    seg_coords_list.append(coords)

                else:
                    try:
                        res = _fetch_osrm([a, b], mode)
                        total_dist += res['distance']
                        if has_dur:
                            total_dur += res['duration']
                        seg_coords_list.append(res['coords'])
                    except Exception as ex:
                        err = str(ex)
                        break

            def on_ui():
                if err:
                    try:
                        self.MapBrowser.InvokeScript(
                            "setStatus", Array[Object]([f"Routing failed: {err}", "true"]))
                    except Exception:
                        pass
                    return
                # Concatenate (skip first point of each segment after the first)
                all_coords = []
                for k, sc in enumerate(seg_coords_list):
                    all_coords.extend(sc if k == 0 else sc[1:])
                self._apply_route({
                    'coords':   all_coords,
                    'distance': total_dist,
                    'duration': total_dur if has_dur else None,
                })

            self.MapBrowser.Dispatcher.Invoke(Action(on_ui))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_route(self, result):
        self._route_coords = result['coords']
        dist_m  = result['distance']
        dist_km = dist_m / 1000

        self.LblDistance.Text = f"{dist_km:.2f} km" if dist_km >= 1 else f"{dist_m:.0f} m"

        dur = result['duration']
        if dur is None:
            self.LblDuration.Text = "—"
        else:
            dur_s = int(dur)
            h, rem = divmod(dur_s, 3600)
            m, s   = divmod(rem, 60)
            self.LblDuration.Text = f"{h}h {m:02d}m" if h > 0 else f"{m}m {s:02d}s"

        self.BtnExport.IsEnabled = True
        try:
            self.MapBrowser.InvokeScript(
                "drawRoute", Array[Object]([json.dumps(self._route_coords)]))
            self.MapBrowser.InvokeScript(
                "setStatus", Array[Object](["", "false"]))
        except Exception:
            pass

    # ── Export GPX ────────────────────────────────────────────────────────────

    def _on_export(self, _s, _e):
        if not self._route_coords:
            return
        dlg            = SaveFileDialog()
        dlg.Title      = "Export route as GPX"
        dlg.Filter     = "GPX files (*.gpx)|*.gpx"
        dlg.DefaultExt = ".gpx"
        if not dlg.ShowDialog():
            return
        try:
            _write_route_gpx(self._route_coords, self._waypoints,
                             str(dlg.FileName), self._profile)
        except Exception:
            pass


# ── OSRM HTTP call (background thread) ────────────────────────────────────────

def _fetch_osrm(waypoints, profile):
    base       = _OSRM_BASE.get(profile, _OSRM_BASE['foot'])
    coords_str = ";".join(f"{w['lon']},{w['lat']}" for w in waypoints)
    url = f"{base}/{coords_str}?overview=full&geometries=geojson&steps=false"
    req = urllib.request.Request(url, headers={"User-Agent": "GPX-Utility/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(data.get("message", "No route returned"))
    route = data["routes"][0]
    coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
    return {
        "coords":   coords,
        "distance": route["distance"],
        "duration": route["duration"],
    }


# ── GPX export ────────────────────────────────────────────────────────────────

def _write_route_gpx(coords, waypoints, path, profile):
    NS  = "http://www.topografix.com/GPX/1/1"
    gpx = ET.Element(f"{{{NS}}}gpx", version="1.1", creator="GPX Utility")
    for i, wp in enumerate(waypoints):
        wpt = ET.SubElement(gpx, f"{{{NS}}}wpt")
        wpt.set("lat", f"{wp['lat']:.8f}")
        wpt.set("lon", f"{wp['lon']:.8f}")
        ET.SubElement(wpt, f"{{{NS}}}name").text = f"WP{i+1}"
    trk = ET.SubElement(gpx, f"{{{NS}}}trk")
    ET.SubElement(trk, f"{{{NS}}}name").text = f"Route ({profile})"
    seg = ET.SubElement(trk, f"{{{NS}}}trkseg")
    for lat, lon in coords:
        pt = ET.SubElement(seg, f"{{{NS}}}trkpt")
        pt.set("lat", f"{lat:.8f}")
        pt.set("lon", f"{lon:.8f}")
    ET.indent(gpx)
    ET.ElementTree(gpx).write(path, xml_declaration=True, encoding="UTF-8")


# ── WPF helpers ───────────────────────────────────────────────────────────────
from System.Windows import CornerRadius as System_CornerRadius
from System.Windows import GridLength   as System_GridLength
