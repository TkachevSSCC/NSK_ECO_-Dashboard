import asyncio
import bisect
import random
import math
import json
from app.core.celery_app import celery
from app.core.water import snap_to_land
from app.core.redis_client import get_redis, POLLUTION_OVERRIDE_KEY
from app.core.roads import road_route, ROAD_MIN_ROUTE_M
from app.db.database import async_session_maker
from app.db.models.station import Stations
from app.db.models.station_behavior import StationBehavior
from sqlalchemy import select

loop = asyncio.get_event_loop()

# движение по дороге: скорость в метрах за тик. Тик — секунда
# (beat_schedule = TICK_SECONDS), поэтому 4–15 м/с — это 14–54 км/ч,
# обычный городской диапазон.
ROAD_SPEED_MIN_M = 4.0
ROAD_SPEED_MAX_M = 15.0

# Память для маршрутов и прогресса
routes = {}
progress = {}
speeds = {}

def reset_movement_state():
    """Полный сброс кеша маршрутов (после пересоздания станций)."""
    routes.clear()
    progress.clear()
    speeds.clear()


def position_on_road(route, dist_m):
    """Точка маршрута на расстоянии dist_m от его начала.

    Устройство едет «туда-обратно»: пройденное расстояние растёт
    бесконечно, а положение считается треугольной волной по длине
    маршрута. Так на развороте устройство не телепортируется, а
    действительно доезжает до конца улицы и возвращается.
    """
    pts = route["pts"]
    cum = route["cum"]
    length = route["length"]
    if length <= 0.0 or len(pts) < 2:
        return pts[0]
    d = dist_m % (2.0 * length)
    if d > length:
        d = 2.0 * length - d
    i = bisect.bisect_left(cum, d)
    if i <= 0:
        return pts[0]
    if i >= len(cum):
        return pts[-1]
    seg = cum[i] - cum[i - 1]
    t = 0.0 if seg <= 0.0 else (d - cum[i - 1]) / seg
    (lat0, lon0), (lat1, lon1) = pts[i - 1], pts[i]
    return (lat0 + (lat1 - lat0) * t, lon0 + (lon1 - lon0) * t)


def make_route(st, bh, forward=True):
    """Маршрут движущейся станции: улица из OSM, а если дорог рядом нет —
    старая круговая орбита (чтобы станция не «стояла»)."""
    road = road_route(st.latitude, st.longitude, forward=forward)
    if road is not None:
        pts, cum, length = road
        if length >= ROAD_MIN_ROUTE_M:
            bh.progress = 0.0
            return {"pts": pts, "cum": cum, "length": length, "road": True}

    base_lat = st.latitude
    base_lon = st.longitude
    # у каждой станции своя стартовая фаза, «неровная» орбита
    # (амплитуда радиуса меняется по 3 «лепесткам») и направление
    angle_step = 2 * math.pi / 12
    phase = (st.id * 1.7) % (2 * math.pi)
    points = [
        (
            base_lat
            + bh.radius
            * (1 + 0.3 * math.sin(3 * (i * angle_step + phase)))
            * math.cos(i * angle_step + phase),
            base_lon
            + bh.radius
            * (1 + 0.3 * math.sin(3 * (i * angle_step + phase)))
            * math.sin(i * angle_step + phase),
        )
        for i in range(12)
    ]
    # чётные станции едут по часовой, нечётные — против
    if st.id % 2 == 0:
        points.reverse()
    return {"pts": points, "cum": None, "length": 0.0, "road": False}

@celery.task(name="app.tasks.update_stations.update_stations")
def update_stations():
    loop.run_until_complete(update_stations_async())


