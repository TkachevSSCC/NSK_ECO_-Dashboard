from random import uniform
from sqlalchemy.future import select
from app.db.models.station import Stations
from app.services.base import BaseService
from app.db.database import async_session_maker
from app.db.models.station import Stations


class StationService(BaseService):
    model = Stations

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
