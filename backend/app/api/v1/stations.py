from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

import matplotlib
import matplotlib.pyplot as plt
from datetime import datetime
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from skimage import measure
from random import randint, uniform

from sqlalchemy import text, select
from app.db.database import async_session_maker
from app.db.models.station import Stations
from app.db.models.station_behavior import StationBehavior
from app.schedules import clustering_schedule_json, time_zone_schedule, clustering_schedule
from app.schemas.station import PlotRequest, Station
from app.services.station_service import StationService
from app.state.runtime import runtime_state
from app.core.water import land_points_from
from app.core.redis_client import get_redis, POLLUTION_OVERRIDE_KEY


matplotlib.use('agg')

router = APIRouter(
    prefix="/stations",
)

@router.post("/fake_pollutions")
async def set_fake_pollutions():
    runtime_state["fake_pollutions"] += randint(1, 20)
    return JSONResponse(content={"status": "ok", "fake_pollutions": runtime_state["fake_pollutions"]})

@router.post("/clear_fake_pollutions")
async def clear_fake_pollutions():
    runtime_state["fake_pollutions"] -= randint(1, 20)
    return JSONResponse(content={"status": "ok", "fake_pollutions": 0})

@router.post("/reset")
async def reset_cluster_heads():
    """
    Сбрасывает все станции к их исходному типу.
    """
    count = await StationService.reset_all_types()
    return JSONResponse(content={"status": "ok", "updated_stations": count})

@router.post("/set_count")
async def set_stations_count(count: int = 100, moving_count: int | None = None):
    """
    Удаляет все станции и создаёт заново указанное количество
    демо-станций (от 1 до 2000). Первые moving_count станций —
    движущиеся (type_st=1), остальные — стационарные (type_st=0).
    По умолчанию все станции движущиеся (как раньше).
    """
    count = max(1, min(count, 2000))
    if moving_count is None:
        moving_count = count
    moving_count = max(0, min(moving_count, count))

    rng = np.random.default_rng(45)
    tlv = [10, 30]
    low = [54.81, 82.87]
    high = [55.16, 83.21]
    points = land_points_from(rng, low, high, count)
    pm = np.round(rng.gamma((3, 5), (2, 4), (count, 2)), 2)

    async with async_session_maker() as session:
        # полностью чистим старые станции и поведение с перезапуском счётчика id
        await session.execute(
            text("TRUNCATE stations_behaviors, stations RESTART IDENTITY CASCADE")
        )
        for idx, tpl in enumerate(zip(points, pm), start=1):
            session.add(
                Stations(
                    id=idx,
                    type_st=1 if idx <= moving_count else 0,
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
                    radius=0.0015,
                    speed=2.5,
                    progress=0.0,
                )
            )
        await session.commit()

    runtime_state["mode"] = ""
    runtime_state["cluster_count"] = 0
    runtime_state["stations_count"] = count
    runtime_state["fake_pollutions"] = 0

    return JSONResponse(
        content={"status": "ok", "stations_count": count, "moving_count": moving_count}
    )

@router.get("")
async def get_stations() -> list[Station]:
    return await StationService.find_all()

@router.post("/{station_id}/pollute")
async def pollute_station(
    station_id: int,
    pm25: float,
    pm10: float,
    ticks: int = 1,
):
    """Добавляет указанный уровень загрязнения (PM2.5 и PM10) конкретной станции.

    Значения сразу пишутся в БД, а затем держатся на заданном уровне ticks
    тиков (тик = цикл движения, 3 с) в Redis-оверрайде: пока оверрайд активен,
    таск движения не «дрейфует» эти значения, после — загрязнение затухает
    как обычно (ступает в обычный случайный процесс).
    """
    if not (0 <= pm25 <= 500 and 0 <= pm10 <= 500):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "PM должен быть в диапазоне 0..500"},
        )
    st = await StationService.find_by_id(station_id)
    if st is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"Станция {station_id} не найдена"},
        )

    ticks = max(1, min(int(ticks), 200))

    import json as _json

    async with async_session_maker() as session:
        cur = await session.get(Stations, station_id)
        if cur is None:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": f"Станция {station_id} не найдена"},
            )
        cur.PM_2_5 = round(pm25, 2)
        cur.PM_10 = round(pm10, 2)
        cur.overTLV = cur.PM_2_5 > 25 or cur.PM_10 > 50
        await session.commit()

    get_redis().hset(
        POLLUTION_OVERRIDE_KEY,
        str(station_id),
        _json.dumps({"pm25": round(pm25, 2), "pm10": round(pm10, 2), "ticks": ticks}),
    )

    return JSONResponse(
        content={
            "status": "ok",
            "station_id": station_id,
            "PM_2_5": round(pm25, 2),
            "PM_10": round(pm10, 2),
            "overTLV": round(pm25, 2) > 25 or round(pm10, 2) > 50,
            "ticks": ticks,
        }
    )


