"""Определение «воды» для демо-станций (Обь, озёра, каналы).

Вода = точка внутри любого полигона воды (water.json) ИЛИ внутри полосы
шириной HALF_WIDTH вокруг линии реки/канала (water_lines.json).
Данные скачаны из OpenStreetMap (Overpass), см. fetch_water.py /
fetch_water_lines.py.
"""
import json
import math
import os

import numpy as np
from matplotlib.path import Path

from app.core import tile_water

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# полуширина полосы вокруг линии реки в градусах (~ 300 м по широте)
HALF_WIDTH = 0.003

# зона станций (для отсечения лишних сегментов)
_ST_LAT0, _ST_LAT1, _ST_LON0, _ST_LON1 = 54.81, 55.16, 82.87, 83.21
_MARGIN = 0.01

_poly_paths = None
_poly_bbox = None  # (min_lat, max_lat, min_lon, max_lon) для каждого полигона
_seg = None  # (lat1, lon1, lat2, lon2) массивы


def _build_segments():
    """Разбивает сохранённые линии на сегменты, оставляет только близкие к станциям."""
    line_path = os.path.join(_DATA_DIR, "water_lines.json")
    if not os.path.exists(line_path):
        return None
    with open(line_path, encoding="utf-8") as f:
        lines = json.load(f)
    segs = []
    for line in lines:
        pts = line["pts"]
        for (lat1, lon1), (lat2, lon2) in zip(pts, pts[1:]):
            mid_lat = (lat1 + lat2) / 2
            mid_lon = (lon1 + lon2) / 2
            if (
                _ST_LAT0 - _MARGIN <= mid_lat <= _ST_LAT1 + _MARGIN
                and _ST_LON0 - _MARGIN <= mid_lon <= _ST_LON1 + _MARGIN
            ):
                segs.append((lat1, lon1, lat2, lon2))
    if not segs:
        return None
    arr = np.asarray(segs, dtype=float)
    return arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]


def _load():
    global _poly_paths, _poly_bbox, _seg
    if _poly_paths is not None:
        return
    _poly_paths = []
    _poly_bbox = []
    poly_path = os.path.join(_DATA_DIR, "water.json")
    if os.path.exists(poly_path):
        with open(poly_path, encoding="utf-8") as f:
            polys = json.load(f)
        for p in polys:
            if len(p) < 4:
                continue
            _poly_paths.append(Path(np.array([(lon, lat) for lat, lon in p])))
            lats = [pt[0] for pt in p]
            lons = [pt[1] for pt in p]
            _poly_bbox.append((min(lats), max(lats), min(lons), max(lons)))
    _seg = _build_segments()


def _vector_water(lat: float, lon: float) -> bool:
    """Только векторная проверка (полигоны и полосы рек/каналов)."""
    _load()
    for bbox, path in zip(_poly_bbox, _poly_paths):
        if not (bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3]):
            continue
        if path.contains_point((lon, lat)):
            return True
    if _seg is not None:
        lat1, lon1, lat2, lon2 = _seg
        dx = lat2 - lat1
        dy = lon2 - lon1
        l2 = dx * dx + dy * dy
        t = ((lat - lat1) * dx + (lon - lon1) * dy) / np.maximum(l2, 1e-12)
        t = np.clip(t, 0.0, 1.0)
        projx = lat1 + t * dx
        projy = lon1 + t * dy
        if np.hypot(lat - projx, lon - projy).min() < HALF_WIDTH:
            return True
    return False


def is_in_water(lat: float, lon: float) -> bool:
    """True, если точка (lat, lon) попала на воду.

    Вода = векторная маска (полигоны + полосы) ИЛИ цвет OSM-тайла
    (то, что реально видно на карте).
    """
    if _vector_water(lat, lon):
        return True
    vw = tile_water.visual_water(lat, lon)
    return bool(vw) if vw is not None else False


def is_wet_point(lat: float, lon: float) -> bool:
    """Точка считается «мокрой», если она на воде или вплотную к ней
    (в пределах ~1 пикселя маски/зума 13, т.е. ~10 м) — для генерации,
    чтобы станции не «прилипали» к синей кромке на карте."""
    if is_in_water(lat, lon):
        return True
    vn = tile_water.visual_water_neigh(lat, lon, radius=1)
    return bool(vn) if vn is not None else False


