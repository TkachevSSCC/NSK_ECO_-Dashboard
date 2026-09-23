"""Проверка «воды» по цвету OSM-тайлов — то, что реально видно на карте.

Вода в стиле OSM-standard рисуется цветом ~ rgb(170, 211, 223).
Модуль строит один раз растровую маску для зоны станций (zoom 13,
пиксель ~11 м) из тайлов tile.openstreetmap.org, кэширует её в
app/data/water_mask.npz вместе с параметрами проекции, и дальше отвечает
на любой вопрос мгновенно (точная проекция Web-Mercator/z13).
"""
import math
import os
import threading
import urllib.request

import matplotlib.image as mpimg
import numpy as np

# ---- настройки ----
ZOOM = 13
WATER_RGB = (170, 211, 223)
TOL = 22  # допуск по каждому каналу

# зона станций + запас (только для выбора тайлов; итоговые границы маски —
# привязываются к сетке тайлов и сохраняются в npz)
_MASK_LAT0, _MASK_LAT1 = 54.78, 55.17
_MASK_LON0, _MASK_LON1 = 82.86, 83.22

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_TILES_DIR = os.path.join(_DATA_DIR, "tiles")
_MASK_PATH = os.path.join(_DATA_DIR, "water_mask.npz")

_UA = "nsk-eco-dashboard/dev (air-monitoring demo) contact=local"

_N = 2 ** ZOOM

_mask = None       # np.ndarray bool: row 0 = юг, col 0 = запад
_meta = None       # (x0, x1, y0, y1) — индексы тайлов, покрывающих маску
_mask_cached = False
_lock = threading.Lock()


def _yf(lat):
    lat_rad = math.radians(lat)
    return (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * _N


def _tile_xy(lat, lon, z=ZOOM):
    return int((lon + 180.0) / 360.0 * _N), int(_yf(lat))


def _is_water_pixel(rgb):
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return (
        abs(r - WATER_RGB[0]) <= TOL
        and abs(g - WATER_RGB[1]) <= TOL
        and abs(b - WATER_RGB[2]) <= TOL
    )


def _load_tile(tx, ty):
    """Возвращает uint8 (256,256,3) тайла; при неудаче — None."""
    path = os.path.join(_TILES_DIR, f"z{ZOOM}", f"{tx}-{ty}.png")
    if not os.path.exists(path):
        url = f"https://tile.openstreetmap.org/{ZOOM}/{tx}/{ty}.png"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
        except Exception:
            return None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        except Exception:
            pass
    try:
        img = mpimg.imread(path)
        if img.ndim == 3 and img.shape[2] == 4:
            img = img[:, :, :3]
        if img.dtype != "uint8":
            img = (img * 255).astype(np.uint8)
        return img
    except Exception:
        return None


def _water_pixels(img):
    return np.all(
        np.abs(img.astype(int) - np.array(WATER_RGB)) <= TOL,
        axis=-1,
    )


def _build_mask():
    """Скачивает тайлы зоны и собирает маску воды (row 0 = юг)."""
    x0, y0 = _tile_xy(_MASK_LAT1, _MASK_LON0)
    x1, _ = _tile_xy(_MASK_LAT1, _MASK_LON1)
    _, y1 = _tile_xy(_MASK_LAT0, _MASK_LON0)
    if x1 < x0 or y1 < y0:
        return None

    cols = (x1 - x0 + 1) * 256
    rows = (y1 - y0 + 1) * 256
    mask = np.zeros((rows, cols), dtype=np.uint8)
    got = total = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            total += 1
            img = _load_tile(tx, ty)
            if img is None:
                continue
            got += 1
            # маска хранится «юг-сверху» (row 0 = самая южная строка),
            # тайлы укладываются вверх-ногами (flipud), как требует _row_col
            r0 = (y1 - ty) * 256
            c0 = (tx - x0) * 256
            mask[r0 : r0 + 256, c0 : c0 + 256] = np.flipud(_water_pixels(img))
    if got < total * 0.7:
        return None  # слишком много тайлов не скачалось — маска нерепрезентативна
    np.savez_compressed(_MASK_PATH, mask=mask.astype(bool), x0=x0, x1=x1, y0=y0, y1=y1)
    return mask.astype(bool), (int(x0), int(x1), int(y0), int(y1))


def ensure_mask():
    """Гарантирует наличие маски (из кэша или строится при первом вызове)."""
    global _mask, _meta, _mask_cached
    if _mask_cached:
        return _mask
    with _lock:
        if _mask_cached:
            return _mask
        try:
            if os.path.exists(_MASK_PATH):
                with np.load(_MASK_PATH) as data:
                    _mask = data["mask"]
                    _meta = (
                        int(data["x0"]), int(data["x1"]), int(data["y0"]), int(data["y1"]),
                    )
            else:
                _mask, _meta = _build_mask()
        except Exception:
            _mask = None
            _meta = None
        _mask_cached = True
    return _mask


def _row_col(lat, lon):
    """Индексы в маске для точки (lat, lon) или None, если вне покрытия.

    Маска построена так: row 0 = самая южная строка (y1-тайл, его нижняя
    строка), col 0 = самая западная. Тайлы уложены с flipud, поэтому
    строка внутри тайла читается «снизу вверх».
    """
    if _mask is None or _meta is None:
        return None
    x0, x1, y0, y1 = _meta
    xf = (lon + 180.0) / 360.0 * _N
    yf = _yf(lat)
    tx, ty = int(xf), int(yf)
    if not (x0 <= tx <= x1 and y0 <= ty <= y1):
        return None
    px = int((xf - tx) * 256)
    py = int((yf - ty) * 256)
    col = (tx - x0) * 256 + px
    row = (y1 - ty) * 256 + (255 - py)
    if not (0 <= row < _mask.shape[0] and 0 <= col < _mask.shape[1]):
        return None
    return row, col


def visual_water(lat: float, lon: float):
    """True/False если маска есть, None если маски нет (нет определения)."""
    ensure_mask()
    rc = _row_col(lat, lon)
    if rc is None:
        return None
    row, col = rc
    return bool(_mask[row, col])


def visual_water_neigh(lat: float, lon: float, radius=2):
    """True если рядом (в радиусе radius пикселей зума ZOOM) есть вода."""
    ensure_mask()
    rc = _row_col(lat, lon)
    if rc is None:
        return None
    row, col = rc
    r0, r1 = max(0, row - radius), min(_mask.shape[0] - 1, row + radius)
    c0, c1 = max(0, col - radius), min(_mask.shape[1] - 1, col + radius)
    return bool(_mask[r0 : r1 + 1, c0 : c1 + 1].any())