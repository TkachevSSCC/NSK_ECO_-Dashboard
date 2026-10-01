from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

import matplotlib
import matplotlib.pyplot as plt
from datetime import datetime
import numpy as np
from random import randint, uniform

from sqlalchemy import text, select
from app.db.database import async_session_maker
from app.db.models.station import Stations
from app.db.models.station_behavior import StationBehavior
from app.schedules import clustering_schedule_json, time_zone_schedule, clustering_schedule
from app.metrics.scheduler import (
    DEFAULT_BUFFER_CAPACITY_KB,
    MIN_BUFFER_CAPACITY_KB,
    MAX_BUFFER_CAPACITY_KB,
    DEFAULT_BUFFER_RATE_RATIO,
    MIN_BUFFER_RATE_RATIO,
    MAX_BUFFER_RATE_RATIO,
    GLOBAL_MSG_KB,
    ZONE_MSG_KB,
    mean_weight_factor,
    BATTERY_DRAIN_GLOBAL,
    BATTERY_DRAIN_ZONE,
    MOVING_TYPE,
)
from app.metrics.calculator import (
    calculate_messages_per_second,
    count_danger_clusters,
    danger_cluster_members,
    global_sender_ids,
    DANGER_CLUSTER_MSG_MULTIPLIER,
)
from app.schemas.station import PlotRequest, Station
from app.services.station_service import StationService
from app.services.cluster_service import recluster, preview_cluster_layout
from app.state.runtime import runtime_state
from app.core.water import land_points_from, is_wet_point
from app.core.redis_client import get_redis, POLLUTION_OVERRIDE_KEY
from app.tasks.update_stations import reset_movement_state


matplotlib.use('agg')

# полный заряд батареи, % (движущиеся станции создаются с полным зарядом)
FULL_BATTERY_PCT = 100.0

# границы области анализа и генерации станций: (широта, долгота)
GENERATION_LOW = [54.81, 82.87]
GENERATION_HIGH = [55.16, 83.21]

# число кластеров по умолчанию: столько кластеров строит recluster,
# если его не вызывали (совпадает с capacity=10 в cluster_service.recluster)
DEFAULT_CLUSTER_COUNT = 10

