import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Uri, Array, Object
from System.Windows import FontWeights, Thickness, VerticalAlignment, TextTrimming, GridLength, GridUnitType
from System.Windows.Controls import TreeViewItem, StackPanel, TextBlock, Button, Orientation, Grid as WPFGrid, ColumnDefinition
from System.Windows.Media import SolidColorBrush, Color, Brushes, ScaleTransform
from System.Windows.Threading import DispatcherTimer
from System import TimeSpan
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
import wpf
import os
import json

from core.gpx_parser import parse_gpx, track_stats, write_gpx
from core.fit_parser  import parse_fit
from core import settings as settings_mod
from core import tile_server as tile_server_mod


# ── Shared frozen brushes (thread-safe across WPF STA / Python threads) ───────
def _frozen_brush(r, g, b):
    b_ = SolidColorBrush(Color.FromRgb(r, g, b))
    b_.Freeze()
    return b_

_CLR_DARK  = _frozen_brush(0x18, 0x18, 0x1B)   # file node
_CLR_MUTED = _frozen_brush(0x52, 0x52, 0x5B)   # visible inactive track
_CLR_SEL   = _frozen_brush(0x25, 0x63, 0xEB)   # active track
_CLR_HIDE  = _frozen_brush(0xC4, 0xC4, 0xC7)   # hidden track