def nearest_land(lat: float, lon: float, max_r: float = 0.02):
    """Возвращает ближайшую сушу вокруг точки (lat, lon) или None, если не нашли.

    Поиск веером: концентрические кольца с 32 направлениями, засчитывается
    самая близкая сухая точка (по фактическому расстоянию в метрах ~).
    """
    if not is_wet_point(lat, lon):
        return float(lat), float(lon)
    coslat = math.cos(math.radians(lat))
    best = None
    best_d2 = math.inf
    radii = np.linspace(0.0004, max_r, 32)
    for r in radii:
        for ang in range(32):
            a = ang * (2 * math.pi / 32)
            clat = lat + r * math.sin(a) / coslat
            clon = lon + r * math.cos(a)
            # суша с запасом ~10 м от «синей кромки» карты
            if not is_wet_point(clat, clon):
                dx = (clat - lat) * coslat
                dy = clon - lon
                d2 = dx * dx + dy * dy
                if d2 < best_d2:
                    best_d2 = d2
                    best = (float(clat), float(clon))
        # как только найдена суша и радиус кольца уже значимый — дальше можно не искать
        if best is not None and r >= 0.004:
            break
    return best


def find_land_near(lat: float, lon: float, max_r: float = 0.012):
    """Ищет ближайшую сушу вокруг точки (если и сама точка, и старт на воде)."""
    lp = nearest_land(lat, lon, max_r=max_r)
    if lp is None:
        return float(lat), float(lon)
    return lp


def snap_to_land(old_lat, old_lon, new_lat, new_lon):
    """Если новая точка на воде — возвращает последнюю сушу на отрезке old→new.

    Возвращаем точку lo (последний гарантированно сухой параметр), а не середину
    интервала: середина может оказаться на «водной» стороне границы и всё равно
    классифицироваться как вода, из-за чего станция застревает в воде.
    """
    if not is_in_water(new_lat, new_lon):
        return float(new_lat), float(new_lon)
    if is_in_water(old_lat, old_lon):
        return find_land_near(new_lat, new_lon)
    lo, hi = 0.0, 1.0
    lo_lat, lo_lon = old_lat, old_lon
    for _ in range(25):
        mid = (lo + hi) / 2
        mlat = old_lat + (new_lat - old_lat) * mid
        mlon = old_lon + (new_lon - old_lon) * mid
        if is_in_water(mlat, mlon):
            hi = mid
        else:
            lo = mid
            lo_lat, lo_lon = mlat, mlon
    return float(lo_lat), float(lo_lon)


def random_land_point(rng: np.random.Generator, low, high, tries: int = 60):
    """Генерирует случайную точку в прямоугольнике low..high.

    Если точка попала на воду — переносит её на ближайшую сушу.
    """
    for _ in range(tries):
        p = rng.uniform(low=low, high=high)
        if is_wet_point(float(p[0]), float(p[1])):
            lp = nearest_land(float(p[0]), float(p[1]))
            if lp is None:
                continue
            p = np.asarray(lp)
        return p
    return high


def land_points_from(rng, low, high, count: int):
    """Список из count сухопутных точек в прямоугольнике.

    Координаты, попавшие на воду, переносятся на ближайшую сушу
    (а не перегенерируются), чтобы разброс станций сохранялся.
    """
    out = []
    while len(out) < count:
        p = rng.uniform(low=low, high=high)
        if is_wet_point(float(p[0]), float(p[1])):
            lp = nearest_land(float(p[0]), float(p[1]))
            if lp is None:
                continue
            p = np.asarray(lp)
        out.append(p)
    return np.asarray(out, dtype=float)


# прогрев кэша маски воды по цвету: при закешированном water_mask.npz это
# мгновенно; в свежем окружении (без кэша и с сетью) один раз скачает тайлы.
# Обёрнуто в try, чтобы отсутствие сети не роняло импорт.
try:
    tile_water.ensure_mask()
except Exception:
    pass