@router.post("/{station_id}/clear_pollution")
async def clear_pollution(station_id: int):
    """Сбрасывает загрязнение выбранной станции к фоновому (дефолтному) уровню:
    снимает Redis-оверрайд и пишет в БД свежие фоновые значения.
    Дальше станция дрейфует от этого уровня как обычно."""
    st = await StationService.find_by_id(station_id)
    if st is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"Станция {station_id} не найдена"},
        )

    get_redis().hdel(POLLUTION_OVERRIDE_KEY, str(station_id))

    pm25 = round(uniform(0.5, 10.0), 2)
    pm10 = round(uniform(0.5, 12.0), 2)

    async with async_session_maker() as session:
        cur = await session.get(Stations, station_id)
        if cur is None:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": f"Станция {station_id} не найдена"},
            )
        cur.PM_2_5 = pm25
        cur.PM_10 = pm10
        cur.overTLV = False
        await session.commit()

    return JSONResponse(
        content={
            "status": "ok",
            "station_id": station_id,
            "PM_2_5": pm25,
            "PM_10": pm10,
            "overTLV": False,
        }
    )


@router.post("/clear_pollutions")
async def clear_all_pollutions():
    """Сбрасывает загрязнение на ВСЕХ станциях к фоновому уровню:
    удаляет все Redis-оверрайды и пишет в БД фоновые значения."""
    get_redis().delete(POLLUTION_OVERRIDE_KEY)

    async with async_session_maker() as session:
        stations_all = (await session.execute(select(Stations))).scalars().all()
        for cur in stations_all:
            cur.PM_2_5 = round(uniform(0.5, 10.0), 2)
            cur.PM_10 = round(uniform(0.5, 12.0), 2)
            cur.overTLV = False
        await session.commit()
        n = len(stations_all)

    runtime_state["fake_pollutions"] = 0

    return JSONResponse(
        content={"status": "ok", "stations_count": n}
    )


@router.get("/{station_id}/")
async def get_station(station_id: int) -> Station:
    return await StationService.find_by_id(station_id)


