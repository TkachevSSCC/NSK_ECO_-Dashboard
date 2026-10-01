"""Дорожная сеть региона: координаты улиц из OpenStreetMap.

Данные лежат в app/data/roads.json (их кладёт fetch_roads.py, сам модуль
ничего не качает и от сети не зависит). Модуль отвечает на три вопроса:

  * snap_to_road(lat, lon) — привязать точку к ближайшей улице
    (так устройство, поставленное кликом, оказывается на дороге);
  * random_road_point(rng) — случайная точка улицы (генерация устройств);
  * road_route(lat, lon, forward) — маршрут вдоль улицы, по которому
    едет движущееся устройство (см. tasks/update_stations.py).

Маршрут — это ломаная из координат узлов улиц: устройство движется по ней
интерполяцией, то есть всегда остаётся на дороге. Длина маршрута набирается
переходами с улицы на улицу (в OSM пересечения не всегда общие узлы, поэтому
соседство ищем по расстоянию), типичный маршрут — около 6 км.

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
# насколько далеко искать соседние ячейки, если в своей ничего нет
_RING_SEARCH = 4

# маршрут короче этого не считаем дорожным — таких устройств немного, и для
# них остаётся запасная круговая орбита
_MIN_ROUTE_M = 350.0
ROAD_MIN_ROUTE_M = _MIN_ROUTE_M  # публичное имя для других модулей
# сколько метров стараемся набрать в маршрут: устройство должно ездить по
# городу, а не метаться между двумя соседними домами
_ROUTE_TARGET_M = 6000.0
ROUTE_TARGET_M = _ROUTE_TARGET_M  # публичное имя для других модулей
# сколько поворотов максимум делаем, строя маршрут
_MAX_ROUTE_TURNS = 64
# насколько близко конец улицы должен быть до другой улицы, чтобы считать
# это перекрёстком и продолжить по ней. В OSM пересечения не всегда являются
# общими узлами, поэтому соединяем по близости. Допуск намеренно небольшой:
# при 25 м и больше проход начинает сворачивать на вторую проезжую часть
# соседней улицы, и маршрут превращается в пинг-понг через дорогу.
_JUNCTION_TOL_M = 12.0
# с каким шагом идём по улице в поисках перекрёстка. Шаг заметно больше
# допуска: иначе проход сворачивает на каждой мелкой улочке и упирается в
# лимит поворотов, не успев набрать длину.
_JUNCTION_PROBE_M = 25.0
# поворот считаем настоящим, если улицы расходятся круче ~40°. Меньше — значит
# это вторая проезжая часть той же дороги, а не перекрёсток.
_JUNCTION_MIN_COS = 0.75

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


class RoadNetwork:
    """Полилины улиц + индексы для быстрых пространственных запросов."""

    def __init__(self, polylines, bbox=None):
        self.bbox = bbox
        self.polylines = []   # [(lat, lon), ...]
        self.cum = []         # [[м, ...], ...] накопительные расстояния
        self.lengths = []     # [м, ...] длина каждой улицы
        self._grid = {}       # (cell_lat, cell_lon) -> [индексы улиц]

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
        # габариты улиц: по ним отсекаем заведомо далёкие, не считая по ним
        # расстояние до каждого сегмента
        self._box = []
        for xy in self._xy:
            xs = [p[0] for p in xy]
            ys = [p[1] for p in xy]
            self._box.append((min(xs), min(ys), max(xs), max(ys)))
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

    def project(self, lat: float, lon: float):
        """Проекция точки на ближайшую улицу — точно, а не по вершине.

        Возвращает (широта, долгота, расстояние_м, индекс_улицы,
        расстояние_вдоль_улицы_м) или None, если рядом улиц нет.

        nearest() отдаёт ближайшую ВЕРШИНУ полилины: на длинном сегменте
        это до десятков метров в сторону. Для маршрута такая ошибка
        выглядит как прыжок устройства в первый же тик, поэтому маршрут
        строится от точной проекции.
        """
        candidates = self._candidates(lat, lon)
        if not candidates:
            return None
        qx = lon * _M_PER_DEG_LON
        qy = lat * _M_PER_DEG
        best = None  # (d2, way_idx, seg_idx, t)
        for way_idx in set(candidates):
            xy = self._xy[way_idx]
            for i in range(len(xy) - 1):
                ax, ay = xy[i]
                bx, by = xy[i + 1]
                dx = bx - ax
                dy = by - ay
                seg2 = dx * dx + dy * dy
                if seg2 <= 0.0:
                    t = 0.0
                else:
                    t = ((qx - ax) * dx + (qy - ay) * dy) / seg2
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                d2 = (ax + dx * t - qx) ** 2 + (ay + dy * t - qy) ** 2
                if best is None or d2 < best[0]:
                    best = (d2, way_idx, i, t)
        if best is None:
            return None
        d2, way_idx, seg_idx, t = best
        poly = self.polylines[way_idx]
        a_lat, a_lon = poly[seg_idx]
        b_lat, b_lon = poly[seg_idx + 1]
        cum = self.cum[way_idx]
        pos_m = cum[seg_idx] + (cum[seg_idx + 1] - cum[seg_idx]) * t
        return (a_lat + (b_lat - a_lat) * t,
                a_lon + (b_lon - a_lon) * t,
                math.sqrt(d2), way_idx, pos_m)

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

    def _nearest_on_way(self, way_idx: int, lat: float, lon: float):
        """Ближайшая точка улицы way_idx к (lat, lon).

        Возвращает (квадрат_расстояния_м2, индекс_сегмента, доля_сегмента)
        или None. Точка может лежать внутри сегмента — это и есть перекрёсток.
        """
        xy = self._xy[way_idx]
        if len(xy) < 2:
            return None
        qx = lon * _M_PER_DEG_LON
        qy = lat * _M_PER_DEG
        best = None
        for i in range(len(xy) - 1):
            ax, ay = xy[i]
            bx, by = xy[i + 1]
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
                best = (d2, i, t)
        return best

    def _dir_at(self, way_idx: int, dist_m: float, direction: int):
        """Единичный вектор движения по улице на отметке dist_m."""
        cum = self.cum[way_idx]
        span = min(20.0, max(cum[-1] / 4.0, 1.0))
        a_lat, a_lon = self.point_at(way_idx, dist_m - direction * span)
        b_lat, b_lon = self.point_at(way_idx, dist_m + direction * span)
        dx = (b_lon - a_lon) * _M_PER_DEG_LON
        dy = (b_lat - a_lat) * _M_PER_DEG
        norm = math.hypot(dx, dy)
        if norm <= 1e-9:
            return None
        return (dx / norm, dy / norm)

    def _pick_junction(self, way_idx: int, lat: float, lon: float,
                       dir_xy=None, prev_way=None):
        """Перекрёсток рядом с точкой: (d2, индекс_сегмента, t, улица) или None.

        dir_xy — единичный вектор движения по текущей улице: улица, идущая
        рядом параллельно (вторая проезжая часть, дубль), перекрёстком не
        считается. prev_way — улица, с которой мы только что свернули.

        Варианты отбираются по убыванию привлекательности: настоящий поворот,
        затем свернуть назад, затем хоть что-то рядом. Так маршрут не
        начинает прыгать туда-обратно между двумя улицами, идущими рядом.
        """
        tol = _JUNCTION_TOL_M
        tol2 = tol * tol
        qx = lon * _M_PER_DEG_LON
        qy = lat * _M_PER_DEG
        turn_pick = None   # настоящий поворот на другую улицу
        back_pick = None   # можно свернуть на ту, откуда приехали
        any_pick = None    # хоть что-то рядом
        for cand in set(self._candidates(lat, lon)):
            if cand == way_idx:
                continue
            x0, y0, x1, y1 = self._box[cand]
            if (qx < x0 - tol or qx > x1 + tol
                    or qy < y0 - tol or qy > y1 + tol):
                continue  # улица заведомо дальше допуска
            found_on = self._nearest_on_way(cand, lat, lon)
            if found_on is None or found_on[0] > tol2:
                continue
            if cand == prev_way:
                if back_pick is None or found_on[0] < back_pick[0]:
                    back_pick = found_on + (cand,)
                continue
            if dir_xy is None:
                if any_pick is None or found_on[0] < any_pick[0]:
                    any_pick = found_on + (cand,)
                continue
            seg = self._xy[cand][found_on[1]]
            nxt = self._xy[cand][found_on[1] + 1]
            seg_len = math.hypot(nxt[0] - seg[0], nxt[1] - seg[1])
            if seg_len <= 0.0:
                continue
            cos_a = abs(dir_xy[0] * (nxt[0] - seg[0]) + dir_xy[1] * (nxt[1] - seg[1])) / seg_len
            if cos_a > _JUNCTION_MIN_COS:
                if any_pick is None or found_on[0] < any_pick[0]:
                    any_pick = found_on + (cand,)
                continue  # идёт рядом параллельно — это не поворот
            if turn_pick is None or found_on[0] < turn_pick[0]:
                turn_pick = found_on + (cand,)
        return turn_pick or back_pick or any_pick

    def _points_to(self, way_idx: int, from_m: float, to_m: float):
        """Точки полилины между отметками from_m и to_m в порядке движения."""
        poly = self.polylines[way_idx]
        cum = self.cum[way_idx]
        out = [self.point_at(way_idx, from_m)]
        if abs(to_m - from_m) < 1e-9:
            return out
        if to_m > from_m:
            # вперёд: вершины строго после from_m и до точки поворота
            i = bisect.bisect_right(cum, from_m)
            while i < len(poly) and cum[i] < to_m:
                out.append(poly[i])
                i += 1
        else:
            # назад: те же вершины в обратном порядке
            j = bisect.bisect_left(cum, from_m) - 1
            while j >= 0 and cum[j] > to_m:
                out.append(poly[j])
                j -= 1
        out.append(self.point_at(way_idx, to_m))
        return out

    def route(self, lat: float, lon: float, forward: bool = True):
        """Маршрут вдоль улиц, начинающийся в точке (lat, lon).

        Устройство едет по улице до ближайшего перекрёстка и поворачивает на
        пересекающую улицу, и так далее — получается зигзаг по кварталам.
        Перекрёсток ищется не в конце улицы, а по ходу движения: в OSM
        пересечения не всегда общие узлы, поэтому ищем ближайшую по
        расстоянию улицу в пределах _JUNCTION_TOL_M.

        Продолжаем, пока не наберётся _ROUTE_TARGET_M или не кончится бюджет
        поворотов _MAX_ROUTE_TURNS; одна и та же улица может попасться дважды.
        Упёршись в тупик, устройство разворачивается и едет обратно.

        Возвращает (точки, накопительные_расстояния, длина_м) или None.
        """
        # стартуем от ТОЧНОЙ проекции исходной точки на улицу: если взять
        # ближайшую вершину, устройство в первый же тик прыгнет на десятки
        # метров (длинные сегменты между вершинами OSM — норма)
        found = self.project(lat, lon)
        if found is None:
            return None
        _lat, _lon, _dist, way_idx, pos_m = found
        direction = 1 if forward else -1

        pts = [self.point_at(way_idx, pos_m)]
        length = 0.0
        turns = 0
        reversed_on = None  # на какой улице уже разворачивались
        prev_way = None     # улица, с которой свернули в прошлый раз

        while length < _ROUTE_TARGET_M and turns < _MAX_ROUTE_TURNS:
            way_len = self.lengths[way_idx]
            # едем по текущей улице до первого перекрёстка (или до конца)
            m = pos_m
            pick = None
            reached_end = False
            while True:
                if length + abs(m - pos_m) >= _ROUTE_TARGET_M:
                    break  # маршрут уже нужной длины
                left = (way_len - m) if direction > 0 else m
                if left <= 1e-9:
                    reached_end = True
                    break
                m = m + direction * min(_JUNCTION_PROBE_M, left)
                probe = self.point_at(way_idx, m)
                dir_xy = self._dir_at(way_idx, m, direction)
                pick = self._pick_junction(way_idx, probe[0], probe[1],
                                           dir_xy, prev_way)
                if pick is not None:
                    break

            moved = abs(m - pos_m)
            if moved > 0.0:
                pts.extend(self._points_to(way_idx, pos_m, m))
                length += moved
                pos_m = m
            if pick is None:
                # доехали до конца улицы: разворот и обратно, как живой водитель
                if reached_end and reversed_on != way_idx:
                    reversed_on = way_idx
                    direction = -direction
                    continue
                break  # маршрут построен

            _d2, seg_idx, t, cand = pick
            cand_pts = self.polylines[cand]
            a_lat, a_lon = cand_pts[seg_idx]
            b_lat, b_lon = cand_pts[seg_idx + 1]
            # точка перехода — проекция на пересекающую улицу
            foot = (a_lat + (b_lat - a_lat) * t, a_lon + (b_lon - a_lon) * t)
            pts.append(foot)

            cand_cum = self.cum[cand]
            cand_m = cand_cum[seg_idx] + (cand_cum[seg_idx + 1] - cand_cum[seg_idx]) * t
            # едем туда, где больше остатка улицы, — меньше шанс упереться в тупик
            new_dir = 1 if (cand_cum[-1] - cand_m) >= cand_m else -1
            first = cand_pts[seg_idx + 1] if new_dir > 0 else cand_pts[seg_idx]
            if len(pts) >= 2 and first == pts[-2]:
                new_dir = -new_dir  # иначе получился бы разворот на 180°

            prev_way = way_idx
            way_idx = cand
            direction = new_dir
            pos_m = cand_m
            turns += 1

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
        "target_route_m": _ROUTE_TARGET_M,
        "junction_tol_m": _JUNCTION_TOL_M,
        "bbox": net.bbox,
    }


# публичный синоним (так его называют другие модули проекта)
road_status = status