import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System.Windows.Media import SolidColorBrush, Color, Brushes
import wpf
import os

from ui.pages.page_map import MapPage


def _rgb(r, g, b):
    return SolidColorBrush(Color.FromRgb(r, g, b))


class MainWindow:
    def __init__(self):
        wpf.LoadComponent(self, os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_window.xaml"))

        self._tab_borders = [self.BtnMap]
        self._tab_texts   = [self.BtnMapText]
        self._pages       = [MapPage]

        self._bind_tabs()
        self._navigate(0)

    def _bind_tabs(self):
        for i, border in enumerate(self._tab_borders):
            idx = i
            border.MouseLeftButtonDown += lambda s, e, i=idx: self._navigate(i)

    def _navigate(self, tab_idx):
        for i, (border, text) in enumerate(zip(self._tab_borders, self._tab_texts)):
            if i == tab_idx:
                border.Background = _rgb(250, 250, 250)
                text.Foreground   = _rgb(24, 24, 27)
            else:
                border.Background = Brushes.Transparent
                text.Foreground   = _rgb(161, 161, 170)

        self.MainFrame.Navigate(self._pages[tab_idx]()._wpf)
