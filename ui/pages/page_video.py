import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Uri, Array, Object, TimeSpan
from System.Windows import (FontWeights, Thickness, VerticalAlignment, Point,
                             Visibility, CornerRadius, HorizontalAlignment,
                             WindowStartupLocation, ResizeMode, SizeToContent,
                             Application)
from System.Windows.Controls import (TreeViewItem, StackPanel, TextBlock,
                                     Canvas, Grid as WPFGrid,
                                     Border as WPFBorder, TextBox as WPFTextBox,
                                     Label as WPFLabel, Button as WPFButton,
                                     Orientation)
from System.Windows.Input import Cursors, Key
from System.Windows.Media import SolidColorBrush, Color, Brushes
from System.Windows.Shapes import Line, Polygon
from System.Windows.Media import PointCollection
from System.Windows.Threading import DispatcherTimer
from Microsoft.Win32 import OpenFileDialog

import wpf, os, json, math

from core import settings as settings_mod
from core import tile_server as tile_server_mod


# ── Shared brushes ────────────────────────────────────────────────────────────
def _brush(r, g, b):
    b_ = SolidColorBrush(Color.FromRgb(r, g, b))
    b_.Freeze()
    return b_


_CLR_DARK  = _brush(0x18, 0x18, 0x1B)
_CLR_MUTED = _brush(0x52, 0x52, 0x5B)
_CLR_SEL   = _brush(0x25, 0x63, 0xEB)
_CLR_HIDE  = _brush(0xC4, 0xC4, 0xC7)
_CLR_HEAD     = _brush(0xEF, 0x44, 0x44)
_CLR_TICK     = _brush(0xD4, 0xD4, 0xD8)
_CLR_TICK_LBL = _brush(0xA1, 0xA1, 0xAA)

def _brush_a(a, r, g, b):
    b_ = SolidColorBrush(Color.FromArgb(a, r, g, b))
    b_.Freeze()
    return b_

_CLR_SPEED_F    = _brush_a(90,  0x25, 0x63, 0xEB)   # semi-transparent blue fill
_CLR_SPEED_S    = _brush_a(180, 0x25, 0x63, 0xEB)  # speed graph stroke
_CLR_VIDEO_FILL = _brush_a(50,  0x25, 0x63, 0xEB)  # video bar fill
_CLR_VIDEO_STR  = _brush_a(160, 0x25, 0x63, 0xEB)  # video bar stroke

# Overlay colour palette (R, G, B)
_OV_PALETTE = [
    (0xF5, 0x9E, 0x0B),  # amber
    (0x10, 0xB9, 0x81),  # emerald
    (0xF9, 0x73, 0x16),  # orange
    (0x8B, 0x5C, 0xF6),  # violet
    (0xEF, 0x44, 0x44),  # red
    (0x06, 0xB6, 0xD4),  # cyan
]


# ── Time helpers ──────────────────────────────────────────────────────────────
def _epoch_ms(iso):
    """ISO 8601 → Unix ms, or None.  Handles optional milliseconds and Z suffix."""
    if not iso:
        return None
    from datetime import datetime, timezone
    s = iso.rstrip('Z')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp() * 1000
        except ValueError:
            pass
    return None