async def update_stations_async():
    async with async_session_maker() as session:
        try:
            result = await session.execute(select(Stations, StationBehavior).join(StationBehavior, Stations.id == StationBehavior.station_id))
            stations = result.all()

            # Удаляем маршруты станций, которых больше нет в БД (например, после ресида)
            db_ids = {st.id for st, bh in stations}
            for sid in list(routes.keys()):
                if sid not in db_ids:
                    del routes[sid]

            # Маршрут только для движущихся (type_st == 1):
            #  - удаляем маршруты у станций, ставших стационарными (после ресида с moving_count),
            #  - создаём маршруты для новых движущихся станций.
            #  Направление по дороге чередуется по номеру станции, чтобы
            #  соседи по улице ехали навстречу, а не друг за другом.
            for st, bh in stations:
                if st.type_st == 1:
                    if st.id not in routes:
                        routes[st.id] = make_route(
                            st, bh, forward=st.id % 2 == 1
                        )
                else:
                    routes.pop(st.id, None)

            # Обновление всех станций
            for st, bh in stations:
                if st.id in routes:
                    route = routes[st.id]

                    if route["road"]:
                        # скорость в метрах за тик: сдвигаемся вперёд по
                        # маршруту, на развороте (конец улицы) маршрут
                        # «разворачивается» сам — устройство едет туда-обратно
                        bh.progress += bh.speed
                        new_lat, new_lon = position_on_road(route, bh.progress)
                    else:
                        # запасная круговая орбита (дорог рядом нет):
                        # старая логика — скорость в «шагах» по 12 точкам
                        pts = route["pts"]
                        idx = int(bh.progress) % len(pts)
                        next_idx = (idx + 1) % len(pts)
                        lat1, lon1 = pts[idx]
                        lat2, lon2 = pts[next_idx]
                        frac = bh.progress % 1.0
                        new_lat = lat1 + (lat2 - lat1) * frac
                        new_lon = lon1 + (lon2 - lon1) * frac
                        bh.progress += bh.speed
                        if bh.progress >= len(pts):
                            bh.progress -= len(pts)

                    old_lat, old_lon = st.latitude, st.longitude
                    # если новая точка попала на воду — остаёмся на ближайшей суше
                    st.latitude, st.longitude = snap_to_land(
                        old_lat, old_lon, new_lat, new_lon
                    )

            result_all = await session.execute(select(Stations))
            stations_all = result_all.scalars().all()

            # Оверрайды загрязнений (станция удерживает заданные PM заданное
            # число тиков, пока они активны — случайный дрейф их не трогает)
            rc = get_redis()
            override_raw = rc.hgetall(POLLUTION_OVERRIDE_KEY) or {}
            overrides = {int(k): json.loads(v) for k, v in override_raw.items()}
            writeback = {}

            for st in stations_all:
                ov = overrides.get(st.id)
                if ov and ov["ticks"] > 0:
                    st.PM_2_5 = float(ov["pm25"])
                    st.PM_10 = float(ov["pm10"])
                    ov["ticks"] -= 1
                    writeback[st.id] = ov
                else:
                    # обычный случайный дрейф показателей
                    for field in ["PM_2_5", "PM_10"]:
                        old = getattr(st, field)
                        if old == 0.1:
                            old = random.uniform(0.1, 10)
                        new = round(old * (1 + random.uniform(-0.05, 0.05)), 2)
                        setattr(st, field, new)

                # пересчёт TLV
                st.overTLV = st.PM_2_5 > 25 or st.PM_10 > 50

            # синхронизируем оставшиеся тики оверрайдов и убираем отработавшие
            exhausted = set(overrides) - set(writeback)
            if writeback or exhausted:
                pipe = rc.pipeline()
                for sid, ov in writeback.items():
                    pipe.hset(POLLUTION_OVERRIDE_KEY, str(sid), json.dumps(ov))
                for sid in exhausted:
                    pipe.hdel(POLLUTION_OVERRIDE_KEY, str(sid))
                pipe.execute()

            await session.commit()
            # сколько едет по улицам, а сколько по запасной круговой орбите
            # (второе — только если рядом нет дорог, т.е. нет roads.json)
            on_roads = sum(1 for r in routes.values() if r["road"])
            print(
                f"✅ Updated {len(stations)} stations "
                f"({len(routes)} moving: {on_roads} по дорогам, "
                f"{len(routes) - on_roads} по орбите)"
            )

        except Exception as e:
            await session.rollback()
            print("❌ Error updating stations:", e)
