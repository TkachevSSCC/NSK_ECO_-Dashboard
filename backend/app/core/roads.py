"""Дорожная сеть региона: координаты улиц из OpenStreetMap.

Данные лежат в app/data/roads.json (их кладёт fetch_roads.py, сам модуль
ничего не качает и от сети не зависит). Модуль отвечает на три вопроса:

  * snap_to_road(lat, lon) — привязать точку к ближайшей улице
    (так устройство, поставленное кликом, оказывается на дороге);
  * random_road_point(rng) — случайная точка улицы (генерация устройств);
  * road_route(lat, lon, forward) — маршрут вдоль улицы, по которому
    едет движущееся устройство (см. tasks/update_stations.py).

Маршрут — это ломаная из координат ведущих узлов улицы: устройство
движется по ней интерполяцией, то есть всегда остаётся на дороге.

Если roads.json нет или он битый, все функции возвращают None/исходную
точку, и станции ездят по старой круговой орбите — интерфейс и
симуляция продолжают работать.
"""
import bisect
import json
import math
import os
import threading

# ---- геометрия ----
# градусы в метры. Долготу переводим в метры по широте центра региона:
# весь граф лежит в пределах ~40 км, точности хватает с большим запасом,
# а считать тригонометрию на каждую точку в цикле не нужно.
_M_PER_DEG = 111_320.0
_CENTER_LAT = 54.985
_M_PER_DEG_LON = _M_PER_DEG * math.cos(math.radians(_CENTER_LAT))

# ячейка индекса для поиска ближайшей улицы, градусы (~1.1 км по широте)
_CELL = 0.01
# с какой точностью считать совпадение концов улиц, градусы (~1 м)
_NODE_PRECISION = 5
# насколько далеко искать соседние ячейки, если в своей ничего нет
_RING_SEARCH = 4

# маршрут короче этого не строим: короткий цикл «туда-обратно» заметно
# уступает длинному, а с 350 м за тик 1 м/с устройство проходит его за 6 минут
_MIN_ROUTE_M = 350.0
ROAD_MIN_ROUTE_M = _MIN_ROUTE_M  # публичное имя для других модулей
# сколько улиц максимум склеиваем в один маршрут
_MAX_ROUTE_WAYS = 4

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_ROADS_PATH = os.path.join(_DATA_DIR, "roads.json")

_net = None          # RoadNetwork | None
_net_loaded = False  # пробовали ли читать файл (чтобы не читать каждый раз)
_lock = threading.Lock()


def _to_xy(lat: float, lon: float):
    return ((lon) * _M_PER_DEG_LON, (lat) * _M_PER_DEG)


def _cum_lengths(xy):
    """Накопленные расстояния (м) до каждой точки полилины."""
    cum = [0.0]
    total = 0.0
    for i in range(1, len(xy)):
        dx = xy[i][0] - xy[i - 1][0]
        dy = xy[i][1] - xy[i - 1][1]
        total += math.hypot(dx, dy)
        cum.append(total)
    return cum


def _node_key(lat: float, lon: float):
    return (round(lat, _NODE_PRECISION), round(lon, _NODE_PRECISION))