class MapPage:
    def __init__(self, settings=None, on_settings_changed=None):
        wpf.LoadComponent(self, os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_map.xaml"))

        self._settings            = settings or settings_mod.load()
        self._on_settings_changed = on_settings_changed

        self._root     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        html_path      = os.path.join(self._root, "assets", "map.html")
        self.MapBrowser.Navigate(Uri(html_path))
        self._loaded   = False
        self.MapBrowser.LoadCompleted += self._on_map_loaded

        self._files      = []
        self._active     = (-1, -1)
        self._visibility = {}

        self.BtnLoad.Click                += self._on_load
        self.BtnSave.Click                += self._on_save
        self.FileTree.SelectedItemChanged += self._on_tree_selected

        # Poll JS callback queue (save_view etc.) every 200 ms
        self._timer = DispatcherTimer()
        self._timer.Interval = TimeSpan.FromMilliseconds(200)
        self._timer.Tick += self._poll
        self._timer.Start()

    # ── Map ready ─────────────────────────────────────────────────────────────

    def _on_map_loaded(self, _s, _e):
        self._loaded = True
        s = self._settings
        try:
            self.MapBrowser.InvokeScript(
                "setDefaultView",
                Array[Object]([str(s['map_lat']), str(s['map_lon']), str(s['map_zoom'])]))
        except Exception:
            pass
        self.apply_settings(self._settings)

    # ── Poll JS callbacks ─────────────────────────────────────────────────────

    def _poll(self, _s, _e):
        if not self._loaded:
            return
        try:
            cb = self.MapBrowser.InvokeScript("dequeueCallback")
            if cb:
                self._handle_map_cb(str(cb))
        except Exception:
            pass

    def _handle_map_cb(self, cb):
        sep    = cb.index('|')
        action = cb[:sep]
        data   = json.loads(cb[sep + 1:])
        if action == 'save_view':
            self._settings['map_lat']  = data['lat']
            self._settings['map_lon']  = data['lon']
            self._settings['map_zoom'] = int(data['zoom'])
            settings_mod.save(self._settings)
            if self._on_settings_changed:
                self._on_settings_changed(self._settings)
        elif action == 'split':
            self._on_split_detected(int(data['split_idx']))

    # ── Split segments ────────────────────────────────────────────────────────

    def _on_split_detected(self, split_idx):
        """Called by the JS callback queue when the user splits a track."""
        fi, ti = self._active
        # Find the active track TreeViewItem
        for file_item in self.FileTree.Items:
            for track_item in file_item.Items:
                if str(track_item.Tag) == f"{fi},{ti}":
                    self._clear_split_segments(track_item)
                    if split_idx >= 0:
                        track_item.IsExpanded = True
                        seg1 = self._make_segment_item(0, "Segment 1")
                        seg2 = self._make_segment_item(1, "Segment 2")
                        track_item.Items.Add(seg1)
                        track_item.Items.Add(seg2)
                    return

    @staticmethod
    def _clear_split_segments(track_item):
        """Remove any existing segment sub-items from a track TreeViewItem."""
        to_remove = [item for item in track_item.Items
                     if item.Tag is not None and str(item.Tag).startswith("seg,")]
        for item in to_remove:
            track_item.Items.Remove(item)

    def _make_segment_item(self, seg_idx, label):
        item = TreeViewItem()
        item.Tag      = f"seg,{seg_idx}"
        item.FontSize = 11.0

        sp             = StackPanel()
        sp.Orientation = Orientation.Horizontal

        btn                  = Button()
        btn.Content          = "💾"
        btn.FontSize         = 11.0
        btn.Width            = 20.0
        btn.Height           = 18.0
        btn.Margin           = Thickness(0, 0, 5, 0)
        btn.Background       = Brushes.Transparent
        btn.BorderThickness  = Thickness(0)
        btn.VerticalAlignment = VerticalAlignment.Center
        btn.Tag              = str(seg_idx)
        btn.Click           += self._on_save_segment

        tb                   = TextBlock()
        tb.Text              = label
        tb.VerticalAlignment = VerticalAlignment.Center
        tb.Foreground        = _CLR_MUTED

        sp.Children.Add(btn)
        sp.Children.Add(tb)
        item.Header = sp
        return item

    def _on_save_segment(self, sender, e):
        e.Handled = True
        seg_idx = int(str(sender.Tag))
        try:
            pts_json  = str(self.MapBrowser.InvokeScript("getModifiedPts"))
            split_raw = self.MapBrowser.InvokeScript("getSplitIdx")
            split_idx = int(str(split_raw)) if split_raw is not None else -1
            pts = json.loads(pts_json)
        except Exception as ex:
            self.LblStatus.Text = f"Error reading track: {ex}"
            return

        if not pts or split_idx < 0:
            self.LblStatus.Text = "No split to save."
            return

        segment = pts[:split_idx + 1] if seg_idx == 0 else pts[split_idx + 1:]
        if not segment:
            self.LblStatus.Text = "Segment is empty."
            return

        dlg = SaveFileDialog()
        dlg.Title      = f"Save Segment {seg_idx + 1}"
        dlg.Filter     = "GPX files (*.gpx)|*.gpx"
        dlg.DefaultExt = ".gpx"
        if not dlg.ShowDialog():
            return
        try:
            write_gpx(segment, str(dlg.FileName))
            self.LblStatus.Text = f"Segment {seg_idx + 1} saved"
        except Exception as ex:
            self.LblStatus.Text = f"Save error: {ex}"

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
            n = settings.get('arrow_every_n', 20)
            try:
                self.MapBrowser.InvokeScript(
                    "setArrowEveryN", Array[Object]([str(n)]))
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

    # ── Load ─────────────────────────────────────────────────────────────────

    def _on_load(self, _s, _e):
        dlg = OpenFileDialog()
        dlg.Title       = "Open GPX / FIT file"
        dlg.Filter      = "Track files (*.gpx;*.fit)|*.gpx;*.fit|GPX files (*.gpx)|*.gpx|FIT files (*.fit)|*.fit|All files (*.*)|*.*"
        dlg.Multiselect = True

        if not dlg.ShowDialog():
            return

        for path in dlg.FileNames:
            path = str(path)
            if any(f["path"] == path for f in self._files):
                continue
            try:
                if path.lower().endswith('.fit'):
                    tracks = parse_fit(path)
                else:
                    tracks = parse_gpx(path)
            except Exception as ex:
                self.LblStatus.Text = f"Error: {os.path.basename(path)}"
                continue
            if tracks:
                fi = len(self._files)
                self._files.append({"path": path, "filename": os.path.basename(path), "tracks": tracks})
                for ti in range(len(tracks)):
                    self._visibility[(fi, ti)] = True

        if not self._files:
            return

        self._rebuild_tree()
        fi = len(self._files) - 1
        self._select(fi, 0)

    # ── Save ─────────────────────────────────────────────────────────────────

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

        try:
            write_gpx(pts, str(dlg.FileName), split_idx)
            self.LblStatus.Text = "Saved"
        except Exception as ex:
            self.LblStatus.Text = f"Save error: {ex}"

    # ── Tree ─────────────────────────────────────────────────────────────────

    def _on_tree_selected(self, _s, _e):
        item = self.FileTree.SelectedItem
        if item is None:
            return
        tag = str(item.Tag) if item.Tag is not None else ""
        if "," not in tag or tag.startswith("seg,"):
            return
        fi, ti = int(tag.split(",")[0]), int(tag.split(",")[1])
        if ti == -1:
            return
        self._select(fi, ti)

    def _on_eye_click(self, sender, e):
        e.Handled = True   # prevent click bubbling to TreeViewItem selection
        tag = str(sender.Tag)
        fi, ti = int(tag.split(",")[0]), int(tag.split(",")[1])
        key = (fi, ti)

        # Active track cannot be hidden — ignore click
        if key == self._active:
            return

        self._visibility[key] = not self._visibility.get(key, True)
        visible = self._visibility[key]

        # Update eye icon and text colour
        sender.Content = self._eye_char(visible)
        self._set_track_item_color(fi, ti)
        self._refresh_bg_tracks()

    # ── Tree build ────────────────────────────────────────────────────────────

    def _rebuild_tree(self):
        self.FileTree.Items.Clear()

        for fi, f in enumerate(self._files):
            file_item             = TreeViewItem()
            file_item.Tag         = f"{fi},-1"
            file_item.Header      = self._make_file_header(fi, f["filename"])
            file_item.FontWeight  = FontWeights.SemiBold
            file_item.Foreground  = _CLR_DARK
            file_item.FontSize    = 12.0
            file_item.IsExpanded  = True

            for ti, track in enumerate(f["tracks"]):
                track_item          = TreeViewItem()
                track_item.Tag      = f"{fi},{ti}"
                track_item.FontSize = 12.0

                name  = track.name or f"Track {ti + 1}"
                stats = track_stats(track)
                dist  = f"  {stats['distance_km']:.1f} km" if stats.get("distance_km") else ""
                pts_n = f"  {stats['points']} pts"         if stats.get("points")       else ""

                visible             = self._visibility.get((fi, ti), True)
                track_item.Header   = self._make_track_header(fi, ti, f"{name}{dist}{pts_n}", visible)
                track_item.Foreground = _CLR_MUTED if visible else _CLR_HIDE

                file_item.Items.Add(track_item)

            self.FileTree.Items.Add(file_item)

        count = len(self._files)
        self.LblStatus.Text = f"{count} file{'s' if count != 1 else ''} loaded"

    def _make_file_header(self, fi, filename):
        """Grid: [filename *] [trash btn Auto] — trash removes the file."""
        g = WPFGrid()
        c0 = ColumnDefinition(); c0.Width = GridLength(1, GridUnitType.Star)
        c1 = ColumnDefinition(); c1.Width = GridLength.Auto
        g.ColumnDefinitions.Add(c0)
        g.ColumnDefinitions.Add(c1)

        tb = TextBlock()
        tb.Text              = filename
        tb.VerticalAlignment = VerticalAlignment.Center
        tb.TextTrimming      = TextTrimming.CharacterEllipsis
        WPFGrid.SetColumn(tb, 0)
        g.Children.Add(tb)

        btn = Button()
        btn.Content            = "🗑"
        btn.FontSize           = 11.0
        btn.Width              = 20.0
        btn.Height             = 18.0
        btn.Margin             = Thickness(6, 0, 0, 0)
        btn.Background         = Brushes.Transparent
        btn.BorderThickness    = Thickness(0)
        btn.VerticalAlignment  = VerticalAlignment.Center
        btn.Tag                = str(fi)
        btn.Click             += self._on_trash_click
        WPFGrid.SetColumn(btn, 1)
        g.Children.Add(btn)

        return g

    def _on_trash_click(self, sender, e):
        e.Handled = True   # prevent TreeViewItem selection
        self._remove_file(int(str(sender.Tag)))

    def _remove_file(self, fi):
        """Remove a loaded file, re-index state, and refresh the view."""
        active_fi, active_ti = self._active

        # Drop visibility entries for the removed file's tracks
        for ti in range(len(self._files[fi]["tracks"])):
            self._visibility.pop((fi, ti), None)

        # Remove the file record
        self._files.pop(fi)

        # Re-index visibility keys: every file with index > fi shifts down
        new_vis = {}
        for (f, t), v in self._visibility.items():
            new_vis[(f - 1 if f > fi else f, t)] = v
        self._visibility = new_vis

        # ── No files remain ───────────────────────────────────────────────
        if not self._files:
            self._active = (-1, -1)
            self.BtnSave.IsEnabled = False
            self._rebuild_tree()
            try:
                self.MapBrowser.InvokeScript("drawTrack",   Array[Object](["[]"]))
                self.MapBrowser.InvokeScript("drawBgTracks", Array[Object](["[]"]))
            except Exception:
                pass
            for attr in ("LblTrackName", "LblDistance", "LblPoints",
                         "LblEleGain", "LblEleLoss", "LblEleMin",
                         "LblEleMax", "LblDuration"):
                getattr(self, attr).Text = "—"
            return

        # ── Determine new active track ─────────────────────────────────────
        if active_fi == fi:
            # The active file was removed — pick first track of the nearest file
            new_fi = min(fi, len(self._files) - 1)
            new_ti = 0
        elif active_fi > fi:
            # Active file shifted down by one
            new_fi, new_ti = active_fi - 1, active_ti
        else:
            new_fi, new_ti = active_fi, active_ti

        self._active = (-1, -1)   # reset so _select fully re-applies everything
        self._rebuild_tree()
        self._select(new_fi, new_ti)

    def _make_track_header(self, fi, ti, label_text, visible):
        """StackPanel: [eye btn] [track label]"""
        sp             = StackPanel()
        sp.Orientation = Orientation.Horizontal

        btn                    = Button()
        btn.Content            = self._eye_char(visible)
        btn.FontSize           = 11.0
        btn.Width              = 20.0
        btn.Height             = 18.0
        btn.Margin             = Thickness(0, 0, 5, 0)
        btn.Background         = Brushes.Transparent
        btn.BorderThickness    = Thickness(0)
        btn.VerticalAlignment  = VerticalAlignment.Center
        btn.Tag                = f"{fi},{ti}"
        btn.Click             += self._on_eye_click

        tb                    = TextBlock()
        tb.Text               = f"  {label_text}"
        tb.VerticalAlignment  = VerticalAlignment.Center

        sp.Children.Add(btn)
        sp.Children.Add(tb)
        return sp

    @staticmethod
    def _eye_char(visible):
        return "●" if visible else "○"

    def _set_track_item_color(self, fi, ti):
        """Update foreground of a single track TreeViewItem."""
        for file_item in self.FileTree.Items:
            for track_item in file_item.Items:
                if str(track_item.Tag) == f"{fi},{ti}":
                    if (fi, ti) == self._active:
                        track_item.Foreground = _CLR_SEL   # blue; turns white via system highlight when selected
                    elif self._visibility.get((fi, ti), True):
                        track_item.Foreground = _CLR_MUTED
                    else:
                        track_item.Foreground = _CLR_HIDE
                    return

    def _highlight_tree_item(self, fi, ti):
        """Mark active item; restore others to muted/hidden colour."""
        for file_item in self.FileTree.Items:
            for track_item in file_item.Items:
                tag = str(track_item.Tag) if track_item.Tag is not None else ""
                if "," not in tag:
                    continue
                tfi, tti = int(tag.split(",")[0]), int(tag.split(",")[1])
                if tfi == fi and tti == ti:
                    # Blue foreground — system highlight turns it white-on-blue when selected,
                    # falls back to readable blue-on-white if something else is clicked
                    track_item.Foreground = _CLR_SEL
                    track_item.IsSelected = True
                    # Update eye button to always show ● for active
                    self._set_eye_btn(track_item, True)
                else:
                    vis = self._visibility.get((tfi, tti), True)
                    track_item.Foreground = _CLR_MUTED if vis else _CLR_HIDE

    @staticmethod
    def _set_eye_btn(track_item, visible):
        """Update eye button content inside a track item's StackPanel header."""
        sp = track_item.Header
        if sp is None:
            return
        try:
            btn = sp.Children[0]
            btn.Content = MapPage._eye_char(visible)
        except Exception:
            pass

    # ── Select & draw ─────────────────────────────────────────────────────────

    def _select(self, fi, ti):
        if fi < 0 or fi >= len(self._files):
            return
        f      = self._files[fi]
        tracks = f["tracks"]
        if ti < 0 or ti >= len(tracks):
            return

        # Clear segment sub-items from the previously active track
        prev_fi, prev_ti = self._active
        if (prev_fi, prev_ti) != (fi, ti):
            for file_item in self.FileTree.Items:
                for track_item in file_item.Items:
                    if str(track_item.Tag) == f"{prev_fi},{prev_ti}":
                        self._clear_split_segments(track_item)

        self._active = (fi, ti)
        track = tracks[ti]
        self._update_info(f["filename"], track, track_stats(track))
        self._draw_track(track)
        self._refresh_bg_tracks()
        self.BtnSave.IsEnabled = True
        self._highlight_tree_item(fi, ti)

    def _refresh_bg_tracks(self):
        """Send all visible non-active tracks to JS as grey background polylines."""
        active_fi, active_ti = self._active
        bg = []
        for fi, f in enumerate(self._files):
            for ti, t in enumerate(f["tracks"]):
                if fi == active_fi and ti == active_ti:
                    continue
                if not self._visibility.get((fi, ti), True):
                    continue
                bg.append([{"lat": p.lat, "lon": p.lon} for p in t.points])
        try:
            self.MapBrowser.InvokeScript("drawBgTracks", Array[Object]([json.dumps(bg)]))
        except Exception:
            pass

    # ── Info panel ────────────────────────────────────────────────────────────

    def _update_info(self, filename, track, stats):
        self.LblTrackName.Text = track.name or filename
        self.LblDistance.Text  = f"{stats['distance_km']:.2f} km" if stats.get("distance_km") is not None else "—"
        self.LblPoints.Text    = str(stats["points"])              if stats.get("points")                  else "—"
        self.LblEleGain.Text   = f"+{stats['ele_gain']:.0f} m"    if stats.get("ele_gain")  is not None   else "—"
        self.LblEleLoss.Text   = f"-{stats['ele_loss']:.0f} m"    if stats.get("ele_loss")  is not None   else "—"
        self.LblEleMin.Text    = f"{stats['ele_min']:.0f} m"      if stats.get("ele_min")   is not None   else "—"
        self.LblEleMax.Text    = f"{stats['ele_max']:.0f} m"      if stats.get("ele_max")   is not None   else "—"

        dur = stats.get("duration_s")
        if dur is not None:
            h, rem = divmod(int(dur), 3600)
            m, s   = divmod(rem, 60)
            self.LblDuration.Text = f"{h:02d}:{m:02d}:{s:02d}"
        else:
            self.LblDuration.Text = "—"

    # ── Draw ─────────────────────────────────────────────────────────────────

    def _draw_track(self, track):
        pts = [{"lat": p.lat, "lon": p.lon, "ele": p.ele, "time": p.time, "extensions": p.extensions}
               for p in track.points]
        try:
            self.MapBrowser.InvokeScript("drawTrack", Array[Object]([json.dumps(pts)]))
        except Exception as ex:
            self.LblStatus.Text = f"Map error: {ex}"
