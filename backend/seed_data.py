"""Идемпотентный сид-скрипт: создаёт типы станций и 100 демо-станций.

Запуск (из папки backend):
    python seed_data.py
"""
import asyncio

import numpy as np
from sqlalchemy import select

from app.db.database import async_session_maker
from app.db.models.station import Stations
from app.db.models.station_behavior import StationBehavior
from app.db.models.types import Types
from app.core.water import land_points_from


async def seed():
    async with async_session_maker() as session:
        # --- types ---
        types = (await session.execute(select(Types))).scalars().all()
        if not types:
            for tid, desc in [(0, "undefined"), (1, "ordinary"), (2, "cluster_head")]:
                session.add(Types(id=tid, description=desc))
            await session.commit()
            print("Seeded types")
        else:
            print(f"Types already present: {len(types)}")

        # --- stations ---
        stations = (await session.execute(select(Stations))).scalars().all()
        if stations:
            print(f"Stations already present: {len(stations)}, skip")
            return

        rng = np.random.default_rng(45)
        n_stations = 100
        tlv = [10, 30]
        low = [54.81, 82.87]
        high = [55.16, 83.21]
        points = land_points_from(rng, low, high, n_stations)
        pm = np.round(rng.gamma((3, 5), (2, 4), (n_stations, 2)), 2)

        for idx, tpl in enumerate(zip(points, pm), start=1):
            session.add(
                Stations(
                    id=idx,
                    type_st=1,
                    battery_life=100.0,
                    latitude=float(tpl[0][0]),
                    longitude=float(tpl[0][1]),
                    PM_2_5=float(tpl[1][0]),
                    PM_10=float(tpl[1][1]),
                    overTLV=int((tpl[1] > tlv).any()),
                )
            )
            session.add(
                StationBehavior(
                    station_id=idx,
                    # у каждой станции свои скорость, радиус орбиты и фаза
                    radius=round(float(rng.uniform(0.0006, 0.0035)), 6),
                    speed=round(float(rng.uniform(3.2, 16.0)), 2),
                    progress=round(float(rng.uniform(0.0, 11.99)), 2),
                )
            )
        await session.commit()
        print(f"Seeded {n_stations} stations with behaviors")


if __name__ == "__main__":
    asyncio.run(seed())