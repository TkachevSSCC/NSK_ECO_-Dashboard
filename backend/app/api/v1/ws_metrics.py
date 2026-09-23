
from fastapi import APIRouter, WebSocket
import asyncio
from app.metrics.runtime import runtime_metrics

router = APIRouter()

@router.websocket("/ws/metrics")
async def metrics_ws(ws: WebSocket):
    await ws.accept()

    # при обновлении страницы открывается новое подключение —
    # обнуляем накопительные счётчики (вес и количество сообщений)
    for key in (
        "total_messages",
        "total_zone_messages",
        "total_message_weight_kb",
    ):
        runtime_metrics[key] = 0

    try:
        while True:
            await ws.send_json(runtime_metrics)
            await asyncio.sleep(3)
    except Exception:  # noqa: BLE001
        pass
