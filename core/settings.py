import json
import os

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'settings.json'
)

_DEFAULTS = {
    'font_size':    'medium',  # 'small' | 'medium' | 'large'
    'map_lat':      48.8566,
    'map_lon':       2.3522,
    'map_zoom':      5,
    'arrow_every_n': 20,       # draw direction arrows every N points (0 = off)
    'map_style':    'map',     # 'map' | 'relief' | 'satellite' | 'hybrid'
}

_FONT_SCALE = {
    'small':  0.82,
    'medium': 1.00,
    'large':  1.22,
}


def load() -> dict:
    try:
        with open(_PATH, encoding='utf-8') as f:
            return {**_DEFAULTS, **json.load(f)}
    except Exception:
        return dict(_DEFAULTS)


def save(data: dict) -> None:
    try:
        with open(_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def font_scale(data: dict) -> float:
    return _FONT_SCALE.get(data.get('font_size', 'medium'), 1.0)