def _fmt_ms(ms):
    """ms → 'm:ss.mmm'"""
    ms = max(0, int(ms))
    mins, rem = divmod(ms, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{int(mins)}:{int(secs):02d}.{int(millis):03d}"


def _parse_ms(s):
    """'m:ss.mmm' or 'ss.mmm' or plain seconds → float ms, or None."""
    s = s.strip()
    try:
        if ':' in s:
            parts = s.split(':', 1)
            return (float(parts[0]) * 60 + float(parts[1])) * 1000.0
        return float(s) * 1000.0
    except Exception:
        return None


class VideoSyncPage:
    def __init__(self, settings=None, on_settings_changed=None, get_map_page=None):
        wpf.LoadComponent(
            self,
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "page_video.xaml"))

        self._settings            = settings or settings_mod.load()
        self._on_settings_changed = on_settings_changed
        self._get_map_page        = get_map_page
        self._root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # ── Track state ───────────────────────────────────────────────────────
        self._active_pts    = []
        self._active_name   = ""
        self._cached_speeds = []   # speeds parallel to _active_pts, reset on track change

        # ── Video state ───────────────────────────────────────────────────────
        self._video_path   = None
        self._is_playing   = False
        self._fps          = 30.0
        self._duration_ms  = 0.0
        self._video_pos_ms = 0.0

        # ── Map overlay ───────────────────────────────────────────────────────
        self._map_loaded        = False
        self._map_nav_done      = False
        self._dragging          = False
        self._drag_start_offset_x = 0.0
        self._drag_start_offset_y = 0.0
        self._drag_start_screen_x = 0.0
        self._drag_start_screen_y = 0.0

        # ── Timeline canvas state ─────────────────────────────────────────────
        self._tl_playhead   = None
        self._tl_ruler      = []
        self._tl_speed      = []
        self._tl_video_bar  = []
        self._tl_yaxis      = []
        self._tl_overlays   = []
        self._tl_width_last = -1.0

        # ── Text overlays ─────────────────────────────────────────────────────
        self._overlays       = []   # list of overlay dicts
        self._next_ov_id     = 0
        self._sel_ov_id      = None
        self._ov_tl_rects    = {}   # ov_id → (x1, x2)

        # video overlay drag
        self._ov_drag_id         = None
        self._ov_drag_start_mx   = 0.0
        self._ov_drag_start_my   = 0.0
        self._ov_drag_start_xfrac = 0.0
        self._ov_drag_start_yfrac = 0.0

        # timeline overlay drag / resize
        self._tl_ov_drag = None   # None or dict

        # ── Wire video events ─────────────────────────────────────────────────
        self.VideoPlayer.MediaOpened += self._on_media_opened
        self.VideoPlayer.MediaEnded  += self._on_media_ended

        # ── Wire toolbar ──────────────────────────────────────────────────────
        self.BtnOpenVideo.MouseLeftButtonDown += self._on_open_video
        self.BtnPlayPause.MouseLeftButtonDown += self._on_play_pause
        self.BtnGoStart.MouseLeftButtonDown   += lambda s, e: self._seek(0)
        self.BtnGoEnd.MouseLeftButtonDown     += lambda s, e: self._seek(self._duration_ms)
        self.BtnPrevFrame.MouseLeftButtonDown += lambda s, e: self._step_frame(-1)
        self.BtnNextFrame.MouseLeftButtonDown += lambda s, e: self._step_frame(+1)
        self.BtnRewind.MouseLeftButtonDown    += lambda s, e: self._seek_relative(-5000)
        self.BtnFwd.MouseLeftButtonDown       += lambda s, e: self._seek_relative(+5000)
        self.BtnToggleMap.MouseLeftButtonDown += self._toggle_map

        # ── Wire overlay toolbar + edit bar ───────────────────────────────────
        self.BtnAddOverlay.MouseLeftButtonDown       += self._on_add_overlay
        self.BtnApplyOverlayText.MouseLeftButtonDown += self._on_apply_overlay_text
        self.BtnDeleteOverlay.MouseLeftButtonDown    += self._on_delete_overlay
        self.OverlayTextBox.KeyDown                  += self._on_overlay_textbox_keydown

        # Deselect overlay when clicking empty canvas area
        self.OverlayCanvas.MouseLeftButtonDown += self._on_overlay_canvas_click
        self.OverlayCanvas.SizeChanged         += self._on_overlay_canvas_resized

        # ── Wire timeline ─────────────────────────────────────────────────────
        self.TimelineCanvas.MouseLeftButtonDown += self._on_timeline_mousedown
        self.TimelineCanvas.MouseMove           += self._on_timeline_mousemove
        self.TimelineCanvas.MouseLeftButtonUp   += self._on_timeline_mouseup
        self.TimelineCanvas.MouseDoubleClick    += self._on_timeline_dblclick
        self.TimelineCanvas.SizeChanged         += self._on_timeline_resize

        # ── Wire tree ─────────────────────────────────────────────────────────
        self.VideoFileTree.SelectedItemChanged += self._on_tree_selected

        # ── Wire map overlay (declared in XAML) ──────────────────────────────
        self._wire_map_overlay()

        # ── 100 ms polling timer ──────────────────────────────────────────────
        self._timer = DispatcherTimer()
        self._timer.Interval = TimeSpan.FromMilliseconds(100)
        self._timer.Tick     += self._on_timer
        self._timer.Start()

    # ── Activation ───────────────────────────────────────────────────────────

    def on_activated(self):
        self._refresh_tree()
        self._update_info_label()
        if not self._map_popup.IsOpen:
            self._open_map_overlay()

    def apply_settings(self, settings):
        self._settings = settings
        if self._map_loaded:
            self._apply_tile_settings()

    def _apply_tile_settings(self):
        try:
            port = tile_server_mod.get().port
            self._map_browser.InvokeScript(
                "setTileServerPort", Array[Object]([str(port)]))
        except Exception:
            pass
        try:
            style = self._settings.get('map_style', 'map')
            self._map_browser.InvokeScript(
                "setTileLayer", Array[Object]([style]))
        except Exception:
            pass

    # ── Map overlay (XAML-declared) ───────────────────────────────────────────

    def _wire_map_overlay(self):
        self.MapOverlayPopup.PlacementTarget = self.BtnToggleMap
        self.MapDragHandle.MouseLeftButtonDown += self._on_drag_start
        self.MapDragHandle.MouseMove           += self._on_drag_move
        self.MapDragHandle.MouseLeftButtonUp   += self._on_drag_end
        self.BtnCloseMap.MouseLeftButtonDown   += lambda s, e: self._close_map_overlay()
        self._map_popup   = self.MapOverlayPopup
        self._map_browser = self.MapOverlayBrowser
        self.MapOverlayBrowser.Loaded       += self._on_map_browser_loaded
        self.MapOverlayBrowser.LoadCompleted += self._on_map_loaded

    def _on_map_browser_loaded(self, _s, _e):
        if not self._map_nav_done:
            self._map_nav_done = True
            html_path = os.path.join(self._root, "assets", "map.html")
            self.MapOverlayBrowser.Navigate(Uri(html_path))

    def _on_map_loaded(self, _s, _e):
        self._map_loaded = True
        try:
            self._map_browser.InvokeScript("setOverlayMode")
        except Exception:
            pass
        self._apply_tile_settings()
        if self._active_pts:
            self._push_track_to_map()

    # ── Map overlay show / hide / drag ────────────────────────────────────────

    def _toggle_map(self, _s, _e):
        if self._map_popup.IsOpen:
            self._close_map_overlay()
        else:
            self._open_map_overlay()

    def _open_map_overlay(self):
        self._map_popup.IsOpen = True
        self._reposition_map_overlay()

    def _reposition_map_overlay(self):
        try:
            w = self.VideoArea.ActualWidth
            if w <= 1:
                raise ValueError("not laid out")
            pt = self.VideoArea.PointToScreen(Point(w - 302, 10))
            self._map_popup.HorizontalOffset = pt.X
            self._map_popup.VerticalOffset   = pt.Y
        except Exception:
            t = DispatcherTimer()
            t.Interval = TimeSpan.FromMilliseconds(150)
            def _retry(s, e):
                t.Stop()
                try:
                    w = self.VideoArea.ActualWidth
                    pt = self.VideoArea.PointToScreen(Point(max(0, w - 302), 10))
                    self._map_popup.HorizontalOffset = pt.X
                    self._map_popup.VerticalOffset   = pt.Y
                except Exception:
                    pass
            t.Tick += _retry
            t.Start()

    def _close_map_overlay(self):
        self._map_popup.IsOpen = False

    def _on_drag_start(self, sender, e):
        self._dragging = True
        self._drag_start_offset_x = self._map_popup.HorizontalOffset
        self._drag_start_offset_y = self._map_popup.VerticalOffset
        local_pt = e.GetPosition(sender)
        screen_pt = sender.PointToScreen(Point(local_pt.X, local_pt.Y))
        self._drag_start_screen_x = screen_pt.X
        self._drag_start_screen_y = screen_pt.Y
        sender.CaptureMouse()
        e.Handled = True

    def _on_drag_move(self, sender, e):
        if not self._dragging:
            return
        local_pt  = e.GetPosition(sender)
        screen_pt = sender.PointToScreen(Point(local_pt.X, local_pt.Y))
        self._map_popup.HorizontalOffset = (
            self._drag_start_offset_x + screen_pt.X - self._drag_start_screen_x)
        self._map_popup.VerticalOffset = (
            self._drag_start_offset_y + screen_pt.Y - self._drag_start_screen_y)

    def _on_drag_end(self, sender, e):
        self._dragging = False
        sender.ReleaseMouseCapture()
        e.Handled = True

    # ── Tree ──────────────────────────────────────────────────────────────────

    def _refresh_tree(self):
        self.VideoFileTree.Items.Clear()
        map_page = self._get_map_page() if self._get_map_page else None
        if not map_page or not map_page._files:
            placeholder = TreeViewItem()
            placeholder.Header = self._make_lbl(
                "Load GPX files on the Map tab first",
                color=_CLR_MUTED, size=11.0)
            placeholder.IsEnabled = False
            self.VideoFileTree.Items.Add(placeholder)
            return

        for fi, f in enumerate(map_page._files):
            file_item            = TreeViewItem()
            file_item.Tag        = f"{fi},-1,-1"
            file_item.FontWeight = FontWeights.SemiBold
            file_item.Foreground = _CLR_DARK
            file_item.FontSize   = 12.0
            file_item.IsExpanded = True
            file_item.Header     = self._make_lbl(f["filename"])

            for ti, track in enumerate(f["tracks"]):
                name = track.name or f"Track {ti + 1}"
                n    = len(track.points)
                track_item          = TreeViewItem()
                track_item.Tag      = f"{fi},{ti},-1"
                track_item.FontSize = 12.0
                track_item.Header   = self._make_lbl(
                    f"  {name}  ({n} pts)", color=_CLR_MUTED)

                split_idx = self._get_split_idx(map_page, fi, ti)
                if split_idx >= 0:
                    track_item.IsExpanded = True
                    pts_all = [{"lat": p.lat, "lon": p.lon,
                                "ele": p.ele, "time": p.time}
                               for p in track.points]
                    n1 = split_idx + 1
                    n2 = n - n1
                    seg1 = TreeViewItem()
                    seg1.Tag    = f"{fi},{ti},0"
                    seg1.Header = self._make_lbl(
                        f"    Segment 1  ({n1} pts)", color=_CLR_SEL)
                    seg2 = TreeViewItem()
                    seg2.Tag    = f"{fi},{ti},1"
                    seg2.Header = self._make_lbl(
                        f"    Segment 2  ({n2} pts)", color=_CLR_SEL)
                    track_item.Items.Add(seg1)
                    track_item.Items.Add(seg2)

                file_item.Items.Add(track_item)

            self.VideoFileTree.Items.Add(file_item)

    @staticmethod
    def _get_split_idx(map_page, fi, ti):
        try:
            if map_page._active == (fi, ti):
                raw = map_page.MapBrowser.InvokeScript("getSplitIdx")
                return int(str(raw)) if raw is not None else -1
        except Exception:
            pass
        return -1

    @staticmethod
    def _make_lbl(text, color=None, size=12.0):
        tb           = TextBlock()
        tb.Text      = text
        tb.FontSize  = size
        tb.Foreground = color or _CLR_DARK
        tb.VerticalAlignment = VerticalAlignment.Center
        return tb

    def _on_tree_selected(self, _s, _e):
        item = self.VideoFileTree.SelectedItem
        if item is None:
            return
        tag = str(item.Tag) if item.Tag is not None else ""
        parts = tag.split(",")
        if len(parts) < 3:
            return
        try:
            fi, ti, seg = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            return
        if ti == -1:
            return

        map_page = self._get_map_page() if self._get_map_page else None
        if not map_page:
            return
        try:
            track = map_page._files[fi]["tracks"][ti]
        except (IndexError, KeyError):
            return

        pts_all = [{"lat": p.lat, "lon": p.lon, "ele": p.ele, "time": p.time}
                   for p in track.points]

        if seg == -1:
            pts  = pts_all
            name = track.name or f"Track {ti + 1}"
        else:
            split_idx = self._get_split_idx(map_page, fi, ti)
            if split_idx >= 0:
                pts = pts_all[:split_idx + 1] if seg == 0 \
                      else pts_all[split_idx + 1:]
            else:
                pts = pts_all
            name = f"{track.name or 'Track'} – Segment {seg + 1}"

        self._active_pts    = pts
        self._active_name   = name
        self._cached_speeds = []
        self._update_info_label()
        self._push_track_to_map()
        self._full_rebuild_tl()

    def _push_track_to_map(self):
        if not self._map_loaded or not self._active_pts:
            return
        try:
            self._map_browser.InvokeScript(
                "loadTrack", Array[Object]([json.dumps(self._active_pts)]))
        except Exception:
            pass

    # ── Video player ──────────────────────────────────────────────────────────

    def _on_open_video(self, _s, _e):
        dlg = OpenFileDialog()
        dlg.Title  = "Open Video File"
        dlg.Filter = ("Video files (*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.m4v)"
                      "|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.m4v"
                      "|All files (*.*)|*.*")
        if not dlg.ShowDialog():
            return
        self._video_path   = str(dlg.FileName)
        self._is_playing   = False
        self._duration_ms  = 0.0
        self._video_pos_ms = 0.0
        self.TxtPlayPause.Text          = "▶"
        self.TxtNoVideo.Visibility      = Visibility.Collapsed
        self.VideoPlayer.Source         = Uri(self._video_path)
        self.VideoPlayer.Play()
        self.VideoPlayer.Pause()

    def _on_media_opened(self, _s, _e):
        dur = self.VideoPlayer.NaturalDuration
        self._duration_ms = dur.TimeSpan.TotalMilliseconds if dur.HasTimeSpan else 0.0
        self.LblVideoDuration.Text = _fmt_ms(self._duration_ms)
        try:
            self._fps = max(1.0, float(self.TxtFPS.Text.strip()))
        except Exception:
            self._fps = 30.0
        self._update_info_label()
        self._full_rebuild_tl()

    def _on_media_ended(self, _s, _e):
        self._is_playing       = False
        self.TxtPlayPause.Text = "▶"

    # ── Playback controls ─────────────────────────────────────────────────────

    def _on_play_pause(self, _s, _e):
        if not self._video_path:
            return
        if self._is_playing:
            self.VideoPlayer.Pause()
            self._is_playing       = False
            self.TxtPlayPause.Text = "▶"
        else:
            self.VideoPlayer.Play()
            self._is_playing       = True
            self.TxtPlayPause.Text = "⏸"

    def _step_frame(self, direction):
        if not self._video_path:
            return
        if self._is_playing:
            self.VideoPlayer.Pause()
            self._is_playing       = False
            self.TxtPlayPause.Text = "▶"
        frame_ms = 1000.0 / self._fps
        self._seek(self._video_pos_ms + direction * frame_ms)

    def _seek_relative(self, delta_ms):
        if not self._video_path:
            return
        self._seek(self._video_pos_ms + delta_ms)

    def _seek(self, target_ms):
        target_ms = max(0.0, min(self._duration_ms, float(target_ms)))
        self.VideoPlayer.Position = TimeSpan.FromTicks(int(target_ms * 10000))
        self._video_pos_ms = target_ms
        self._update_time_label()
        self._update_playhead()
        self._update_map_for_pos(target_ms)
        self._update_overlay_visibility()

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _on_timer(self, _s, _e):
        if not self._video_path or not self._is_playing:
            return
        self._video_pos_ms = self.VideoPlayer.Position.TotalMilliseconds
        self._update_time_label()
        self._update_playhead()
        self._update_map_for_pos(self._video_pos_ms)
        self._update_overlay_visibility()

    def _update_time_label(self):
        self.LblVideoTime.Text = _fmt_ms(self._video_pos_ms)

    # ── Track info label ──────────────────────────────────────────────────────

    def _update_info_label(self):
        if not self._active_pts:
            self.LblSyncStatus.Foreground = _CLR_MUTED
            self.LblSyncStatus.Text = "Select a track"
            return
        first_e = self._gpx_first_epoch()
        if first_e is None:
            self.LblSyncStatus.Text = self._active_name or "Track loaded"
            self.LblSyncStatus.Foreground = _CLR_MUTED
            return
        from datetime import datetime, timezone
        dt  = datetime.fromtimestamp(first_e / 1000, tz=timezone.utc)
        dur = self._gpx_duration_ms()
        self.LblSyncStatus.Foreground = _CLR_SEL
        self.LblSyncStatus.Text = (
            f"{self._active_name}  ·  "
            f"{dt.strftime('%d %b %Y  %H:%M:%S')} UTC  ·  "
            f"{_fmt_ms(dur)}")

    # ── Map / GPS position ────────────────────────────────────────────────────

    def _nearest_pt_for_video_ms(self, video_ms):
        first_e = self._gpx_first_epoch()
        if first_e is None:
            return None
        target_epoch = first_e + video_ms
        best, best_d = None, float('inf')
        for pt in self._active_pts:
            e = _epoch_ms(pt.get('time'))
            if e is None:
                continue
            d = abs(e - target_epoch)
            if d < best_d:
                best_d = d
                best   = pt
        return best

    def _update_map_for_pos(self, video_ms):
        if not self._map_loaded:
            return
        pt = self._nearest_pt_for_video_ms(video_ms)
        if pt is None:
            return
        try:
            self._map_browser.InvokeScript(
                "setPosition",
                Array[Object]([str(pt['lat']), str(pt['lon'])]))
        except Exception:
            pass

    # ── Timeline helpers ──────────────────────────────────────────────────────

    def _gpx_first_epoch(self):
        for pt in self._active_pts:
            e = _epoch_ms(pt.get('time'))
            if e is not None:
                return e
        return None

    def _gpx_duration_ms(self):
        first = last = None
        for pt in self._active_pts:
            e = _epoch_ms(pt.get('time'))
            if e is None:
                continue
            if first is None or e < first:
                first = e
            if last is None or e > last:
                last = e
        if first is None or last is None:
            return 0.0
        return last - first

    _LEFT_MARGIN = 36   # px reserved on left for Y-axis labels

    def _data_w(self, canvas_w):
        return max(1.0, canvas_w - self._LEFT_MARGIN)

    def _tl_x_for_epoch(self, epoch_ms, canvas_w):
        first_e  = self._gpx_first_epoch()
        total_ms = self._tl_axis_ms()
        if first_e is None or total_ms <= 0:
            return float(self._LEFT_MARGIN)
        return self._LEFT_MARGIN + (epoch_ms - first_e) / total_ms * self._data_w(canvas_w)

    def _tl_axis_ms(self):
        gpx_dur = self._gpx_duration_ms()
        if self._duration_ms > 0 and gpx_dur > 0:
            return max(self._duration_ms, gpx_dur)
        return self._duration_ms if self._duration_ms > 0 else gpx_dur

    # ── Timeline canvas ───────────────────────────────────────────────────────

    def _on_timeline_resize(self, _s, _e):
        w = self.TimelineCanvas.ActualWidth
        if abs(w - self._tl_width_last) > 1:
            self._tl_width_last = w
            self._full_rebuild_tl()

    def _full_rebuild_tl(self):
        """Rebuild all timeline layers in Z-order."""
        self._rebuild_tl_video_bar()
        self._rebuild_tl_speed()
        self._rebuild_tl_yaxis()
        self._rebuild_tl_ruler()
        self._rebuild_tl_overlays()
        self._ensure_playhead()

    def _remove_elems(self, lst):
        for el in lst:
            try:
                self.TimelineCanvas.Children.Remove(el)
            except Exception:
                pass
        lst.clear()

    # ── Layout constants ──────────────────────────────────────────────────────
    # From bottom: 14px ruler | 2px gap | 10px video bar | 3px gap | 14px overlay bars | 4px gap | speed chart
    _RULER_H     = 14
    _BAR_H       = 10
    _BAR_GAP     = 2
    _OV_BAR_H    = 14   # text overlay bars row
    _OV_BAR_GAP  = 3    # gap between video bar and overlay bars
    _BAR_TOP_GAP = 4    # gap between overlay bars and speed graph

    def _graph_h(self, canvas_h):
        return max(0, canvas_h - self._RULER_H - self._BAR_GAP - self._BAR_H
                   - self._OV_BAR_GAP - self._OV_BAR_H - self._BAR_TOP_GAP)

    def _bar_top(self, canvas_h):
        return max(0, canvas_h - self._RULER_H - self._BAR_GAP - self._BAR_H)

    def _ov_bar_top(self, canvas_h):
        return max(0, canvas_h - self._RULER_H - self._BAR_GAP - self._BAR_H
                   - self._OV_BAR_GAP - self._OV_BAR_H)

    # ── Layer 0: video-length bar ─────────────────────────────────────────────

    def _rebuild_tl_video_bar(self):
        from System.Windows.Shapes import Rectangle as WpfRect
        self._remove_elems(self._tl_video_bar)

        w = self.TimelineCanvas.ActualWidth
        h = self.TimelineCanvas.ActualHeight
        if w <= 0 or h <= 0 or self._duration_ms <= 0:
            return
        total_ms = self._tl_axis_ms()
        if total_ms <= 0:
            return

        bar_w = self._duration_ms / total_ms * self._data_w(w)
        bar_t = self._bar_top(h)

        r = WpfRect()
        r.RadiusX        = 5
        r.RadiusY        = 5
        r.Fill           = _CLR_VIDEO_FILL
        r.Stroke         = _CLR_VIDEO_STR
        r.StrokeThickness = 1.0
        r.Width          = max(4, bar_w)
        r.Height         = self._BAR_H
        Canvas.SetLeft(r, self._LEFT_MARGIN)
        Canvas.SetTop(r, bar_t)
        self.TimelineCanvas.Children.Add(r)
        self._tl_video_bar.append(r)

    # ── Layer 1: speed graph ──────────────────────────────────────────────────

    def _rebuild_tl_speed(self):
        self._remove_elems(self._tl_speed)

        w = self.TimelineCanvas.ActualWidth
        h = self.TimelineCanvas.ActualHeight
        if w <= 0 or h <= 0 or len(self._active_pts) < 2:
            return
        if self._tl_axis_ms() <= 0:
            return

        graph_h = self._graph_h(h)
        if graph_h <= 0:
            return

        if not self._cached_speeds:
            self._cached_speeds = self._compute_speeds(self._active_pts)
        speeds  = self._cached_speeds
        max_spd = max((s for s in speeds if s is not None), default=0)
        if max_spd <= 0:
            return

        n      = len(self._active_pts)
        WINDOW = max(1, n // 100)

        pts_top = []
        for i in range(0, n, WINDOW):
            chunk_spds = [speeds[j] for j in range(i, min(i + WINDOW, n))
                          if speeds[j] is not None]
            if not chunk_spds:
                continue
            mid = i + (min(i + WINDOW, n) - i) // 2
            e   = _epoch_ms(self._active_pts[mid].get('time'))
            if e is None:
                continue
            avg_spd = sum(chunk_spds) / len(chunk_spds)
            x = self._tl_x_for_epoch(e, w)
            y = graph_h - (avg_spd / max_spd) * graph_h * 0.85
            pts_top.append((x, y))

        if len(pts_top) < 2:
            return

        pts_all = pts_top + [(pts_top[-1][0], graph_h), (pts_top[0][0], graph_h)]
        pc = PointCollection()
        for x, y in pts_all:
            pc.Add(Point(x, y))

        poly = Polygon()
        poly.Points          = pc
        poly.Fill            = _CLR_SPEED_F
        poly.Stroke          = _CLR_SPEED_S
        poly.StrokeThickness = 1.0
        self.TimelineCanvas.Children.Add(poly)
        self._tl_speed.append(poly)

    # ── Layer 1b: Y-axis labels + grid lines ──────────────────────────────────

    @staticmethod
    def _nice_spd_step(max_spd):
        for c in (1, 2, 5, 10, 20, 25, 50, 100):
            if max_spd / c <= 5:
                return c
        return max(1, int(max_spd / 4))

    def _rebuild_tl_yaxis(self):
        from System.Windows.Shapes import Rectangle as WpfRect
        self._remove_elems(self._tl_yaxis)

        w = self.TimelineCanvas.ActualWidth
        h = self.TimelineCanvas.ActualHeight
        if w <= 0 or h <= 0 or not self._cached_speeds:
            return

        max_spd = max((s for s in self._cached_speeds if s is not None), default=0)
        if max_spd <= 0:
            return

        graph_h = self._graph_h(h)
        if graph_h <= 0:
            return

        step   = self._nice_spd_step(max_spd)
        data_w = self._data_w(w)

        bg = WpfRect()
        bg.Width  = self._LEFT_MARGIN
        bg.Height = graph_h
        bg.Fill   = _brush_a(220, 0xF8, 0xF8, 0xF8)
        Canvas.SetLeft(bg, 0)
        Canvas.SetTop(bg, 0)
        self.TimelineCanvas.Children.Add(bg)
        self._tl_yaxis.append(bg)

        spd = step
        while spd <= max_spd * 1.01:
            y = graph_h - (spd / max_spd) * graph_h * 0.85
            if y < 0:
                break

            gl = Line()
            gl.X1 = float(self._LEFT_MARGIN)
            gl.X2 = self._LEFT_MARGIN + data_w
            gl.Y1 = gl.Y2 = y
            gl.Stroke          = _brush_a(60, 0xA1, 0xA1, 0xAA)
            gl.StrokeThickness = 1.0
            self.TimelineCanvas.Children.Add(gl)
            self._tl_yaxis.append(gl)

            lbl            = TextBlock()
            lbl.Text       = f"{int(spd)}"
            lbl.FontSize   = 8.5
            lbl.Foreground = _CLR_TICK_LBL
            Canvas.SetLeft(lbl, 2)
            Canvas.SetTop(lbl, y - 6)
            self.TimelineCanvas.Children.Add(lbl)
            self._tl_yaxis.append(lbl)

            spd += step

        unit           = TextBlock()
        unit.Text      = "km/h"
        unit.FontSize  = 7.5
        unit.Foreground = _CLR_TICK_LBL
        Canvas.SetLeft(unit, 2)
        Canvas.SetTop(unit, 2)
        self.TimelineCanvas.Children.Add(unit)
        self._tl_yaxis.append(unit)

    # ── Layer 2: ruler ────────────────────────────────────────────────────────

    def _rebuild_tl_ruler(self):
        self._remove_elems(self._tl_ruler)

        w = self.TimelineCanvas.ActualWidth
        h = self.TimelineCanvas.ActualHeight
        axis_ms = self._tl_axis_ms()
        if w <= 0 or axis_ms <= 0:
            return

        tick_y1 = max(0, h - self._RULER_H)
        tick_y2 = max(0, h - 2)

        data_w = self._data_w(w)
        step = self._nice_step_ms(axis_ms, data_w)
        t = 0
        while t <= axis_ms + step * 0.5:
            x = self._LEFT_MARGIN + t / axis_ms * data_w

            tick = Line()
            tick.X1 = tick.X2 = x
            tick.Y1 = tick_y1
            tick.Y2 = tick_y2
            tick.Stroke          = _CLR_TICK
            tick.StrokeThickness = 1.0
            self.TimelineCanvas.Children.Add(tick)
            self._tl_ruler.append(tick)

            lbl = TextBlock()
            lbl.Text       = _fmt_ms(t)
            lbl.FontSize   = 9.0
            lbl.Foreground = _CLR_TICK_LBL
            Canvas.SetLeft(lbl, x + 2)
            Canvas.SetBottom(lbl, 2)
            self.TimelineCanvas.Children.Add(lbl)
            self._tl_ruler.append(lbl)

            t += step

    # ── Layer 3: text overlay bars ────────────────────────────────────────────

    def _rebuild_tl_overlays(self):
        from System.Windows.Shapes import Rectangle as WpfRect
        self._remove_elems(self._tl_overlays)
        self._ov_tl_rects.clear()

        w = self.TimelineCanvas.ActualWidth
        h = self.TimelineCanvas.ActualHeight
        if w <= 0 or h <= 0 or not self._overlays:
            return
        total_ms = self._tl_axis_ms()
        if total_ms <= 0:
            return

        ov_top = self._ov_bar_top(h)
        data_w = self._data_w(w)

        for ov in self._overlays:
            ov_id    = ov['id']
            r, g, b  = _OV_PALETTE[ov_id % len(_OV_PALETTE)]
            selected = (ov_id == self._sel_ov_id)

            x1 = self._LEFT_MARGIN + ov['start_ms'] / total_ms * data_w
            x2 = self._LEFT_MARGIN + ov['end_ms']   / total_ms * data_w
            bar_w = max(8.0, x2 - x1)

            self._ov_tl_rects[ov_id] = (x1, x1 + bar_w)

            # Main bar
            rect = WpfRect()
            rect.RadiusX         = 3
            rect.RadiusY         = 3
            rect.Fill            = _brush_a(200 if selected else 140, r, g, b)
            rect.Stroke          = _brush(r, g, b)
            rect.StrokeThickness = 2.0 if selected else 1.0
            rect.Width           = bar_w
            rect.Height          = self._OV_BAR_H
            Canvas.SetLeft(rect, x1)
            Canvas.SetTop(rect, ov_top)
            self.TimelineCanvas.Children.Add(rect)
            self._tl_overlays.append(rect)

            # Text label inside bar
            if bar_w > 24:
                lbl = TextBlock()
                lbl.Text              = ov['text']
                lbl.FontSize          = 9.0
                lbl.Foreground        = Brushes.White
                lbl.IsHitTestVisible  = False
                Canvas.SetLeft(lbl, x1 + 5)
                Canvas.SetTop(lbl, ov_top + 2)
                self.TimelineCanvas.Children.Add(lbl)
                self._tl_overlays.append(lbl)

            # Resize handles (left and right edges)
            for edge_x in (x1, x1 + bar_w - 4):
                handle = WpfRect()
                handle.Fill           = _brush_a(100, 255, 255, 255)
                handle.Width          = 4
                handle.Height         = self._OV_BAR_H
                handle.IsHitTestVisible = False
                Canvas.SetLeft(handle, edge_x)
                Canvas.SetTop(handle, ov_top)
                self.TimelineCanvas.Children.Add(handle)
                self._tl_overlays.append(handle)

            # Time labels shown when selected
            if selected:
                for val_ms, lx in ((ov['start_ms'], x1), (ov['end_ms'], x1 + bar_w)):
                    tlbl = TextBlock()
                    tlbl.Text             = _fmt_ms(val_ms)
                    tlbl.FontSize         = 8.0
                    tlbl.Foreground       = _brush(r, g, b)
                    tlbl.IsHitTestVisible = False
                    Canvas.SetLeft(tlbl, lx + 2)
                    Canvas.SetTop(tlbl, ov_top - 12)
                    self.TimelineCanvas.Children.Add(tlbl)
                    self._tl_overlays.append(tlbl)

    # ── Layer 4: playhead (always on top) ─────────────────────────────────────

    def _ensure_playhead(self):
        if self._tl_playhead is not None:
            try:
                self.TimelineCanvas.Children.Remove(self._tl_playhead)
            except Exception:
                pass
        else:
            self._tl_playhead = Line()
            self._tl_playhead.Stroke          = _CLR_HEAD
            self._tl_playhead.StrokeThickness = 2.0

        self._tl_playhead.Y1 = 0
        self._tl_playhead.Y2 = self.TimelineCanvas.ActualHeight or 180
        self.TimelineCanvas.Children.Add(self._tl_playhead)
        self._update_playhead()

    def _update_playhead(self):
        if self._tl_playhead is None:
            return
        w        = self.TimelineCanvas.ActualWidth
        h        = self.TimelineCanvas.ActualHeight or 180
        total_ms = self._tl_axis_ms()
        self._tl_playhead.Y2 = h
        if total_ms > 0 and w > 0:
            x = self._LEFT_MARGIN + self._video_pos_ms / total_ms * self._data_w(w)
            self._tl_playhead.X1 = x
            self._tl_playhead.X2 = x

    # ── Timeline mouse handlers ───────────────────────────────────────────────

    def _on_timeline_mousedown(self, sender, e):
        pos = e.GetPosition(self.TimelineCanvas)
        x, y = pos.X, pos.Y
        h    = self.TimelineCanvas.ActualHeight

        # Check overlay bar region first
        ov_top = self._ov_bar_top(h)
        if ov_top <= y <= ov_top + self._OV_BAR_H:
            self._handle_tl_ov_mousedown(x, sender, e)
            return

        # Deselect overlay when clicking outside bar region
        if self._sel_ov_id is not None:
            self._select_overlay(None)

        # Seek
        if not self._video_path or self._duration_ms <= 0:
            return
        w        = self.TimelineCanvas.ActualWidth
        total_ms = self._tl_axis_ms()
        if w <= 0 or total_ms <= 0:
            return
        click_x  = x - self._LEFT_MARGIN
        data_w   = self._data_w(w)
        video_ms = min(max(0.0, click_x) / data_w * total_ms, self._duration_ms)
        self._seek(video_ms)

    def _handle_tl_ov_mousedown(self, x, sender, e):
        EDGE = 6
        hit_id, mode = None, 'move'

        for ov_id, (x1, x2) in self._ov_tl_rects.items():
            if x1 <= x <= x2:
                hit_id = ov_id
                if x - x1 <= EDGE:
                    mode = 'left'
                elif x2 - x <= EDGE:
                    mode = 'right'
                break

        if hit_id is None:
            # Clicked in overlay row but not on any bar — deselect
            self._select_overlay(None)
            return

        self._select_overlay(hit_id)
        ov       = self._get_overlay(hit_id)
        total_ms = self._tl_axis_ms()
        w        = self.TimelineCanvas.ActualWidth
        data_w   = self._data_w(w)

        self._tl_ov_drag = {
            'id':       hit_id,
            'mode':     mode,
            'start_x':  x,
            'start_ms': ov['start_ms'],
            'end_ms':   ov['end_ms'],
            'total_ms': total_ms,
            'data_w':   data_w,
        }
        sender.CaptureMouse()
        e.Handled = True

    def _on_timeline_mousemove(self, sender, e):
        if self._tl_ov_drag is None:
            return
        pos  = e.GetPosition(self.TimelineCanvas)
        drag = self._tl_ov_drag
        dx_ms = (pos.X - drag['start_x']) / drag['data_w'] * drag['total_ms']

        ov = self._get_overlay(drag['id'])
        if ov is None:
            return

        mode = drag['mode']
        if mode == 'move':
            dur       = drag['end_ms'] - drag['start_ms']
            new_start = max(0.0, drag['start_ms'] + dx_ms)
            new_end   = new_start + dur
            if self._duration_ms > 0:
                if new_end > self._duration_ms:
                    new_end   = self._duration_ms
                    new_start = max(0.0, new_end - dur)
            ov['start_ms'] = new_start
            ov['end_ms']   = new_end
        elif mode == 'left':
            ov['start_ms'] = max(0.0, min(drag['end_ms'] - 200.0,
                                           drag['start_ms'] + dx_ms))
        elif mode == 'right':
            new_end = max(drag['start_ms'] + 200.0, drag['end_ms'] + dx_ms)
            if self._duration_ms > 0:
                new_end = min(new_end, self._duration_ms)
            ov['end_ms'] = new_end

        self._rebuild_tl_overlays()
        self._ensure_playhead()
        self._update_overlay_visibility()

    def _on_timeline_mouseup(self, sender, e):
        if self._tl_ov_drag is not None:
            sender.ReleaseMouseCapture()
            self._tl_ov_drag = None

    def _on_timeline_dblclick(self, sender, e):
        # Clear any drag state left by the first click of the double-click sequence
        self._tl_ov_drag = None
        try:
            sender.ReleaseMouseCapture()
        except Exception:
            pass

        pos = e.GetPosition(self.TimelineCanvas)
        x, y = pos.X, pos.Y
        h    = self.TimelineCanvas.ActualHeight

        ov_top = self._ov_bar_top(h)
        if not (ov_top <= y <= ov_top + self._OV_BAR_H):
            return

        for ov_id, (x1, x2) in self._ov_tl_rects.items():
            if x1 <= x <= x2:
                self._show_time_editor(ov_id)
                e.Handled = True
                return

    # ── Text overlay: video canvas ────────────────────────────────────────────

    def _on_add_overlay(self, _s, _e):
        ov_id    = self._next_ov_id
        self._next_ov_id += 1
        start_ms = max(0.0, self._video_pos_ms)
        end_ms   = start_ms + 5000.0
        if self._duration_ms > 0:
            end_ms = min(end_ms, self._duration_ms)
        ov = {
            'id':       ov_id,
            'text':     f"Text {ov_id + 1}",
            'x_frac':   0.05,
            'y_frac':   0.10,
            'start_ms': start_ms,
            'end_ms':   end_ms,
            'border':   None,
        }
        self._overlays.append(ov)
        self._create_ov_border(ov)
        self._select_overlay(ov_id)
        self._rebuild_tl_overlays()
        self._ensure_playhead()
        self._update_overlay_visibility()

    def _create_ov_border(self, ov):
        r, g, b = _OV_PALETTE[ov['id'] % len(_OV_PALETTE)]

        tb             = TextBlock()
        tb.Text        = ov['text']
        tb.FontSize    = 18.0
        tb.Foreground  = Brushes.White
        tb.Margin      = Thickness(10, 5, 10, 5)

        border                 = WPFBorder()
        border.Child           = tb
        border.Background      = _brush_a(160, 0, 0, 0)
        border.BorderBrush     = _brush(r, g, b)
        border.BorderThickness = Thickness(2.0)
        border.CornerRadius    = CornerRadius(5.0)
        border.Cursor          = Cursors.SizeAll
        border.Visibility      = Visibility.Collapsed

        ov_id = ov['id']
        border.MouseLeftButtonDown += lambda s, e, oid=ov_id: self._on_ov_mousedown(oid, s, e)
        border.MouseMove           += self._on_ov_mousemove
        border.MouseLeftButtonUp   += self._on_ov_mouseup

        self.OverlayCanvas.Children.Add(border)
        ov['border'] = border
        self._reposition_overlay(ov)

    def _reposition_overlay(self, ov):
        border = ov.get('border')
        if border is None:
            return
        w = self.OverlayCanvas.ActualWidth
        h = self.OverlayCanvas.ActualHeight
        if w <= 0 or h <= 0:
            return
        Canvas.SetLeft(border, ov['x_frac'] * w)
        Canvas.SetTop(border,  ov['y_frac'] * h)

    def _on_overlay_canvas_resized(self, _s, _e):
        for ov in self._overlays:
            self._reposition_overlay(ov)

    def _on_overlay_canvas_click(self, _s, _e):
        """Clicking empty canvas area deselects the current overlay."""
        self._select_overlay(None)

    def _on_ov_mousedown(self, ov_id, sender, e):
        self._select_overlay(ov_id)
        pos = e.GetPosition(self.OverlayCanvas)
        self._ov_drag_id          = ov_id
        self._ov_drag_start_mx    = pos.X
        self._ov_drag_start_my    = pos.Y
        ov = self._get_overlay(ov_id)
        self._ov_drag_start_xfrac = ov['x_frac']
        self._ov_drag_start_yfrac = ov['y_frac']
        sender.CaptureMouse()
        e.Handled = True

    def _on_ov_mousemove(self, sender, e):
        if self._ov_drag_id is None:
            return
        pos = e.GetPosition(self.OverlayCanvas)
        w = self.OverlayCanvas.ActualWidth
        h = self.OverlayCanvas.ActualHeight
        if w <= 0 or h <= 0:
            return
        ov = self._get_overlay(self._ov_drag_id)
        if ov is None:
            return
        ov['x_frac'] = max(0.0, min(0.95, self._ov_drag_start_xfrac
                                    + (pos.X - self._ov_drag_start_mx) / w))
        ov['y_frac'] = max(0.0, min(0.95, self._ov_drag_start_yfrac
                                    + (pos.Y - self._ov_drag_start_my) / h))
        self._reposition_overlay(ov)

    def _on_ov_mouseup(self, sender, e):
        if self._ov_drag_id is not None:
            sender.ReleaseMouseCapture()
            self._ov_drag_id = None
        e.Handled = True

    def _update_overlay_visibility(self):
        pos = self._video_pos_ms
        for ov in self._overlays:
            border = ov.get('border')
            if border is None:
                continue
            vis = (Visibility.Visible
                   if ov['start_ms'] <= pos <= ov['end_ms']
                   else Visibility.Collapsed)
            border.Visibility = vis

    # ── Text overlay: selection + edit bar ───────────────────────────────────

    def _get_overlay(self, ov_id):
        for ov in self._overlays:
            if ov['id'] == ov_id:
                return ov
        return None

    def _select_overlay(self, ov_id):
        self._sel_ov_id = ov_id
        for ov in self._overlays:
            self._update_ov_border_style(ov)
        if ov_id is None:
            self.OverlayEditBar.Visibility = Visibility.Collapsed
        else:
            ov = self._get_overlay(ov_id)
            if ov:
                self.OverlayTextBox.Text       = ov['text']
                self.OverlayEditBar.Visibility = Visibility.Visible
        self._rebuild_tl_overlays()
        self._ensure_playhead()

    def _update_ov_border_style(self, ov):
        border = ov.get('border')
        if border is None:
            return
        r, g, b  = _OV_PALETTE[ov['id'] % len(_OV_PALETTE)]
        selected = (ov['id'] == self._sel_ov_id)
        if selected:
            border.BorderThickness = Thickness(3.0)
            border.BorderBrush     = Brushes.White
        else:
            border.BorderThickness = Thickness(2.0)
            border.BorderBrush     = _brush(r, g, b)

    def _on_apply_overlay_text(self, _s, _e):
        ov = self._get_overlay(self._sel_ov_id)
        if ov is None:
            return
        ov['text'] = self.OverlayTextBox.Text
        border = ov.get('border')
        if border is not None and border.Child is not None:
            border.Child.Text = ov['text']
        self._rebuild_tl_overlays()
        self._ensure_playhead()

    def _on_overlay_textbox_keydown(self, _s, e):
        if e.Key == Key.Return:
            self._on_apply_overlay_text(None, None)

    def _on_delete_overlay(self, _s, _e):
        ov_id = self._sel_ov_id
        if ov_id is None:
            return
        ov = self._get_overlay(ov_id)
        if ov is None:
            return
        border = ov.get('border')
        if border is not None:
            try:
                self.OverlayCanvas.Children.Remove(border)
            except Exception:
                pass
        self._overlays = [o for o in self._overlays if o['id'] != ov_id]
        self._sel_ov_id = None
        self.OverlayEditBar.Visibility = Visibility.Collapsed
        self._rebuild_tl_overlays()
        self._ensure_playhead()

    # ── Time editor dialog ────────────────────────────────────────────────────

    def _show_time_editor(self, ov_id):
        from System.Windows import Window
        from System.Windows.Controls import StackPanel as SP

        ov = self._get_overlay(ov_id)
        if ov is None:
            return

        win = Window()
        win.Title                 = "Edit Overlay Time"
        win.SizeToContent         = SizeToContent.WidthAndHeight
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        win.ResizeMode            = ResizeMode.NoResize
        win.Topmost               = True
        win.Background            = _brush_a(255, 0xF4, 0xF4, 0xF5)
        try:
            win.Owner = Application.Current.MainWindow
        except Exception:
            pass

        outer = SP()
        outer.Orientation = Orientation.Vertical
        outer.Margin      = Thickness(20, 16, 20, 16)

        def _row(label_text, value_ms):
            row = SP()
            row.Orientation = Orientation.Horizontal
            row.Margin      = Thickness(0, 0, 0, 10)
            lbl = WPFLabel()
            lbl.Content = label_text
            lbl.Width   = 140
            lbl.VerticalContentAlignment = VerticalAlignment.Center
            tb = WPFTextBox()
            tb.Text    = _fmt_ms(value_ms)
            tb.Width   = 110
            tb.Padding = Thickness(6, 4, 6, 4)
            tb.FontFamily = "Consolas"
            row.Children.Add(lbl)
            row.Children.Add(tb)
            return row, tb

        row_s, tb_start = _row("Start (m:ss.mmm):", ov['start_ms'])
        row_e, tb_end   = _row("End   (m:ss.mmm):", ov['end_ms'])
        outer.Children.Add(row_s)
        outer.Children.Add(row_e)

        btn_row = SP()
        btn_row.Orientation        = Orientation.Horizontal
        btn_row.HorizontalAlignment = HorizontalAlignment.Right

        btn_ok = WPFButton()
        btn_ok.Content   = "OK"
        btn_ok.Width     = 72
        btn_ok.IsDefault = True

        btn_cancel = WPFButton()
        btn_cancel.Content  = "Cancel"
        btn_cancel.Width    = 72
        btn_cancel.Margin   = Thickness(8, 0, 0, 0)
        btn_cancel.IsCancel = True

        btn_row.Children.Add(btn_ok)
        btn_row.Children.Add(btn_cancel)
        outer.Children.Add(btn_row)

        win.Content = outer

        confirmed = [False]

        def _ok(s, e):
            confirmed[0] = True
            win.Close()

        def _cancel(s, e):
            win.Close()

        btn_ok.Click     += _ok
        btn_cancel.Click += _cancel

        win.ShowDialog()

        if confirmed[0]:
            new_start = _parse_ms(tb_start.Text)
            new_end   = _parse_ms(tb_end.Text)
            if (new_start is not None and new_end is not None
                    and new_end > new_start):
                ov['start_ms'] = max(0.0, new_start)
                ov['end_ms']   = (min(new_end, self._duration_ms)
                                  if self._duration_ms > 0 else new_end)
                self._rebuild_tl_overlays()
                self._ensure_playhead()
                self._update_overlay_visibility()

    # ── GPS / speed helpers ───────────────────────────────────────────────────

    @staticmethod
    def _haversine_m(lat1, lon1, lat2, lon2):
        R = 6_371_000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi   = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    def _compute_speeds(cls, pts):
        n = len(pts)
        if n == 0:
            return []
        speeds = [None] * n
        for i in range(1, n):
            t1 = _epoch_ms(pts[i - 1].get('time'))
            t2 = _epoch_ms(pts[i].get('time'))
            if t1 is None or t2 is None or t2 <= t1:
                continue
            d_m  = cls._haversine_m(pts[i-1]['lat'], pts[i-1]['lon'],
                                     pts[i]['lat'],   pts[i]['lon'])
            dt_h = (t2 - t1) / 3_600_000.0
            if dt_h > 0:
                speeds[i] = d_m / 1000.0 / dt_h
        if speeds[0] is None and n > 1:
            speeds[0] = speeds[1]
        return speeds

    @staticmethod
    def _nice_step_ms(total_ms, canvas_w):
        candidates = [
            100, 250, 500,
            1_000, 2_000, 5_000, 10_000, 15_000, 30_000,
            60_000, 120_000, 300_000, 600_000
        ]
        px_per_ms = canvas_w / total_ms if total_ms > 0 else 1
        for c in candidates:
            if c * px_per_ms >= 70:
                return c
        return candidates[-1]
