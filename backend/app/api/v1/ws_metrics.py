
from fastapi import APIRouter, WebSocket
import asyncio
from app.metrics.runtime import runtime_metrics
from app.metrics.scheduler import TICK_SECONDS

router = APIRouter()

@router.websocket("/ws/metrics")
async def metrics_ws(ws: WebSocket):
    await ws.accept()

    try:
        while True:
            await ws.send_json(runtime_metrics)
            await asyncio.sleep(TICK_SECONDS)
    except Exception:  # noqa: BLE001
        pass
