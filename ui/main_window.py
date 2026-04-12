import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System.Windows import FontWeights, Visibility
from System.Windows.Media import SolidColorBrush, Color, Brushes
from System import Action
from System.Windows.Threading import DispatcherPriority
import wpf
import os
import json
import threading

from ui.pages.page_map   import MapPage
from ui.pages.page_route import RoutePage
from core import settings as settings_mod
from core import tile_server as tile_server_mod


def _rgb(r, g, b):
    return SolidColorBrush(Color.FromRgb(r, g, b))


class MainWindow:
    def __init__(self):
        wpf.LoadComponent(self, os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_window.xaml"))

        self._settings = settings_mod.load()

        self._tab_borders  = [self.BtnMap,     self.BtnRoute]
        self._tab_texts    = [self.BtnMapText, self.BtnRouteText]
        self._page_classes = [MapPage,         RoutePage]
        self._page_cache   = {}   # tab_idx → page instance
        self._active_tab   = 0

        self._bind_tabs()
        self._navigate(0)

        # Popup placement targets (so popups reposition with the window)
        self.SettingsPanel.PlacementTarget  = self.BtnSettings
        self.HelpPanel.PlacementTarget      = self.BtnHelp
        self.DownloadPanel.PlacementTarget  = self.BtnDownload

        # Close all popups when window is minimized; reposition when window moves
        self._wpf.StateChanged    += self._on_window_state_changed
        self._wpf.LocationChanged += self._reposition_popups
        self._wpf.SizeChanged     += self._reposition_popups

        # Header icon buttons
        self.BtnSettings.MouseLeftButtonDown      += self._toggle_settings
        self.BtnHelp.MouseLeftButtonDown          += self._toggle_help
        self.BtnDownload.MouseLeftButtonDown      += self._toggle_download
        self.BtnCloseSettings.MouseLeftButtonDown += lambda s, e: self._close_panel(self.SettingsPanel)
        self.BtnCloseHelp.MouseLeftButtonDown     += lambda s, e: self._close_panel(self.HelpPanel)
        self.BtnCloseDownload.MouseLeftButtonDown += lambda s, e: self._close_panel(self.DownloadPanel)
        self.BtnSaveMapView.MouseLeftButtonDown   += self._save_map_view

        # Download panel controls
        self.TxtDlZoomTo.TextChanged              += self._on_dl_zoom_changed
        self.BtnStartDownload.MouseLeftButtonDown += self._on_dl_start
        self.BtnClearCache.MouseLeftButtonDown    += self._on_dl_clear_cache

        # Download state
        self._dl_bounds  = None
        self._dl_running = False
        self._dl_cancel  = None

        # Settings panel — font size pills
        self.BtnFontSmall.MouseLeftButtonDown  += lambda s, e: self._set_font_size('small')
        self.BtnFontMedium.MouseLeftButtonDown += lambda s, e: self._set_font_size('medium')
        self.BtnFontLarge.MouseLeftButtonDown  += lambda s, e: self._set_font_size('large')

        # Arrow-every-N text box
        self.TxtArrowEveryN.LostFocus     += self._on_arrow_n_changed
        self.TxtArrowEveryN.KeyDown       += self._on_arrow_n_key

        # Map style tiles
        self.BtnStyleMap.MouseLeftButtonDown    += lambda s, e: self._set_map_style('map')
        self.BtnStyleRelief.MouseLeftButtonDown += lambda s, e: self._set_map_style('relief')
        self.BtnStyleSat.MouseLeftButtonDown    += lambda s, e: self._set_map_style('satellite')
        self.BtnStyleHybrid.MouseLeftButtonDown += lambda s, e: self._set_map_style('hybrid')


        # Reflect persisted settings in the UI
        self._apply_font_pills(self._settings.get('font_size', 'medium'))
        self.TxtArrowEveryN.Text = str(self._settings.get('arrow_every_n', 20))
        self._apply_style_pills(self._settings.get('map_style', 'map'))

    def _on_window_state_changed(self, _s, _e):
        from System.Windows import WindowState
        if self._wpf.WindowState == WindowState.Minimized:
            self._close_all_panels()

    def _reposition_popups(self, _s, _e):
        for panel in (self.SettingsPanel, self.HelpPanel, self.DownloadPanel):
            if panel.IsOpen:
                off = panel.HorizontalOffset
                panel.HorizontalOffset = off + 1
                panel.HorizontalOffset = off

    # ── Tab navigation ────────────────────────────────────────────────────────

    def _bind_tabs(self):
        for i, border in enumerate(self._tab_borders):
            border.MouseLeftButtonDown += lambda s, e, i=i: self._navigate(i)

    def _navigate(self, tab_idx):
        self._active_tab = tab_idx
        for i, (border, text) in enumerate(zip(self._tab_borders, self._tab_texts)):
            active = (i == tab_idx)
            border.Background = _rgb(250, 250, 250) if active else Brushes.Transparent
            text.Foreground   = _rgb(24, 24, 27)    if active else _rgb(161, 161, 170)

        if tab_idx not in self._page_cache:
            self._page_cache[tab_idx] = self._page_classes[tab_idx](
                self._settings, self._on_settings_changed)

        self.MainFrame.Navigate(self._page_cache[tab_idx]._wpf)

    # ── Settings popup ────────────────────────────────────────────────────────

    def _toggle_settings(self, _s, _e):
        is_open = self.SettingsPanel.IsOpen
        self._close_all_panels()
        if not is_open:
            self._refresh_settings_display()
            self.SettingsPanel.IsOpen = True

    def _toggle_help(self, _s, _e):
        is_open = self.HelpPanel.IsOpen
        self._close_all_panels()
        if not is_open:
            self.HelpPanel.IsOpen = True

    def _close_panel(self, panel):
        panel.IsOpen = False

    def _close_all_panels(self):
        self.SettingsPanel.IsOpen  = False
        self.HelpPanel.IsOpen      = False
        self.DownloadPanel.IsOpen  = False

    def _refresh_settings_display(self):
        s = self._settings
        lat  = s.get('map_lat',  48.8566)
        lon  = s.get('map_lon',   2.3522)
        zoom = s.get('map_zoom',  5)
        self.LblSettingsCoords.Text  = f"{lat:.4f}°,  {lon:.4f}°"
        self.LblSettingsZoom.Text    = f"Zoom {zoom}"
        self.TxtArrowEveryN.Text     = str(s.get('arrow_every_n', 20))
        self._apply_font_pills(s.get('font_size', 'medium'))
        self._apply_style_pills(s.get('map_style', 'map'))

    def _save_map_view(self, _s, _e):
        try:
            page = self._page_cache.get(self._active_tab)
            if page is None:
                return
            raw = page.MapBrowser.InvokeScript('getMapView')
            if not raw:
                return
            data = json.loads(str(raw))
            self._settings['map_lat']  = data['lat']
            self._settings['map_lon']  = data['lon']
            self._settings['map_zoom'] = int(data['zoom'])
            settings_mod.save(self._settings)
            self._refresh_settings_display()
        except Exception:
            pass

    def _apply_font_pills(self, active_size):
        pills = {
            'small':  (self.BtnFontSmall,  self.TxtFontSmall),
            'medium': (self.BtnFontMedium, self.TxtFontMedium),
            'large':  (self.BtnFontLarge,  self.TxtFontLarge),
        }
        for key, (border, txt) in pills.items():
            active = (key == active_size)
            border.Background    = _rgb(24, 24, 27)    if active else Brushes.Transparent
            border.BorderBrush   = _rgb(24, 24, 27)    if active else _rgb(228, 228, 231)
            from System.Windows import Thickness
            border.BorderThickness = Thickness(0) if active else Thickness(1)
            txt.Foreground = Brushes.White if active else _rgb(113, 113, 122)

    # ── Font size change ──────────────────────────────────────────────────────

    def _set_font_size(self, size):
        self._settings['font_size'] = size
        settings_mod.save(self._settings)
        self._apply_font_pills(size)
        # Apply to all cached pages immediately
        for page in self._page_cache.values():
            page.apply_settings(self._settings)

    # ── Map style (tile layer) ────────────────────────────────────────────────

    def _set_map_style(self, style):
        self._settings['map_style'] = style
        settings_mod.save(self._settings)
        self._apply_style_pills(style)
        for page in self._page_cache.values():
            page.apply_settings(self._settings)

    def _apply_style_pills(self, active_style):
        from System.Windows import Thickness
        pills = {
            'map':       (self.BtnStyleMap,    self.TxtStyleMap),
            'relief':    (self.BtnStyleRelief,  self.TxtStyleRelief),
            'satellite': (self.BtnStyleSat,     self.TxtStyleSat),
            'hybrid':    (self.BtnStyleHybrid,  self.TxtStyleHybrid),
        }
        for key, (border, txt) in pills.items():
            active = (key == active_style)
            border.BorderBrush     = _rgb(37, 99, 235)  if active else _rgb(228, 228, 231)
            border.BorderThickness = Thickness(2)        if active else Thickness(1)
            txt.FontWeight         = FontWeights.SemiBold if active else FontWeights.Normal
            txt.Foreground         = _rgb(24, 24, 27)    if active else _rgb(113, 113, 122)

    # ── Track arrows setting ──────────────────────────────────────────────────

    def _on_arrow_n_key(self, _s, e):
        from System.Windows.Input import Key
        if e.Key == Key.Return or e.Key == Key.Enter:
            self._apply_arrow_n()

    def _on_arrow_n_changed(self, _s, _e):
        self._apply_arrow_n()

    def _apply_arrow_n(self):
        try:
            n = int(self.TxtArrowEveryN.Text.strip())
            n = max(0, n)
        except Exception:
            n = self._settings.get('arrow_every_n', 20)
        self.TxtArrowEveryN.Text = str(n)
        if self._settings.get('arrow_every_n') == n:
            return
        self._settings['arrow_every_n'] = n
        settings_mod.save(self._settings)
        for page in self._page_cache.values():
            page.apply_settings(self._settings)

    # ── Download tiles popup ──────────────────────────────────────────────────

    def _toggle_download(self, _s, _e):
        is_open = self.DownloadPanel.IsOpen
        self._close_all_panels()
        if not is_open:
            self._open_download_panel()

    def _open_download_panel(self):
        page = self._page_cache.get(self._active_tab)
        self._dl_bounds = None
        if page:
            try:
                raw = page.MapBrowser.InvokeScript('getMapBounds')
                self._dl_bounds = json.loads(str(raw))
                z = int(self._dl_bounds.get('zoom', 5))
                self.LblDlZoomFrom.Text = f"Z{z}"
                default_max = min(z + 3, 16)
                self.TxtDlZoomTo.Text = str(default_max)
                n = self._dl_bounds
                self.LblDlBounds.Text = (
                    f"N {n['north']:.4f}°  S {n['south']:.4f}°\n"
                    f"E {n['east']:.4f}°  W {n['west']:.4f}°"
                )
            except Exception:
                self.LblDlBounds.Text = "No map view available"

        style_labels = {'map': 'Map', 'relief': 'Relief',
                        'satellite': 'Satellite', 'hybrid': 'Hybrid'}
        self.LblDlStyle.Text = style_labels.get(
            self._settings.get('map_style', 'map'), 'Map')

        self._update_dl_count()
        self._refresh_dl_cache_size()
        self.DownloadPanel.IsOpen = True

    def _on_dl_zoom_changed(self, _s, _e):
        self._update_dl_count()

    def _update_dl_count(self):
        if not self._dl_bounds:
            self.LblDlCount.Text = "No area selected"
            return
        try:
            z_min = int(self._dl_bounds.get('zoom', 5))
            z_max = int(self.TxtDlZoomTo.Text.strip())
            z_max = max(z_min, min(18, z_max))
            n  = tile_server_mod.get().count_tiles(self._dl_bounds, z_min, z_max)
            mb = n * 0.015
            self.LblDlCount.Text = f"~{n:,} tiles · ~{mb:.0f} MB estimated"
        except Exception:
            self.LblDlCount.Text = ""

    def _refresh_dl_cache_size(self):
        try:
            mb = tile_server_mod.get().cache_size_mb()
            self.LblDlCacheSize.Text = f"Cache: {mb:.1f} MB on disk"
        except Exception:
            self.LblDlCacheSize.Text = ""

    def _on_dl_start(self, _s, _e):
        if self._dl_running:
            # Cancel current download
            if self._dl_cancel:
                self._dl_cancel.set()
            return

        if not self._dl_bounds:
            return

        try:
            z_min = int(self._dl_bounds.get('zoom', 5))
            z_max = int(self.TxtDlZoomTo.Text.strip())
            z_max = max(z_min, min(18, z_max))
        except Exception:
            return

        # Map style → server style key
        style_map = {'map': 'map', 'relief': 'relief',
                     'satellite': 'satellite', 'hybrid': 'hybrid'}
        style = style_map.get(self._settings.get('map_style', 'map'), 'map')

        self._dl_cancel  = threading.Event()
        self._dl_running = True
        self.TxtDlBtn.Text            = "Cancel"
        self.BtnStartDownload.Background = _rgb(220, 38, 38)
        self.PnlDlProgress.Visibility = Visibility.Visible
        self.PbDownload.Value         = 0
        self.LblDlProgress.Text       = "Starting…"

        def _on_progress(done, total):
            def _update():
                if not self.DownloadPanel.IsOpen:
                    return
                pct = (done / total * 100.0) if total > 0 else 0
                self.PbDownload.Value   = pct
                self.LblDlProgress.Text = f"{done:,} / {total:,} tiles"
                if done >= total:
                    self._finish_download()
            self.DownloadPanel.Dispatcher.BeginInvoke(
                DispatcherPriority.Normal, Action(_update))

        def _run():
            tile_server_mod.get().download_area(
                style, self._dl_bounds, z_min, z_max,
                on_progress=_on_progress, cancel_ev=self._dl_cancel)
            self.DownloadPanel.Dispatcher.BeginInvoke(
                DispatcherPriority.Normal, Action(self._finish_download))

        threading.Thread(target=_run, daemon=True).start()

    def _finish_download(self):
        self._dl_running             = False
        self._dl_cancel              = None
        self.TxtDlBtn.Text            = "Download"
        self.BtnStartDownload.Background = _rgb(37, 99, 235)
        self._refresh_dl_cache_size()

    def _on_dl_clear_cache(self, _s, _e):
        try:
            tile_server_mod.get().clear_cache()
        except Exception:
            pass
        self._refresh_dl_cache_size()

    # ── Callback from pages when map location is saved ───────────────────────

    def _on_settings_changed(self, new_settings):
        self._settings = new_settings
        # Refresh display if popup is open
        if self.SettingsPanel.IsOpen:
            self._refresh_settings_display()