# режимы для сравнения нагрузки: ключ — значение mode в runtime_state,
# который принимает calculate_messages_per_second
SCENARIO_DEFS = (
    (
        "clusters",
        "Передают все",
        "каждое устройство отправляет сообщение в глобальную сеть",
    ),
    (
        "cluster_head",
        "Кластеры с хэдами",
        "в сеть передаёт только хэд каждого кластера и те, кто вышел за его пределы",
    ),
)
# для каких режимов нужен расклад кластеров
SCENARIOS_NEED_CLUSTERS = frozenset({"cluster_head"})

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
    Сбрасывает все станции к их исходному типу, а устройствам,
    которые были кластер-хэдами, очищает значения до фонового уровня.
    Дополнительно заряжает батареи до 100% (после списания за передачу).
    """
    reset_ids = await StationService.reset_all_types()
    if reset_ids:
        rc = get_redis()
        for sid in reset_ids:
            rc.hdel(POLLUTION_OVERRIDE_KEY, str(sid))
    await StationService.reset_all_battery()
    return JSONResponse(content={"status": "ok", "updated_stations": len(reset_ids)})

@router.post("/charge_battery")
async def charge_battery(charge: float = 100.0):
    """Заряжает батареи всех устройств до charge процентов."""
    charge = round(max(0.0, min(charge, FULL_BATTERY_PCT)), 1)
    n = await StationService.reset_all_battery(charge)
    return JSONResponse(
        content={"status": "ok", "battery_life": charge, "stations_count": n}
    )

@router.post("/transmit_all")
async def set_transmit_all(enabled: bool = True):
    """
    Режим «передают все»: все станции передают в глобальную сеть
    (mode="clusters" -> mps = числу станций). enabled=False — никто
    не передаёт в сеть (mode="").
    """
    if enabled:
        runtime_state["mode"] = "clusters"
        runtime_state["cluster_count"] = 0
        runtime_state["cluster_variant"] = None
        runtime_state.pop("cluster_centroids", None)
        runtime_state.pop("cluster_radii", None)
        runtime_state.pop("timezone_zone_heads", None)
    else:
        runtime_state["mode"] = ""
    return JSONResponse(content={"status": "ok", "mode": runtime_state["mode"]})

@router.get("/load_scenarios")
async def get_load_scenarios():
    """
    Прогноз нагрузки на сеть для каждого режима передачи при текущем
    состоянии станций: сколько сообщений и килобайт в секунду уйдёт в
    глобальную сеть, если включить тот или иной режим.

    Ничего не переключает и не меняет runtime_state — это только расчёт.
    """
    stations_raw = await StationService.find_all()
    stations = [
        {
            "id": s.id,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "PM_2_5": s.PM_2_5,
            "PM_10": s.PM_10,
            "type_st": s.type_st,
            "battery_life": s.battery_life,
        }
        for s in stations_raw
    ]
    # устройства на нулевом заряде молчат — в сценариях их не считаем
    active = [s for s in stations if float(s["battery_life"] or 0.0) > 0.0]
    danger_count = count_danger_clusters(active)
    danger_ids = danger_cluster_members(active)
    moving_count = sum(1 for s in active if s["type_st"] == MOVING_TYPE)

    centroids = runtime_state.get("cluster_centroids")
    radii = runtime_state.get("cluster_radii")
    stored_cluster_count = int(runtime_state.get("cluster_count") or 0)

    # Кластеры могли ещё не строиться. Раньше в этом случае бралось
    # DEFAULT_CLUSTER_COUNT и centroids=None, а _outside_mask без центроидов
    # отдаёт нулевую маску — «вышедшие за пределы» не учитывались совсем.
    # Тогда кластерный режим показывал заметно меньше сообщений, чем
    # получилось бы на деле, и преимущество выглядело завышенным.
    # Теперь считаем расклад заранее (без смены режима и записи в БД),
    # чтобы карточка показывала честные цифры ещё до включения кластеров.
    estimated_layout = False
    preview = None
    if stored_cluster_count:
        cluster_count = stored_cluster_count
        use_centroids, use_radii = centroids, radii
    else:
        preview = preview_cluster_layout(active)
        if preview:
            cluster_count = preview["cluster_count"]
            use_centroids, use_radii = preview["centroids"], preview["radii"]
            estimated_layout = True
        else:
            cluster_count = DEFAULT_CLUSTER_COUNT
            use_centroids, use_radii = None, None

    all_active_ids = {int(s["id"]) for s in active}
    danger_set = set(danger_ids)

    scenarios = []
    for mode, title, hint in SCENARIO_DEFS:
        if mode in SCENARIOS_NEED_CLUSTERS:
            mps = calculate_messages_per_second(
                stations=active,
                mode=mode,
                cluster_count=cluster_count,
                fake_pollutions=runtime_state.get("fake_pollutions", 0),
                cluster_centroids=use_centroids,
                cluster_radii=use_radii,
                danger_cluster_count=danger_count,
            )
        else:
            mps = calculate_messages_per_second(
                stations=active,
                mode=mode,
                cluster_count=cluster_count,
                fake_pollutions=runtime_state.get("fake_pollutions", 0),
                danger_cluster_count=danger_count,
            )

        # Вес одного сообщения зависит от загрязнения станции-отправителя,
        # поэтому считаем средний коэффициент по тем, кто реально передаёт
        # в сеть, и по тем, кто передаёт в пределах зоны. Без этого карточка
        # показывала бы фиксированный вес и занижала трафик в грязных районах.
        if (
            mode in SCENARIOS_NEED_CLUSTERS
            and estimated_layout
            and preview
            and preview.get("head_ids")
        ):
            # Кластеры ещё не включались, поэтому хэдов в типах станций нет,
            # и global_sender_ids не нашёл бы ни одного. Берём хэды из
            # предпросмотра — иначе число сообщений считалось бы по будущим
            # хэдам, а расход заряда и вес — по тем, кто ими не станет.
            g_ids = set(preview["head_ids"]) & all_active_ids
        else:
            g_ids = global_sender_ids(
                active,
                mode,
                centroids=use_centroids,
                radii=use_radii,
                danger_ids=danger_ids,
            )
        g_ids = g_ids | danger_set
        g_factor = mean_weight_factor(active, g_ids) if mps else 1.0
        z_factor = mean_weight_factor(active, all_active_ids - g_ids)


        global_kb = mps * GLOBAL_MSG_KB * g_factor
        # Всё, что не ушло в сеть, передаётся внутри зоны. Здесь нужен
        # минимум 1: планировщик держит «передачи в пределах зоны» не
        # ниже нуля, и полоска должна показывать то же, что график.
        zone_msgs = max(1, len(active) - mps) if active else 0
        zone_kb = zone_msgs * ZONE_MSG_KB * z_factor

        # Расход заряда так считать нельзя. Списывается не «сообщение»,
        # а конкретное устройство: стационарные (type=0) питаются постоянно
        # и не разряжаются, а устройство из временного кластера передаёт
        # в DANGER_CLUSTER_MSG_MULTIPLIER раз чаще — столько же списывается.
        # И никакого минимума в 1: если в зону никто не передаёт, списывать
        # нечего, иначе карточка показывала лишние 0.06 %/с.
        drain = 0.0
        hours_to_empty = None
        for station in active:
            if station["type_st"] == 0:
                continue
            sid = int(station["id"])
            if sid in g_ids:
                rate = BATTERY_DRAIN_GLOBAL
                if sid in danger_set:
                    rate *= DANGER_CLUSTER_MSG_MULTIPLIER
            else:
                rate = BATTERY_DRAIN_ZONE
            drain += rate
            # через сколько часов сядет именно это устройство
            battery = float(station["battery_life"] or 0.0)
            if rate > 0 and battery > 0:
                left = battery / rate / 3600.0
                if hours_to_empty is None or left < hours_to_empty:
                    hours_to_empty = left

        scenarios.append(
            {
                "mode": mode,
                "title": title,
                "hint": hint,
                "global_messages": mps,
                "zone_messages": zone_msgs,
                "global_kb": round(global_kb, 2),
                "zone_kb": round(zone_kb, 2),
                # средний коэффициент веса сообщения по загрязнению
                "global_weight_factor": round(g_factor, 3),
                "zone_weight_factor": round(z_factor, 3),
                "total_kb": round(global_kb + zone_kb, 2),
                # сумма списания по всем устройствам, % за секунду
                "battery_drain_pct": round(drain, 3),
                # через сколько часов сядет первое устройство — это и есть
                # реальный срок службы, сумма выше мало о чём говорит
                "hours_to_empty": round(hours_to_empty, 1)
                if hours_to_empty is not None
                else None,
            }
        )

    # режим, который включён сейчас, — чтобы интерфейс мог его подсветить
    current_mode = runtime_state.get("mode") or ""
    for sc in scenarios:
        sc["is_current"] = sc["mode"] == current_mode

    return JSONResponse(
        content={
            "status": "ok",
            "active_count": len(active),
            "moving_count": moving_count,
            "danger_cluster_count": danger_count,
            "cluster_count": cluster_count,
            "clusters_built": bool(stored_cluster_count),
            # расклад посчитан на лету, режим кластеров ещё не включался
            "cluster_layout_estimated": estimated_layout,
            "current_mode": current_mode,
            "scenarios": scenarios,
        }
    )


@router.get("/water_mask")
async def get_water_mask(cells: int = 36):
    """Признак воды по ячейкам сетки анализа.

    Нужен интерфейсу, чтобы не рекомендовать точку установки в воде:
    сама геометрия воды живёт только на бэкенде, на фронте её нет.

    Возвращает плоский массив длиной cells*cells в том же порядке, что и
    ячейки поля загрязнения (строка с севера на юг, колонка с запада на
    восток), чтобы фронт мог просто исключить такие ячейки.

    Нужен интерфейсу, чтобы не рекомендовать точку установки в воде:
    сама геометрия воды живёт только на бэкенде, на фронте её нет.

    Возвращает плоский массив длиной cells*cells в том же порядке, что и
    ячейки поля загрязнения (строка с севера на юг, колонка с запада на
    восток), чтобы фронт мог просто исключить такие ячейки.
    """
    cells = max(4, min(int(cells or 36), 120))
    low, high = GENERATION_LOW, GENERATION_HIGH
    lat_step = (high[0] - low[0]) / cells
    lon_step = (high[1] - low[1]) / cells

    mask = []
    for row in range(cells):
        # поле считается с севера на юг, поэтому та же развёртка индексов
        lat = high[0] - (row + 0.5) * lat_step
        for column in range(cells):
            lon = low[1] + (column + 0.5) * lon_step
            mask.append(bool(is_wet_point(lat, lon)))

    return JSONResponse(
        content={
            "status": "ok",
            "cells": cells,
            "min_lat": low[0],
            "max_lat": high[0],
            "min_lon": low[1],
            "max_lon": high[1],
            "mask": mask,
        }
    )


@router.get("/buffer")
async def get_buffer_capacity():
    """Ёмкость и скорость обработки буфера очереди, КБ и КБ/с
    (для восстановления полей в интерфейсе)."""
    return JSONResponse(
        content={
            "status": "ok",
            "buffer_capacity_kb": runtime_state.get(
                "buffer_capacity_kb", DEFAULT_BUFFER_CAPACITY_KB
            ),
            "max_buffer_capacity_kb": MAX_BUFFER_CAPACITY_KB,
            "buffer_rate_ratio": runtime_state.get(
                "buffer_rate_ratio", DEFAULT_BUFFER_RATE_RATIO
            ),
            "max_buffer_rate_ratio": MAX_BUFFER_RATE_RATIO,
        }
    )


@router.post("/set_buffer")
async def set_buffer_capacity(capacity_kb: float = DEFAULT_BUFFER_CAPACITY_KB):
    """
    Ёмкость буфера очереди в килобайтах (0–1000000). За тик устройства
    складывают сообщения в буфер, не поместившееся (переполнение) теряется
    и не учитывается в счётчиках сообщений и веса. Обрабатывает буфер сеть —
    со скоростью buffer_rate_ratio (см. /set_buffer_rate).
    """
    if capacity_kb != capacity_kb:  # NaN
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Некорректная ёмкость буфера"},
        )
    capacity_kb = round(
        max(MIN_BUFFER_CAPACITY_KB, min(capacity_kb, MAX_BUFFER_CAPACITY_KB)), 1
    )
    runtime_state["buffer_capacity_kb"] = capacity_kb
    return JSONResponse(
        content={
            "status": "ok",
            "buffer_capacity_kb": capacity_kb,
            "max_buffer_capacity_kb": MAX_BUFFER_CAPACITY_KB,
        }
    )


@router.post("/set_buffer_rate")
async def set_buffer_rate(rate_ratio: float = DEFAULT_BUFFER_RATE_RATIO):
    """
    Скорость обработки буфера очереди — во сколько раз от веса сообщений
    в секунду (0–10): за секунду сеть успевает обработать rate_ratio × (вес
    сообщений в секунду). 1.0 — ровно успевает, меньше 1 — буфер копится и
    переполняется, больше 1 — быстрее, чем нужно, буфер не растёт.
    """
    if rate_ratio != rate_ratio:  # NaN
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Некорректная скорость обработки"},
        )
    rate_ratio = round(
        max(MIN_BUFFER_RATE_RATIO, min(rate_ratio, MAX_BUFFER_RATE_RATIO)), 2
    )
    runtime_state["buffer_rate_ratio"] = rate_ratio
    return JSONResponse(
        content={
            "status": "ok",
            "buffer_rate_ratio": rate_ratio,
            "max_buffer_rate_ratio": MAX_BUFFER_RATE_RATIO,
        }
    )


@router.post("/set_count")
async def set_stations_count(count: int = 100, moving_count: int | None = None):
    """
    Удаляет все станции и создаёт заново указанное количество
    демо-станций (от 1 до 2000). Первые moving_count станций —
    движущиеся (type_st=1), остальные — стационарные (type_st=0).
    По умолчанию движущихся нет: все станции стационарные.
    """
    count = max(1, min(count, 2000))
    if moving_count is None:
        moving_count = 0
    moving_count = max(0, min(moving_count, count))

    rng = np.random.default_rng(45)
    tlv = [10, 30]
    low = GENERATION_LOW
    high = GENERATION_HIGH
    points = land_points_from(rng, low, high, count)
    pm = np.round(rng.gamma((3, 5), (2, 4), (count, 2)), 2)

    async with async_session_maker() as session:
        # полностью чистим старые станции и поведение с перезапуском счётчика id
        await session.execute(
            text("TRUNCATE stations_behaviors, stations RESTART IDENTITY CASCADE")
        )
        # сбрасываем кеш маршрутов, чтобы новые станции строили свои траектории
        reset_movement_state()
        for idx, tpl in enumerate(zip(points, pm), start=1):
            # первые moving_count станций — движущиеся, создаются с полным зарядом
            is_moving = idx <= moving_count
            session.add(
                Stations(
                    id=idx,
                    type_st=1 if is_moving else 0,
                    battery_life=FULL_BATTERY_PCT,
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

    runtime_state["mode"] = ""
    runtime_state["cluster_count"] = 0
    runtime_state["stations_count"] = count
    runtime_state["fake_pollutions"] = 0

    return JSONResponse(
        content={"status": "ok", "stations_count": count, "moving_count": moving_count}
    )

@router.post("/add")
async def add_station(
    latitude: float,
    longitude: float,
    type_st: int = 0,
    battery: float = FULL_BATTERY_PCT,
    pm25: float | None = None,
    pm10: float | None = None,
):
    """Добавляет одно устройство в указанную точку (клик по карте).

    type_st: 0 — стационарное, 1 — движущееся (создаётся с параметрами
    траектории, как в /set_count). pm25/pm10 — стартовые концентрации,
    по умолчанию — случайные фоновые значения. Существующие станции
    и их id не затрагиваются: новое устройство получает следующий id.
    """
    if latitude != latitude or longitude != longitude:  # NaN
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Некорректные координаты"},
        )
    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "message": "Координаты вне допустимого диапазона",
            },
        )
    type_st = 1 if int(type_st) == 1 else 0
    battery = round(max(0.0, min(float(battery), FULL_BATTERY_PCT)), 2)
    pm25 = (
        round(float(pm25), 2)
        if pm25 is not None
        else round(uniform(0.5, 10.0), 2)
    )
    pm10 = (
        round(float(pm10), 2)
        if pm10 is not None
        else round(uniform(0.5, 12.0), 2)
    )
    over_tlv = int(pm25 > 25 or pm10 > 50)

    async with async_session_maker() as session:
        max_id = (
            await session.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM stations")
            )
        ).scalar_one()
        new_id = int(max_id) + 1
        session.add(
            Stations(
                id=new_id,
                type_st=type_st,
                battery_life=battery,
                latitude=latitude,
                longitude=longitude,
                PM_2_5=pm25,
                PM_10=pm10,
                overTLV=over_tlv,
            )
        )
        # движущемуся устройству нужна траектория (те же параметры,
        # что у станций из /set_count)
        if type_st == 1:
            session.add(
                StationBehavior(
                    station_id=new_id,
                    radius=round(float(uniform(0.0006, 0.0035)), 6),
                    speed=round(float(uniform(3.2, 16.0)), 2),
                    progress=round(float(uniform(0.0, 11.99)), 2),
                )
            )
        await session.commit()

    # сбрасываем кеш маршрутов, чтобы новое движущееся устройство
    # сразу получило траекторию
    reset_movement_state()

    return JSONResponse(
        content={
            "status": "ok",
            "station_id": new_id,
            "latitude": latitude,
            "longitude": longitude,
            "type_st": type_st,
            "battery_life": battery,
            "PM_2_5": pm25,
            "PM_10": pm10,
            "overTLV": over_tlv,
        }
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
    variant = "battery_life" if mode == "battery_life" else ("head" if mode else None)
    result = await recluster(capacity=capacity, variant=variant)
    print(runtime_state)
    return JSONResponse(content=result)




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
            # хэд зоны — первое устройство полосы (оно же передаёт в сеть)
            "head": member_ids[0] if member_ids else None,
            "stations": member_ids
        })
    
    runtime_state["stations_count"] = len(stations)
    runtime_state["mode"] = "timezone"
    runtime_state["timezone_count"] = num_zones
    # в глобальную сеть передаёт по одному устройству от зоны
    runtime_state["timezone_zone_heads"] = [
        z["stations"][0] for z in zones if z["stations"]
    ]

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