class RoadNetwork:
    """Полилины улиц + индексы для быстрых пространственных запросов."""

    def __init__(self, polylines, bbox=None):
        self.bbox = bbox
        self.polylines = []   # [(lat, lon), ...]
        self.cum = []         # [[м, ...], ...] накопительные расстояния
        self.lengths = []     # [м, ...] длина каждой улицы
        self._grid = {}       # (cell_lat, cell_lon) -> [индексы улиц]
        self._ends = {}       # (lat, lon) -> [индексы улиц с этим концом]

        for pts in polylines:
            if len(pts) < 2:
                continue
            poly = []
            for lat, lon in pts:
                lat = float(lat)
                lon = float(lon)
                if poly and poly[-1] == (lat, lon):
                    continue
                poly.append((lat, lon))
            if len(poly) < 2:
                continue
            idx = len(self.polylines)
            self.polylines.append(poly)
            cum = _cum_lengths([_to_xy(lat, lon) for lat, lon in poly])
            self.cum.append(cum)
            self.lengths.append(cum[-1])
            self._index(idx, poly)

        self._xy = [
            [_to_xy(lat, lon) for lat, lon in poly] for poly in self.polylines
        ]
        # кумулятивная сумма длин — для выбора случайной улицы по весу
        self._length_prefix = []
        acc = 0.0
        for length in self.lengths:
            acc += length
            self._length_prefix.append(acc)
        self.total_length = acc

    # ---- построение индексов ----

    def _cells_of(self, poly):
        lats = [p[0] for p in poly]
        lons = [p[1] for p in poly]
        c_lat0 = int(min(lats) // _CELL)
        c_lat1 = int(max(lats) // _CELL)
        c_lon0 = int(min(lons) // _CELL)
        c_lon1 = int(max(lons) // _CELL)
        return c_lat0, c_lat1, c_lon0, c_lon1

    def _index(self, idx: int, poly):
        c_lat0, c_lat1, c_lon0, c_lon1 = self._cells_of(poly)
        for clat in range(c_lat0, c_lat1 + 1):
            for clon in range(c_lon0, c_lon1 + 1):
                self._grid.setdefault((clat, clon), []).append(idx)
        # оба конца улицы: по ним маршрут продлевается на соседние улицы
        for end in (poly[0], poly[-1]):
            key = _node_key(*end)
            bucket = self._ends.setdefault(key, [])
            if idx not in bucket:
                bucket.append(idx)

    # ---- поиск ----

    def _candidates(self, lat: float, lon: float):
        """Индексы улиц рядом с точкой: своя ячейка и соседние кольца."""
        base = (int(lat // _CELL), int(lon // _CELL))
        found = []
        for ring in range(_RING_SEARCH + 1):
            found = []
            for dlat in range(-ring, ring + 1):
                for dlon in range(-ring, ring + 1):
                    if ring > 0 and abs(dlat) != ring and abs(dlon) != ring:
                        continue  # кольцо, а не квадрат
                    bucket = self._grid.get((base[0] + dlat, base[1] + dlon))
                    if bucket:
                        found.extend(bucket)
            if found:
                return found
        return found

    def nearest(self, lat: float, lon: float):
        """Ближайшая точка уличной сети.

        Возвращает (широта, долгота, расстояние_м, индекс_улицы,
        индекс_точки_в_полилине) или None, если рядом улиц нет.
        """
        candidates = self._candidates(lat, lon)
        if not candidates:
            return None
        qx = lon * _M_PER_DEG_LON
        qy = lat * _M_PER_DEG
        best = None  # (d2, way_idx, point_idx, t)
        for way_idx in set(candidates):
            xy = self._xy[way_idx]
            for i in range(len(xy) - 1):
                ax, ay = xy[i]
                bx, by = xy[i + 1]
                # расстояние от точки до отрезка в локальной плоскости (м)
                dx = bx - ax
                dy = by - ay
                seg2 = dx * dx + dy * dy
                if seg2 <= 0.0:
                    t = 0.0
                else:
                    t = ((qx - ax) * dx + (qy - ay) * dy) / seg2
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                px = ax + dx * t
                py = ay + dy * t
                d2 = (px - qx) ** 2 + (py - qy) ** 2
                if best is None or d2 < best[0]:
                    # индекс точки, на которой устройство окажется: к
                    # ближайшему концу отрезка, если t около нуля/единицы
                    point_idx = i if t < 0.5 else i + 1
                    best = (d2, way_idx, point_idx, t)
        if best is None:
            return None
        d2, way_idx, point_idx, _t = best
        poly = self.polylines[way_idx]
        lat_out, lon_out = poly[point_idx]
        return (lat_out, lon_out, math.sqrt(d2), way_idx, point_idx)

    def point_at(self, way_idx: int, dist_m: float):
        """Точка на расстоянии dist_m от начала улицы (интерполяцией)."""
        cum = self.cum[way_idx]
        dist_m = max(0.0, min(dist_m, cum[-1]))
        i = bisect.bisect_left(cum, dist_m)
        if i <= 0:
            return self.polylines[way_idx][0]
        if i >= len(cum):
            return self.polylines[way_idx][-1]
        seg = cum[i] - cum[i - 1]
        t = 0.0 if seg <= 0 else (dist_m - cum[i - 1]) / seg
        (lat0, lon0), (lat1, lon1) = (
            self.polylines[way_idx][i - 1],
            self.polylines[way_idx][i],
        )
        return (lat0 + (lat1 - lat0) * t, lon0 + (lon1 - lon0) * t)

    def random_point(self, rng):
        """Случайная точка сети: улица выбирается по её длине (длинные
        встречаются чаще — на них и устройств должно быть больше)."""
        if self.total_length <= 0:
            return None
        target = rng.random() * self.total_length
        way_idx = bisect.bisect_left(self._length_prefix, target)
        if way_idx >= len(self.polylines):
            way_idx = len(self.polylines) - 1
        before = self._length_prefix[way_idx - 1] if way_idx else 0.0
        return self.point_at(way_idx, target - before)

    def route(self, lat: float, lon: float, forward: bool = True):
        """Маршрут вдоль улиц, начинающийся в точке (lat, lon).

        1. находит ближайшую улицу и поворачивает её полилину так, чтобы
           началом был узел у исходной точки — устройство продолжит путь
           именно оттуда, где стоит;
        2. продлевает маршрут на соседние по концу улицы, пока не наберётся
           _MIN_ROUTE_M (иначе цикл «туда-обратно» слишком короткий).

        Возвращает (точки, накопительные_расстояния, длина_м) или None.
        """
        found = self.nearest(lat, lon)
        if found is None:
            return None
        _lat, _lon, _dist, way_idx, point_idx = found

        poly = self.polylines[way_idx]
        # поворот: от узла у станции до конца улицы, затем начало улицы
        # до него — получается маршрут без разрыва
        pts = list(poly[point_idx:]) + list(poly[:point_idx])
        if not forward:
            pts.reverse()
        used = {way_idx}

        xy = [_to_xy(la, lo) for la, lo in pts]
        length = _cum_lengths(xy)[-1] if len(xy) > 1 else 0.0

        hops = 1
        while length < _MIN_ROUTE_M and hops < _MAX_ROUTE_WAYS:
            tail = pts[-1]
            bucket = self._ends.get(_node_key(*tail)) or []
            # ищем ещё не использованную улицу, начинающуюся в хвосте
            picked = None
            nxt = None
            for cand in bucket:
                if cand in used:
                    continue
                cand_pts = self.polylines[cand]
                # продолжаем с любого конца соседней улицы
                if _node_key(*cand_pts[0]) == _node_key(*tail):
                    nxt = cand_pts[1:]
                elif _node_key(*cand_pts[-1]) == _node_key(*tail):
                    nxt = cand_pts[-2::-1]
                else:
                    continue
                picked = cand
                break
            if not picked or not nxt:
                break
            used.add(picked)
            pts.extend(nxt)
            length = _cum_lengths([_to_xy(la, lo) for la, lo in pts])[-1]
            hops += 1

        if len(pts) < 2:
            return None
        cum = _cum_lengths([_to_xy(la, lo) for la, lo in pts])
        return pts, cum, cum[-1]


# ---- публичные функции ----


def get_network():
    """Сеть дорог из кэша; None, если файла нет или он не читается."""
    global _net, _net_loaded
    if _net_loaded:
        return _net
    with _lock:
        if _net_loaded:
            return _net
        _net_loaded = True
        if not os.path.exists(_ROADS_PATH):
            return None
        try:
            with open(_ROADS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            _net = RoadNetwork(data.get("polylines") or [], data.get("bbox"))
        except Exception as exc:  # битый файл — работаем без дорог
            print(f"roads.json не читается ({exc}) — движение по орбитам")
            _net = None
        return _net


def is_ready() -> bool:
    """Есть ли пригодная сеть дорог."""
    net = get_network()
    return net is not None and net.total_length > 0


def snap_to_road(lat: float, lon: float):
    """Привязывает точку к ближайшей улице.

    Возвращает (широта, долгота, расстояние_м). Если сети нет, расстояние
    бесконечное, а координаты исходные — вызывающий код ничего не портит.
    """
    net = get_network()
    if net is None:
        return (lat, lon, float("inf"))
    found = net.nearest(lat, lon)
    if found is None:
        return (lat, lon, float("inf"))
    road_lat, road_lon, dist_m, _way, _pt = found
    return (road_lat, road_lon, dist_m)


def random_road_point(rng):
    """Случайная точка улицы или None, если сети нет."""
    net = get_network()
    if net is None:
        return None
    return net.random_point(rng)


def road_route(lat: float, lon: float, forward: bool = True):
    """Маршрут вдоль улиц от точки (lat, lon) или None."""
    net = get_network()
    if net is None:
        return None
    return net.route(lat, lon, forward=forward)


def status() -> dict:
    """Сводка по дорожной сети — для интерфейса и отладки."""
    net = get_network()
    if net is None:
        return {
            "status": "error",
            "message": f"Нет файла дорог: {os.path.basename(_ROADS_PATH)}",
        }
    return {
        "status": "ok",
        "streets": len(net.polylines),
        "points": sum(len(p) for p in net.polylines),
        "total_km": round(net.total_length / 1000.0, 1),
        "min_route_m": _MIN_ROUTE_M,
        "bbox": net.bbox,
    }


# публичный синоним (так его называют другие модули проекта)
road_status = status