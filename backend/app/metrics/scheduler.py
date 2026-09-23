
import asyncio
from datetime import datetime

from app.services.station_service import StationService
from app.metrics.calculator import calculate_messages_per_second
from app.metrics.runtime import runtime_metrics
from app.state.runtime import runtime_state

async def metrics_loop():
    while True:
        stations_raw = await StationService.find_all()

        stations = [
            {
                "id": s.id,
                "overTLV": s.overTLV
            }
            for s in stations_raw
        ]

        mode = runtime_state["mode"]

        cluster_count = runtime_state["cluster_count"]

        mps = calculate_messages_per_second(
            stations=stations,
            mode=mode,
            cluster_count=cluster_count, 
            fake_pollutions=runtime_state["fake_pollutions"],
            timezone_count=runtime_state.get("timezone_count", 0),
        )

        # накапливаем общее количество переданных сообщений (тик = 3 сек)
        runtime_metrics["total_messages"] += mps * 3

        # сообщения в пределах зоны: устройства, которые не передают в сеть
        zone_messages = max(0, len(stations) - mps) * 3
        runtime_metrics["total_zone_messages"] += zone_messages

        # общий вес сообщений:
        # 0.5 * сообщения в глобальную сеть + 0.2 * сообщения в пределах зоны
        runtime_metrics["total_message_weight"] = (
            0.5 * runtime_metrics["total_messages"]
            + 0.2 * runtime_metrics["total_zone_messages"]
        )

        runtime_metrics.update({
            "timestamp": datetime.utcnow().isoformat(),
            "messages_per_second": mps,
            "mode": mode,
            "stations_count": len(stations)
        })

        await asyncio.sleep(3)
