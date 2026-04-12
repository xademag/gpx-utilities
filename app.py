import sys
import os
import winreg


def _set_ie_edge_mode():
    """Force WPF WebBrowser control to use IE11 Edge rendering (needed for Leaflet.js)."""
    exe = os.path.basename(sys.executable)
    key_path = r"SOFTWARE\Microsoft\Internet Explorer\Main\FeatureControl\FEATURE_BROWSER_EMULATION"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, exe, 0, winreg.REG_DWORD, 11001)
    except Exception:
        pass


_set_ie_edge_mode()

import pythonnet
pythonnet.load("netfx")  # Use .NET Framework — required for WPF on Windows

import clr

# Pre-load WPF assemblies from the GAC
_gac = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), r"Microsoft.NET\assembly")
clr.AddReference(os.path.join(_gac, r"GAC_MSIL\PresentationFramework\v4.0_4.0.0.0__31bf3856ad364e35\PresentationFramework.dll"))
clr.AddReference(os.path.join(_gac, r"GAC_64\PresentationCore\v4.0_4.0.0.0__31bf3856ad364e35\PresentationCore.dll"))
clr.AddReference(os.path.join(_gac, r"GAC_MSIL\WindowsBase\v4.0_4.0.0.0__31bf3856ad364e35\WindowsBase.dll"))

from System.Windows import Application
from System.Threading import Thread, ApartmentState, ThreadStart
from ui.main_window import MainWindow
from core import tile_server as _ts

# Start tile caching proxy before the WPF window (daemon thread — no cleanup needed)
_ts.start()


def run():
    app = Application()
    window = MainWindow()
    app.Run(window._wpf)


if __name__ == "__main__":
    # WPF requires an STA thread; Python's main thread is MTA.
    sta = Thread(ThreadStart(run))
    sta.SetApartmentState(ApartmentState.STA)
    sta.Start()
    sta.Join()
