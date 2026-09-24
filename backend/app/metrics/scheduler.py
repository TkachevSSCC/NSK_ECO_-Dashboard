
import asyncio
from datetime import datetime

from app.services.station_service import StationService
from app.services.cluster_service import recluster
from app.metrics.calculator import (
    calculate_messages_per_second,
    count_out_of_cluster,
    count_danger_clusters,
)
from app.metrics.runtime import runtime_metrics
from app.state.runtime import runtime_state

# размер одного сообщения в килобайтах
GLOBAL_MSG_KB = 0.5  # сообщение в глобальную сеть
ZONE_MSG_KB = 0.2  # сообщение в пределах зоны

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
            }
            for s in stations_raw
        ]

        mode = runtime_state["mode"]

        cluster_count = runtime_state["cluster_count"]

        # временные кластеры (станции с суммой PM2.5+PM10 > 200) передают
        # сообщение в глобальную сеть дополнительно к основному режиму
        danger_cluster_count = count_danger_clusters(stations)

        mps = calculate_messages_per_second(
            stations=stations,
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
        moving_count = sum(1 for s in stations if s["type_st"] == 1)
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

        # накапливаем общее количество переданных сообщений (тик = 3 сек)
        runtime_metrics["total_messages"] += mps * 3

        # сообщения в пределах зоны: устройства, которые не передают в сеть
        zone_messages = max(0, len(stations) - mps) * 3
        runtime_metrics["total_zone_messages"] += zone_messages

        # общий вес сообщений (килобайты):
        # 0.5 КБ * сообщения в глобальную сеть + 0.2 КБ * сообщения в зоне
        runtime_metrics["total_message_weight_kb"] = (
            GLOBAL_MSG_KB * runtime_metrics["total_messages"]
            + ZONE_MSG_KB * runtime_metrics["total_zone_messages"]
        )

        runtime_metrics.update({
            "timestamp": datetime.utcnow().isoformat(),
            "messages_per_second": mps,
            "mode": mode,
            "stations_count": len(stations),
            "danger_cluster_count": danger_cluster_count,
        })

        await asyncio.sleep(3)