@router.get("/plot/cluster")
async def get_cluster_schedule(capacity: int = 10, mode: str | None = None) -> JSONResponse:
    stations_list = await StationService.find_all()

    stations = []
    for st in stations_list:
        stations.append({
            'id': st.id,
            'latitude': st.latitude,
            'longitude': st.longitude,
            'PM_2_5': st.PM_2_5,
            'PM_10': st.PM_10,
            'overTLV': st.overTLV,
            'battery_life': st.battery_life,
            'type_st': st.type_st
        })

    coords = np.array([[s['latitude'], s['longitude']] for s in stations])
    station_ids = np.array([s['id'] for s in stations])
    batteries = np.array([s['battery_life'] for s in stations])

    df = pd.DataFrame(coords, columns=['lat', 'lon'])
    num_clusters = int(coords.shape[0] // capacity) + 5
    kmeans = KMeans(n_clusters=num_clusters, random_state=0).fit(df)

    polygons = []
    cluster_heads = []

    # --- формируем зоны
    h = 0.001
    x_min, x_max = coords[:, 0].min() - 0.01, coords[:, 0].max() + 0.01
    y_min, y_max = coords[:, 1].min() - 0.01, coords[:, 1].max() + 0.01
    xx, yy = np.meshgrid(np.arange(x_min, x_max, h),
                         np.arange(y_min, y_max, h))
    Z = kmeans.predict(np.c_[xx.ravel(), yy.ravel()]).reshape(xx.shape)

    for cluster_id in np.unique(Z):
        mask = Z == cluster_id
        padded_mask = np.pad(mask, pad_width=1, mode='constant', constant_values=0)
        contours = measure.find_contours(padded_mask.astype(float), 0.5)

        for contour in contours:
            latitudes = xx[0, 0] + contour[:, 1] * h
            longitudes = yy[0, 0] + contour[:, 0] * h
            polygon = [[float(lat), float(lon)] for lat, lon in zip(latitudes, longitudes)]
            polygons.append({
                "cluster_id": int(cluster_id),
                "points": polygon
            })


        if mode:
            # кандидатом в хэд может быть ЛЮБАЯ станция кластера
            # (стационарная, движущаяся или уже выбранный хэд) —
            # так в каждом кластере гарантированно будет хэд
            valid_mask = kmeans.labels_ == cluster_id
            cluster_points = coords[valid_mask]
            cluster_ids = station_ids[valid_mask]

            if len(cluster_points) == 0:
                continue  # пустой кластер — практически невозможно

            if mode == "battery_life":
                cluster_batteries = batteries[valid_mask]
                sorted_idx = np.argsort(cluster_batteries)[::-1][:2]
            else:
                dists = np.linalg.norm(cluster_points - kmeans.cluster_centers_[cluster_id], axis=1)
                sorted_idx = np.argsort(dists)[:2]

            selected_ids = cluster_ids[sorted_idx]
            selected_heads = cluster_points[sorted_idx]

            # Обновляем их типы в БД
            for sid in selected_ids:
                await StationService.update_type(sid)

            cluster_heads_entry = {"cluster_id": int(cluster_id), "heads": []}
            for sid, coords_pair in zip(selected_ids, selected_heads):
                cluster_heads_entry["heads"].append({
                    "id": int(sid),
                    "coords": coords_pair.tolist(),
                    "type": "battery_head" if mode == "battery_life" else "cluster_head"
                })
            cluster_heads.append(cluster_heads_entry)

    
    if mode:
        runtime_state["mode"] = "cluster_head"
        runtime_state["cluster_count"] = len(polygons)
    else:
        runtime_state["mode"] = "clusters"
        runtime_state["stations_count"] = len(stations) 

    # состав каждого кластера: какие устройства в него входят
    members = []
    for cluster_id in range(num_clusters):
        mask = kmeans.labels_ == cluster_id
        cl_ids = [int(sid) for sid in station_ids[mask]]
        members.append({
            "cluster_id": int(cluster_id),
            "count": len(cl_ids),
            "stations": cl_ids
        })

    print(runtime_state)

    return JSONResponse(content={"polygons": polygons, "heads": cluster_heads, "members": members})




@router.get("/plot/timezone")
async def get_timezone_schedule(capacity: int = 10) -> JSONResponse:
    stations_list = await StationService.find_all()
    stations = []
    for st in stations_list:
        stations.append({
            'id': st.id,
            'latitude': st.latitude,
            'longitude': st.longitude,
            'PM_2_5': st.PM_2_5,
            'PM_10': st.PM_10,
            'overTLV': st.overTLV
        })
    coords = np.array([[s['latitude'], s['longitude']] for s in stations])
    station_ids = np.array([s['id'] for s in stations])
    x_min = coords[:, 0].min() - 0.01
    x_max = coords[:, 0].max() + 0.01
    y_min = coords[:, 1].min() - 0.01
    y_max = coords[:, 1].max() + 0.01

    num_zones = int(coords.shape[0] // capacity) + 5
    lines = np.linspace(x_min, x_max, num_zones + 1).tolist()

    zones = []
    for i in range(len(lines) - 1):
        # станции, попадающие в полосу по широте
        mask = (coords[:, 0] >= lines[i]) & (coords[:, 0] <= lines[i + 1])
        member_ids = [int(sid) for sid in station_ids[mask]]
        zones.append({
            "zone_id": i,
            "x_min": lines[i],
            "x_max": lines[i + 1],
            "y_min": y_min,
            "y_max": y_max,
            "count": len(member_ids),
            "stations": member_ids
        })
    
    runtime_state["stations_count"] = len(stations)
    runtime_state["mode"] = "timezone"
    runtime_state["timezone_count"] = num_zones

    return JSONResponse(content={"zones": zones})

@router.get("/plot/{option}")
async def get_plot(option: int):
    stations = await StationService.find_all()
    stations_list = []
    for st in stations:
        stations_list.append({
            'id': st.id,
            'latitude': st.latitude,
            'longitude': st.longitude,
            'PM_2_5': st.PM_2_5,
            'PM_10': st.PM_10,
            'overTLV': st.overTLV
        })
    print(stations_list)
    if option == 1:
        fig, ax = plt.subplots(figsize=(5, 4))   # создаём только 1 ось
        time_zones = time_zone_schedule(stations_list, 10, ax)

    elif option == 2:
        fig, ax = plt.subplots(figsize=(5, 4))
        cluster_zones_no_heads = clustering_schedule(stations_list, 10, ax)
    
    elif option == 3:
        fig, ax = plt.subplots(figsize=(5, 4))
        cluster_zones_no_heads = clustering_schedule(stations_list, 10, ax, mode='proximity')
    
    elif option == 4:
        fig, ax = plt.subplots(figsize=(5, 4))
        cluster_zones_no_heads = clustering_schedule(stations_list, 10, ax, mode='battery_life')

    filename = f"app/static/plots/plot_{option}_{datetime.now().strftime('%d.%m.%Y_%H-%M-%S')}.png"
    plt.savefig(filename, bbox_inches='tight')
    plt.close()
    
    return FileResponse(filename, media_type="image/png")


@router.post("/plot/cluster_heads")
async def get_cluster_head_plot(data: PlotRequest):
    print(11)
    stations_list = [
        {
            "id": station.id,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "battery_life": station.battery
        }
        for station in data.stations
    ]
    fig, ax = plt.subplots(figsize=(15, 12))
    print(1)
    
    cluster_zones = clustering_schedule_json(stations_list, 10, ax, mode='proximity')

    filename = (
        f"app/static/plots/plot_cluster_heads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )

    plt.savefig(filename, bbox_inches="tight")
    plt.close()
    #return JSONResponse(content={"status": "ok"})
    return FileResponse(filename, media_type="image/png")