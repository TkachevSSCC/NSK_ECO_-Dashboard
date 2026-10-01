
import asyncio
from datetime import datetime
from random import uniform

from app.services.station_service import StationService
from app.services.cluster_service import recluster
from app.metrics.calculator import (
    calculate_messages_per_second,
    count_out_of_cluster,
    count_danger_clusters,
    danger_cluster_members,
    global_sender_ids,
    DANGER_CLUSTER_MSG_MULTIPLIER,
)
from app.metrics.runtime import runtime_metrics
from app.state.runtime import runtime_state

# размер одного сообщения в килобайтах (умножается на коэффициент
# градации загрязнения, см. MSG_WEIGHT_STEPS)
GLOBAL_MSG_KB = 1.0  # сообщение в глобальную сеть
ZONE_MSG_KB = 0.4  # сообщение в пределах зоны
# диапазон настройки веса сообщения, КБ (меняется из интерфейса)
MIN_MSG_KB = 0.0
MAX_MSG_KB = 100.0

# расход батареи за одно сообщение, процент заряда
BATTERY_DRAIN_GLOBAL = 0.1  # сообщение в глобальную сеть
BATTERY_DRAIN_ZONE = 0.06  # сообщение в пределах зоны
# разброс списания: ставка умножается на случайный коэффициент
# 1 ± BATTERY_DRAIN_JITTER, поэтому у станций заряд уходит неравномерно
BATTERY_DRAIN_JITTER = 0.05

# тип устройства «движущееся» (0 — стационарное, 1 — движущееся, 2 — хэд)
MOVING_TYPE = 1

# ёмкость буфера очереди по умолчанию, КБ (меняется из интерфейса)
DEFAULT_BUFFER_CAPACITY_KB = 1000.0
# диапазон ёмкости буфера, КБ
MIN_BUFFER_CAPACITY_KB = 0.0
MAX_BUFFER_CAPACITY_KB = 1000000.0

# скорость обработки буфера задаётся как во сколько раз сеть успевает
# обработать вес сообщений в секунду (меняется из интерфейса):
# 1.0 — ровно успевает, меньше 1 — буфер копится и переполняется,
# больше 1 — сеть быстрее, чем нужно, буфер не растёт
DEFAULT_BUFFER_RATE_RATIO = 1.0
# диапазон скорости обработки буфера, во сколько раз от веса сообщений/с
MIN_BUFFER_RATE_RATIO = 0.0
MAX_BUFFER_RATE_RATIO = 10.0

# интервал метрик, сек (тик) — за тик устройства передают по одному
# сообщению, поэтому тик = секунда и метрики в секунду считаются точно
TICK_SECONDS = 1

# ---- вес сообщения зависит от уровня загрязнения ----
# градация по сумме PM2.5 + PM10 (сумма ПДК в проекте: 25 + 50 = 75):
# чем грязнее вокруг станции, тем больше данных в её сообщении
PM_SUM_TLV = 75.0
MSG_WEIGHT_STEPS = (
    (PM_SUM_TLV / 3.0, 1.0),  # сумма ниже 1/3 ПДК — обычный вес
    (PM_SUM_TLV * 2.0 / 3.0, 1.2),  # 1/3 … 2/3 ПДК
    (PM_SUM_TLV, 1.5),  # 2/3 … ПДК
)
MSG_WEIGHT_TOP = 2.0  # сумма выше ПДК — вес вдвое больше базового


def pollution_weight_factor(pm25, pm10) -> float:
    """Коэффициент веса сообщения по сумме PM2.5 + PM10 (ступенчато)."""
    total = float(pm25 or 0.0) + float(pm10 or 0.0)
    for limit, factor in MSG_WEIGHT_STEPS:
        if total <= limit:
            return factor
    return MSG_WEIGHT_TOP


def mean_weight_factor(stations: list[dict], ids: set[int] | None = None) -> float:
    """Средний коэффициент веса по загрязнению среди отправителей.

    ids — множество id устройств, передающих в глобальную сеть; None
    берёт все переданные станции. Если отправителей определить не удалось
    (пусто), возвращаем 1.0 — базовый вес, чтобы метрики не обнулялись.
    """
    if not stations:
        return 1.0
    if ids is None:
        selected = stations
    else:
        selected = [s for s in stations if s["id"] in ids]
        if not selected:
            return 1.0
    total = sum(
        pollution_weight_factor(s.get("PM_2_5"), s.get("PM_10")) for s in selected
    )
    return total / len(selected)


