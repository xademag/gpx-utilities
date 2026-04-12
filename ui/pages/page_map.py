import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Uri, Array, Object
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
import wpf
import os
import json

from core.gpx_parser import parse_gpx, track_stats, write_gpx


class MapPage:
    def __init__(self):
        wpf.LoadComponent(self, os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_map.xaml"))

        self._root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        html_path = os.path.join(self._root, "assets", "map.html")
        self.MapBrowser.Navigate(Uri(html_path))

        self.BtnLoad.Click += self._on_load
        self.BtnSave.Click += self._on_save

    # ── Load GPX ─────────────────────────────────────────────────────────────

    def _on_load(self, _s, _e):
        dlg = OpenFileDialog()
        dlg.Title  = "Open GPX file"
        dlg.Filter = "GPX files (*.gpx)|*.gpx|All files (*.*)|*.*"

        if not dlg.ShowDialog():
            return

        path = str(dlg.FileName)
        self.LblStatus.Text = "Loading…"

        try:
            tracks = parse_gpx(path)
        except Exception as ex:
            self.LblStatus.Text = f"Error: {ex}"
            return

        if not tracks:
            self.LblStatus.Text = "No tracks found in file."
            return

        track = tracks[0]
        stats = track_stats(track)

        self._update_info(os.path.basename(path), track, stats)
        self._draw_track(track)
        self.BtnSave.IsEnabled = True

    # ── Save GPX ──────────────────────────────────────────────────────────────

    def _on_save(self, _s, _e):
        try:
            pts_json  = str(self.MapBrowser.InvokeScript("getModifiedPts"))
            split_raw = self.MapBrowser.InvokeScript("getSplitIdx")
            split_idx = int(str(split_raw)) if split_raw is not None else -1
            pts = json.loads(pts_json)
        except Exception as ex:
            self.LblStatus.Text = f"Error reading track: {ex}"
            return

        if not pts:
            self.LblStatus.Text = "No track data to save."
            return

        dlg = SaveFileDialog()
        dlg.Title      = "Save modified GPX"
        dlg.Filter     = "GPX files (*.gpx)|*.gpx"
        dlg.DefaultExt = ".gpx"

        if not dlg.ShowDialog():
            return

        path = str(dlg.FileName)
        try:
            write_gpx(pts, path, split_idx)
            self.LblStatus.Text = f"Saved: {os.path.basename(path)}"
        except Exception as ex:
            self.LblStatus.Text = f"Save error: {ex}"

    # ── Info panel ────────────────────────────────────────────────────────────

    def _update_info(self, filename, track, stats):
        self.LblTrackName.Text = track.name or filename

        if stats.get("distance_km") is not None:
            self.LblDistance.Text = f"{stats['distance_km']:.2f} km"
        if stats.get("points"):
            self.LblPoints.Text = str(stats["points"])
        if stats.get("ele_gain") is not None:
            self.LblEleGain.Text = f"+{stats['ele_gain']:.0f} m"
        if stats.get("ele_loss") is not None:
            self.LblEleLoss.Text = f"-{stats['ele_loss']:.0f} m"
        if stats.get("ele_min") is not None:
            self.LblEleMin.Text = f"{stats['ele_min']:.0f} m"
        if stats.get("ele_max") is not None:
            self.LblEleMax.Text = f"{stats['ele_max']:.0f} m"

        dur = stats.get("duration_s")
        if dur is not None:
            h, rem = divmod(dur, 3600)
            m, s   = divmod(rem, 60)
            self.LblDuration.Text = f"{h:02d}:{m:02d}:{s:02d}"

        self.LblStatus.Text = f"Loaded: {filename}"

    # ── Draw track on map ─────────────────────────────────────────────────────

    def _draw_track(self, track):
        pts = [{"lat": p.lat, "lon": p.lon, "ele": p.ele, "time": p.time}
               for p in track.points]
        pts_json = json.dumps(pts)
        try:
            self.MapBrowser.InvokeScript("drawTrack", Array[Object]([pts_json]))
        except Exception as ex:
            self.LblStatus.Text = f"Map error: {ex}"
