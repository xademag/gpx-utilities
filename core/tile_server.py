import os
import math
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

_APP_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_APP_ROOT, 'tiles_cache')

# Remote URL templates — stored locally as {z}/{x}/{y}
# Esri URLs use {z}/{y}/{x} on the remote side; the handler substitutes correctly.
_REMOTE = {
    'map':            'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'relief':         'https://tile.opentopomap.org/{z}/{x}/{y}.png',
    'satellite':      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    'hybrid_overlay': 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
}

_UA = {'User-Agent': 'Mozilla/5.0 GPXUtility/1.0', 'Accept': 'image/png,image/*,*/*'}


def _remote_url(style, z, x, y):
    tmpl = _REMOTE.get(style)
    if not tmpl:
        return None
    return tmpl.replace('{z}', str(z)).replace('{x}', str(x)).replace('{y}', str(y))


def _tile_xy(lat, lon, z):
    """Convert lat/lon to tile x/y at zoom level z (Web Mercator)."""
    n   = 2 ** z
    tx  = int((lon + 180.0) / 360.0 * n)
    lr  = math.radians(max(-85.0511, min(85.0511, lat)))
    ty  = int((1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, tx)), max(0, min(n - 1, ty))


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Expected path: /tiles/{style}/{z}/{x}/{y}.png
        parts = self.path.strip('/').split('/')
        if len(parts) < 5 or parts[0] != 'tiles':
            self.send_error(404)
            return
        style = parts[1]
        z     = parts[2]
        x     = parts[3]
        y     = parts[4].split('.')[0].split('?')[0]   # strip .png / query

        local = os.path.join(_CACHE_DIR, style, z, x, y + '.png')

        if os.path.exists(local):
            with open(local, 'rb') as fh:
                data = fh.read()
        else:
            url = _remote_url(style, z, x, y)
            if not url:
                self.send_error(404)
                return
            try:
                req = urllib.request.Request(url, headers=_UA)
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = r.read()
                os.makedirs(os.path.dirname(local), exist_ok=True)
                with open(local, 'wb') as fh:
                    fh.write(data)
            except Exception:
                self.send_error(502)
                return

        self.send_response(200)
        self.send_header('Content-Type',  'image/png')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=604800')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass   # suppress console noise


# ── HTTP server — silence IE11 connection-reset noise ─────────────────────────

class _QuietHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError,
                            ConnectionAbortedError, OSError)):
            return   # IE11 frequently drops tile connections early — not an error
        super().handle_error(request, client_address)


# ── TileServer singleton ──────────────────────────────────────────────────────

class TileServer:
    def __init__(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        self._srv = None

    def start(self):
        self._srv = _QuietHTTPServer(('127.0.0.1', self.port), _Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        if self._srv:
            self._srv.shutdown()

    # ── Tile maths ────────────────────────────────────────────────────────────

    def count_tiles(self, bounds, z_min, z_max):
        total = 0
        for z in range(z_min, min(z_max, 18) + 1):
            x1, y1 = _tile_xy(bounds['north'], bounds['west'], z)
            x2, y2 = _tile_xy(bounds['south'], bounds['east'], z)
            total += (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1)
        return total

    def download_area(self, style, bounds, z_min, z_max,
                      on_progress=None, cancel_ev=None):
        """Download all tiles for a bounding box and zoom range."""
        # Hybrid needs both base satellite tiles AND the label overlay
        styles = ['satellite', 'hybrid_overlay'] if style == 'hybrid' else [style]

        tasks = []
        for s in styles:
            for z in range(z_min, min(z_max, 18) + 1):
                x1, y1 = _tile_xy(bounds['north'], bounds['west'], z)
                x2, y2 = _tile_xy(bounds['south'], bounds['east'], z)
                for x in range(min(x1, x2), max(x1, x2) + 1):
                    for y in range(min(y1, y2), max(y1, y2) + 1):
                        tasks.append((s, z, x, y))

        total = len(tasks)
        for i, (s, z, x, y) in enumerate(tasks):
            if cancel_ev and cancel_ev.is_set():
                break
            local = os.path.join(_CACHE_DIR, s, str(z), str(x), str(y) + '.png')
            if not os.path.exists(local):
                url = _remote_url(s, z, x, y)
                if url:
                    try:
                        req = urllib.request.Request(url, headers=_UA)
                        with urllib.request.urlopen(req, timeout=15) as r:
                            data = r.read()
                        os.makedirs(os.path.dirname(local), exist_ok=True)
                        with open(local, 'wb') as fh:
                            fh.write(data)
                    except Exception:
                        pass
            if on_progress:
                on_progress(i + 1, total)

    def cache_size_mb(self):
        total = 0
        if os.path.exists(_CACHE_DIR):
            for root, _, files in os.walk(_CACHE_DIR):
                for fn in files:
                    try:
                        total += os.path.getsize(os.path.join(root, fn))
                    except Exception:
                        pass
        return total / 1_048_576

    def clear_cache(self):
        import shutil
        if os.path.exists(_CACHE_DIR):
            shutil.rmtree(_CACHE_DIR)


# Module-level singleton — created at import, started explicitly by app.py
_server = TileServer()


def start():
    _server.start()
    return _server


def get() -> TileServer:
    return _server