# порог: доля движущихся устройств вне своего кластера
MOVING_OUT_LIMIT = 0.5  # 50%


async def trigger_recluster():
    """Перестраивает кластеры и пересчитывает кластер-хэды
    (запускается один раз при превышении лимита)."""
    if runtime_state.get("rebuild_in_progress"):
        return
    runtime_state["rebuild_in_progress"] = True
    try:
        variant = runtime_state.get("cluster_variant", "head")
        await recluster(capacity=10, variant=variant)
    except Exception as exc:  # noqa: BLE001
        print(f"recluster error: {exc}")
        runtime_state["limit_alert_sent"] = False
    finally:
        runtime_state["rebuild_in_progress"] = False


async def metrics_loop():
    while True:
        try:
            await metrics_tick()
        except Exception as exc:  # noqa: BLE001
            # сбойный тик (например, конфликт с обновлением станций)
            # не должен убивать цикл: ждём следующий тик
            print(f"metrics tick error: {exc}")
        await asyncio.sleep(TICK_SECONDS)


async def metrics_tick():
    """Один тик метрик: сообщения, заряд, буфер очереди."""
    stations_raw = await StationService.find_all()

    stations = [
        {
            "id": s.id,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "PM_2_5": s.PM_2_5,
            "PM_10": s.PM_10,
            "overTLV": s.overTLV,
            "type_st": s.type_st,
            "battery_life": s.battery_life,
        }
        for s in stations_raw
    ]

    mode = runtime_state["mode"]

    cluster_count = runtime_state["cluster_count"]

    # устройства с нулевым зарядом молчат: они не передают ни в глобальную
    # сеть, ни в пределах зоны, поэтому в метриках не участвуют
    active = [s for s in stations if float(s["battery_life"] or 0.0) > 0.0]

    # временные кластеры (станции с суммой PM2.5+PM10 > 200) передают
    # DANGER_CLUSTER_MESSAGES сообщений в глобальную сеть дополнительно
    # к основному режиму
    danger_cluster_count = count_danger_clusters(active)

    mps = calculate_messages_per_second(
        stations=active,
        mode=mode,
        cluster_count=cluster_count,
        fake_pollutions=runtime_state["fake_pollutions"],
        timezone_count=runtime_state.get("timezone_count", 0),
        cluster_centroids=runtime_state.get("cluster_centroids"),
        cluster_radii=runtime_state.get("cluster_radii"),
        danger_cluster_count=danger_cluster_count,
    )

    # ---- лимит: mps (в глобальную сеть) > кластер-хэды + half moving ----
    limit_exceeded = False
    moving_count = sum(1 for s in active if s["type_st"] == MOVING_TYPE)
    if mode == "cluster_head" and moving_count > 0:
        threshold = cluster_count + moving_count / 2
        runtime_metrics["limit_threshold"] = round(threshold, 3)
        if mps > threshold:
            limit_exceeded = True
    else:
        runtime_metrics.pop("limit_threshold", None)

    runtime_metrics["limit_exceeded"] = limit_exceeded

    if limit_exceeded and not runtime_state.get("limit_alert_sent"):
        # один раз за событие: сообщение на фронт + перестроение кластеров
        runtime_state["limit_alert_sent"] = True
        asyncio.create_task(trigger_recluster())
    elif not limit_exceeded:
        # лимит снова в норме — можно реагировать на следующее событие
        runtime_state["limit_alert_sent"] = False

    # накапливаем сообщения за тик (тик = 1 сек)
    global_messages = mps * TICK_SECONDS

    # сообщения в пределах зоны: устройства, которые не передают в сеть.
    # Пока есть живые устройства — не меньше 1 за тик («передают в пределах
    # зоны» не опускается до нуля); если живых нет — трафика нет вовсе,
    # и буфер очереди обнуляется
    zone_messages = max(1, len(active) - mps) * TICK_SECONDS if active else 0.0

    # ---- расход батареи: устройство, передавшее сообщение в глобальную
    # сеть, теряет BATTERY_DRAIN_GLOBAL процентов заряда, передавшее
    # в пределах зоны — BATTERY_DRAIN_ZONE ----
    danger_ids = danger_cluster_members(active)
    danger_set = set(danger_ids)
    global_ids = global_sender_ids(
        active,
        mode,
        centroids=runtime_state.get("cluster_centroids"),
        radii=runtime_state.get("cluster_radii"),
        danger_ids=danger_ids,
        timezone_heads=runtime_state.get("timezone_zone_heads", []),
    )
    # Вес одного сообщения зависит от уровня загрязнения на станции:
    # сумма PM2.5 + PM10 задаёт ступенчатый коэффициент (1.0 / 1.2 / 1.5 /
    # 2.0), поэтому в грязном районе трафик тяжелее даже при том же числе
    # сообщений. Базовый вес: GLOBAL_MSG_KB для сети, ZONE_MSG_KB для зоны.
    global_factor = mean_weight_factor(active, global_ids)
    zone_ids = {s["id"] for s in active} - global_ids
    zone_factor = mean_weight_factor(active, zone_ids) if zone_ids else 1.0

    # Базовый вес (КБ) одного сообщения в глобальную сеть и в пределах
    # зоны может меняться из интерфейса («Управление») — runtime_state,
    # по умолчанию константы GLOBAL_MSG_KB / ZONE_MSG_KB
    global_msg_kb = runtime_state.get("global_msg_kb", GLOBAL_MSG_KB)
    zone_msg_kb = runtime_state.get("zone_msg_kb", ZONE_MSG_KB)

    produced_global_kb = global_messages * global_msg_kb * global_factor
    produced_zone_kb = zone_messages * zone_msg_kb * zone_factor
    runtime_metrics["global_weight_factor"] = round(global_factor, 3)
    runtime_metrics["zone_weight_factor"] = round(zone_factor, 3)

    # списание заряда: базовая ставка за передачу × случайный коэффициент
    # ±BATTERY_DRAIN_JITTER, поэтому у каждой станции свой расход
    drain_by_id: dict[int, float] = {}
    global_drained = 0.0
    zone_drained = 0.0
    for s in active:
        sid = s["id"]
        if s["type_st"] == 0:
            # стационарные устройства питаются постоянно и не разряжаются
            drain_by_id[sid] = 0.0
            continue
        is_global = sid in global_ids
        base = BATTERY_DRAIN_GLOBAL if is_global else BATTERY_DRAIN_ZONE
        # устройство из временного кластера передаёт в сеть в
        # DANGER_CLUSTER_MSG_MULTIPLIER раз чаще — столько же списываем
        if is_global and sid in danger_set:
            base *= DANGER_CLUSTER_MSG_MULTIPLIER
        drain = base * uniform(1 - BATTERY_DRAIN_JITTER, 1 + BATTERY_DRAIN_JITTER)
        drain_by_id[sid] = drain
        if is_global:
            global_drained += drain
        else:
            zone_drained += drain

    await StationService.drain_battery(drain_by_id)
    # заряд после списания за тик
    batteries = [
        max(0.0, float(s["battery_life"] or 0.0) - drain_by_id.get(s["id"], 0.0))
        for s in stations
    ]
    runtime_metrics["battery_drained_global_pct"] = round(global_drained, 2)
    runtime_metrics["battery_drained_zone_pct"] = round(zone_drained, 2)
    runtime_metrics["battery_drained_total_pct"] = round(
        global_drained + zone_drained, 2
    )
    if batteries:
        runtime_metrics["battery_min"] = round(min(batteries), 2)
        runtime_metrics["battery_max"] = round(max(batteries), 2)
        runtime_metrics["battery_dead_count"] = sum(1 for b in batteries if b <= 0.0)

    # общий заряд движущихся станций (после списания за тик)
    moving_batteries = [
        b for b, s in zip(batteries, stations) if s["type_st"] == MOVING_TYPE
    ]
    runtime_metrics["moving_count"] = len(moving_batteries)
    runtime_metrics["moving_battery_total"] = round(sum(moving_batteries), 1)
    # средний заряд считаем только по движущимся: у стационарных своё питание,
    # а если движущихся нет — среднее не считаем (None, а не 0)
    moving_battery_avg = (
        round(sum(moving_batteries) / len(moving_batteries), 2)
        if moving_batteries
        else None
    )
    runtime_metrics["moving_battery_avg"] = moving_battery_avg
    runtime_metrics["battery_avg"] = moving_battery_avg

    # ---- буфер очереди: устройства складывают сообщения в буфер, сеть
    # обрабатывает (отправляет) накопленное со скоростью buffer_rate_ratio —
    # во столько раз от веса сообщений в секунду. За тик сеть успевает
    # отдать rate_ratio × (вес, предъявленный за тик), остаток остаётся
    # в буфере. Не поместившееся в буфер (переполнение) теряется
    # и не попадает в счётчики ----
    capacity_kb = runtime_state.get(
        "buffer_capacity_kb", DEFAULT_BUFFER_CAPACITY_KB
    )
    rate_ratio = runtime_state.get(
        "buffer_rate_ratio", DEFAULT_BUFFER_RATE_RATIO
    )
    produced_kb = produced_global_kb + produced_zone_kb
    # на что считаем пропускную способность сети: на вес этого тика, а если
    # устройства замолчали — на последний ненулевой вес, иначе хвост в буфере
    # не смог бы дойти до сети никогда
    offered_kb = produced_kb
    if offered_kb > 0:
        runtime_state["buffer_offered_kb_ref"] = offered_kb
    else:
        offered_kb = float(runtime_state.get("buffer_offered_kb_ref", 0.0))

    level_kb = float(runtime_state.get("buffer_level_kb", 0.0))
    free_kb = max(0.0, capacity_kb - level_kb)
    added_kb = min(produced_kb, free_kb)
    dropped_kb = produced_kb - added_kb

    # сеть обрабатывает буфер: сперва то, что добавили, затем остаток
    in_buffer_kb = level_kb + added_kb
    budget_kb = rate_ratio * offered_kb * TICK_SECONDS
    processing_kb_s = budget_kb / TICK_SECONDS
    sent_kb = min(in_buffer_kb, budget_kb)
    level_kb = in_buffer_kb - sent_kb
    runtime_state["buffer_level_kb"] = level_kb

    used_kb = level_kb
    # в счётчики идёт только реально обработанный вес: и переполнение,
    # и не отправленное за тик из буфера в сеть не засчитываются
    sent_ratio = (sent_kb / produced_kb) if produced_kb > 0 else 0.0

    # вес сообщений в секунду, КБ/с: сколько веса сеть реально обработала
    # из буфера за тик. Считаем по тем же сообщениям, что и накопительный
    # «общий вес», поэтому значение меняется вместе с обработкой и потерями
    weight_per_second_kb = sent_kb / TICK_SECONDS

    # фактический вес тика (без потерь) переносим в накопительные счётчики
    # пропорционально доле обработанного — иначе «общий вес» разошёлся бы
    # с весом в секунду
    sent_global_kb = produced_global_kb * sent_ratio
    sent_zone_kb = produced_zone_kb * sent_ratio

    runtime_metrics["total_messages"] += global_messages * sent_ratio
    runtime_metrics["total_zone_messages"] += zone_messages * sent_ratio

    # общий вес сообщений (килобайты): сумма веса реально обработанных
    # сообщений с фиксированным весом каждого типа передачи
    runtime_metrics["total_message_weight_kb"] += sent_global_kb + sent_zone_kb

    # ---- состояние буфера и потери ----
    runtime_metrics["buffer_capacity_kb"] = capacity_kb
    runtime_metrics["buffer_rate_ratio"] = rate_ratio
    runtime_metrics["buffer_processing_kb_s"] = round(processing_kb_s, 2)
    runtime_metrics["buffer_used_kb"] = round(used_kb, 2)
    runtime_metrics["buffer_fill_percent"] = (
        round(used_kb / capacity_kb * 100, 1) if capacity_kb > 0 else 100.0
    )
    runtime_metrics["dropped_weight_kb"] = round(dropped_kb, 2)
    runtime_metrics["total_dropped_weight_kb"] += dropped_kb
    runtime_metrics["buffer_overflow"] = dropped_kb > 0

    runtime_metrics.update({
        "timestamp": datetime.utcnow().isoformat(),
        "messages_per_second": mps,
        "weight_per_second_kb": round(weight_per_second_kb, 3),
        "mode": mode,
        "stations_count": len(stations),
        "active_count": len(active),
        "silent_count": len(stations) - len(active),
        "danger_cluster_count": danger_cluster_count,
    })
