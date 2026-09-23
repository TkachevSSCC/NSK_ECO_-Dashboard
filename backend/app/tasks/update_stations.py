import asyncio
import random
import math
import json
from app.core.celery_app import celery
from app.core.water import snap_to_land
from app.core.redis_client import get_redis, POLLUTION_OVERRIDE_KEY
from app.db.database import async_session_maker
from app.db.models.station import Stations
from app.db.models.station_behavior import StationBehavior
from sqlalchemy import select

loop = asyncio.get_event_loop()

# Память для маршрутов и прогресса
routes = {}
progress = {}
speeds = {}

@celery.task(name="app.tasks.update_stations.update_stations")
def update_stations():
    loop.run_until_complete(update_stations_async())


async def update_stations_async():
    async with async_session_maker() as session:
        try:
            result = await session.execute(select(Stations, StationBehavior).join(StationBehavior, Stations.id == StationBehavior.station_id))
            stations = result.all()

            def make_route(st, bh):
                base_lat = st.latitude
                base_lon = st.longitude
                return [
                    (base_lat + bh.radius * math.cos(angle),
                     base_lon + bh.radius * math.sin(angle))
                    for angle in [i * (2 * math.pi / 12) for i in range(12)]
                ]

            # Удаляем маршруты станций, которых больше нет в БД (например, после ресида)
            db_ids = {st.id for st, bh in stations}
            for sid in list(routes.keys()):
                if sid not in db_ids:
                    del routes[sid]

            # Маршрут только для движущихся (type_st == 1):
            #  - удаляем маршруты у станций, ставших стационарными (после ресида с moving_count),
            #  - создаём маршруты для новых движущихся станций.
            for st, bh in stations:
                if st.type_st == 1:
                    if st.id not in routes:
                        routes[st.id] = make_route(st, bh)
                        bh.progress = 0.0
                else:
                    routes.pop(st.id, None)

            # Обновление всех станций
            for st, bh in stations:
                if st.id in routes:
                    route = routes[st.id]
                    idx = int(bh.progress) % len(route)
                    next_idx = (idx + 1) % len(route)

                    lat1, lon1 = route[idx]
                    lat2, lon2 = route[next_idx]

                    # доля пути между двумя точками
                    frac = bh.progress % 1.0

                    # плавное движение между точками
                    old_lat, old_lon = st.latitude, st.longitude
                    new_lat = lat1 + (lat2 - lat1) * frac
                    new_lon = lon1 + (lon2 - lon1) * frac
                    # если новая точка попала на воду — остаёмся на ближайшей суше
                    st.latitude, st.longitude = snap_to_land(
                        old_lat, old_lon, new_lat, new_lon
                    )

                    # продвижение по кругу
                    bh.progress += bh.speed
                    if bh.progress >= len(route):
                        bh.progress -= len(route)

            result_all = await session.execute(select(Stations))
            stations_all = result_all.scalars().all()
            print(len(stations_all))

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
            print(f"✅ Updated {len(stations)} stations ({len(routes)} moving in circular paths)")

        except Exception as e:
            await session.rollback()
            print("❌ Error updating stations:", e)
