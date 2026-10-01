import asyncio
from random import uniform
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.future import select
from app.db.models.station import Stations
from app.services.base import BaseService
from app.db.database import async_session_maker
from app.db.models.station import Stations


class StationService(BaseService):
    model = Stations

    @classmethod
    async def drain_battery(cls, drains: dict[int, float]) -> int:
        """
        Списывает заряд батареи за тик: drains — процент заряда для каждой
        станции (у каждой своё значение из-за разброса). Заряд не опускается
        ниже нуля. Возвращает количество устройств, у которых списан заряд.
        """
        drains = {int(sid): float(drain) for sid, drain in drains.items()}
        if not drains:
            return 0

        # строки обновляем строго по возрастанию id: списание идёт одновременно
        # с обновлением станций, и если обе транзакции берут строки в одном
        # порядке, PostgreSQL не ловит взаимную блокировку (deadlock)
        values = ", ".join(
            f"({sid}, {drains[sid]:.4f})" for sid in sorted(drains)
        )
        stmt = text(
            "UPDATE stations SET battery_life = GREATEST(battery_life - d.drain, 0) "
            f"FROM (VALUES {values}) AS d(id, drain) WHERE stations.id = d.id"
        )
        for attempt in range(3):
            try:
                async with async_session_maker() as session:
                    await session.execute(stmt)
                    await session.commit()
                return len(drains)
            except DBAPIError as exc:
                # 40001 — deadlock detected, 40P01 — deadlock (asyncpg)
                code = getattr(getattr(exc, "orig", None), "sqlstate", "") or ""
                if code not in ("40001", "40P01") or attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        return 0

    @classmethod
    async def reset_all_battery(cls, charge: float = 100.0) -> int:
        """Заряжает все станции до charge процентов. Возвращает их количество."""
        async with async_session_maker() as session:
            result = await session.execute(select(Stations))
            stations = result.scalars().all()
            for st in stations:
                st.battery_life = charge
                session.add(st)
            await session.commit()
        return len(stations)

    @classmethod
    async def update_type(cls, station_id: int):
        """
        Обновляет тип станции (например, 'cluster_head' или 'battery_head'),
        но только если у станции type_st == 0.
        """
        async with async_session_maker() as session:
            result = await session.execute(select(Stations).where(Stations.id == station_id))
            station = result.scalar_one_or_none()
            if not station:
                return None

            station.type_st = 2
            session.add(station)
            await session.commit()
            #return station

    @classmethod
    async def reset_all_types(cls):
        """
        Возвращает станции-хэды (type_st == 2) к исходному типу (1) и
        очищает их значения до фонового уровня. Возвращает список id.
        """
        reset_ids = []
        async with async_session_maker() as session:
            result = await session.execute(select(Stations))
            stations = result.scalars().all()

            for st in stations:
                if st.type_st == 2:
                    st.type_st = 1
                    st.PM_2_5 = round(uniform(0.5, 10.0), 2)
                    st.PM_10 = round(uniform(0.5, 12.0), 2)
                    st.overTLV = False
                    reset_ids.append(st.id)
                    session.add(st)

            await session.commit()
        return reset_ids
