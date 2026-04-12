# GPX Utility

A Windows desktop application for viewing, editing, and planning GPS tracks. Built with Python + WPF via pythonnet.

---

## Features

### Map Page
- Load **GPX** and **Garmin FIT** files (multiple files simultaneously)
- Interactive **Leaflet.js** map with OpenStreetMap tiles
- Per-track visibility toggle (eye icon)
- Click a track point to inspect coordinates, elevation, speed, and cumulative distance
- Right-click a track point to anchor a selection range, then crop, delete, or split at a point via context menu
- Edit points directly on the map; save the modified track as a new GPX file
- **Measurement toolbox** (drag to reposition):
  - 📏 **Distance** — click vertices, right-click to finish; shows running total
  - ⬡ **Area** — click polygon corners, right-click to close; calculates spherical area

### Route Planner
- Click the map to add waypoints; drag markers to adjust
- Right-click a marker to remove it
- **Per-segment routing modes** — click the connector between two waypoints to cycle:
  - 🚶 **Foot** — pedestrian routing via OpenStreetMap
  - 🚴 **Bike** — cycling routing via OpenStreetMap
  - 🚗 **Car** — driving routing via OSRM
  - ⟶ **Direct** — straight line between points (no network request)
  - ✏ **Draw** — freehand: click to add vertices, right-click to finish segment
- Shows total route distance and estimated duration
- Export the planned route as a GPX file
- Same **measurement toolbox** as the Map page

### Settings (⚙ header button)
- **Font size** — Small / Medium / Large (scales the left panel)
- **Default map location** — displays saved lat/lon/zoom; click **📍 Save current map view** to capture the current view from whichever page is active
- Settings persist to `settings.json` between sessions

---

## Requirements

| Component | Version |
|-----------|---------|
| Windows | 10 or 11 (64-bit) |
| .NET Framework | 4.8 (pre-installed on Windows 10 v1903+ and Windows 11) |
| Python | 3.11 or newer |
| pythonnet | 3.x (`netfx` runtime) |
| fitparse | any |
| Internet | Required on first launch for map tiles (OpenStreetMap CDN) |

---

## Running from source

```bash
# Clone / copy the repo
cd "c:\Front Ends\gpx-utility"

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Launch
python app.py
```

> **Note**: `app.py` must be run with a standard CPython interpreter (not IronPython). pythonnet's `netfx` mode loads the .NET Framework CLR at startup and requires an STA thread for WPF — both handled automatically in `app.py`.

---

## Building the portable package

`build_portable.py` creates a self-contained folder in `dist/GPX Utility/` that includes an embedded Python runtime and all dependencies. No Python installation is required on the target machine.

```bash
python build_portable.py
```

**What it does:**

1. Downloads **Python 3.11 embeddable** (64-bit) to a temp cache
2. Patches the `._pth` file to enable `site-packages`
3. Installs `pip`, then `pythonnet`, `fitparse`, and `cffi` into the embedded runtime
4. Copies all application source files and assets
5. Produces `dist/gpx-utility-portable.zip` (the distributable archive)

**Requirements for building:** Python 3.x with internet access (one-time download ~30 MB).

### Creating a Windows installer (optional)

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then:

```bash
# After running build_portable.py:
iscc installer\gpx_utility.iss
```

This produces `dist\gpx-utility-setup.exe` — a standard Windows installer with Start Menu and optional Desktop shortcut.

---

## Project structure

```
gpx-utility/
├── app.py                   Entry point — configures IE rendering, loads WPF STA thread
├── wpf.py                   XAML loader shim (XamlReader + attribute binding)
├── requirements.txt
├── settings.json            Persisted user preferences (auto-created on first run)
│
├── core/
│   ├── models.py            GPXPoint, GPXTrack data classes
│   ├── gpx_parser.py        GPX 1.0/1.1 parser, track stats, GPX writer
│   ├── fit_parser.py        Garmin FIT parser (via fitparse); returns GPXTrack objects
│   └── settings.py          Load/save settings.json; font-scale helper
│
├── ui/
│   ├── main_window.py       Main window controller
│   ├── main_window.xaml     Header bar, tab navigation, Settings & Help popups
│   └── pages/
│       ├── page_map.py      Map page controller
│       ├── page_map.xaml    Map page layout (left panel + WebBrowser)
│       ├── page_route.py    Route planner controller
│       └── page_route.xaml  Route planner layout
│
├── assets/
│   ├── map.html             Leaflet.js map for the Map page
│   └── route.html           Leaflet.js map for the Route Planner
│
├── build_portable.py        Portable distribution builder
└── installer/
    └── gpx_utility.iss      Inno Setup installer script
```

---

## Architecture notes

**WPF + Python bridge**
The app runs CPython with pythonnet's `netfx` mode (.NET Framework 4.8). WPF requires an STA (Single-Threaded Apartment) thread; `app.py` creates one explicitly. The custom `wpf.py` shim uses `XamlReader.Load()` to parse XAML and binds every named element as a Python attribute — this avoids `SetValue` failures that arise when subclassing WPF types via pythonnet.

**WebBrowser ↔ Python communication**
WPF's `WebBrowser` control hosts the Leaflet.js maps. Because `WebBrowser` is HWND-based (Win32 airspace), it always renders on top of WPF content — hence Settings and Help panels use WPF `Popup` controls (own HWND, above WebBrowser). Communication between JS and Python uses a polling queue: Python calls `InvokeScript("dequeueCallback")` every 150–200 ms; JS pushes events as `"action|{json}"` strings.

**Routing**
OSRM is used for Foot, Bike, and Car routing. `router.project-osrm.org` only serves driving regardless of the URL profile; foot and bike use `routing.openstreetmap.de` which runs separate OSRM instances per mode.

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Esc | Close context menu / modal |
| Enter | Confirm modal dialog |
| Shift+click | Extend point selection (Map page) |
| Right-click on map | Finish draw segment / finish measurement |
| Right-click on waypoint | Remove waypoint (Route Planner) |
