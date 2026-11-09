"""REST-эндпоинты интеграции с СИГМА для вкладки «Управление»."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.integrations.sigma.client import SigmaError
from app.integrations.sigma.integration import sigma_integration

router = APIRouter(prefix="/sigma", tags=["sigma"])


def _require_configured() -> None:
    config = sigma_integration.config
    if not config.credentials_ready:
        missing = ", ".join(config.missing) or "SIGMA_*"
        raise HTTPException(
            status_code=409,
            detail=f"Интеграция с СИГМА не настроена: задайте {missing}",
        )
    if not config.enabled:
        raise HTTPException(
            status_code=409,
            detail="Интеграция с СИГМА выключена (SIGMA_ENABLED=false)",
        )


@router.get("/status")
async def sigma_status():
    return await sigma_integration.status()


@router.post(
    "/test",
    responses={
        403: {"description": "Тестовые события отключены"},
        409: {"description": "Интеграция не настроена или выключена"},
        502: {"description": "Ошибка отправки в платформу СИГМА"},
    },
)
async def sigma_test():
    _require_configured()
    if not sigma_integration.config.test_enabled:
        raise HTTPException(
            status_code=403,
            detail="Отправка тестовых событий отключена (SIGMA_TEST_ENABLED=false)",
        )
    try:
        return {
            "status": "ok",
            **await sigma_integration.service.send_test(),
        }
    except SigmaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc


@router.get(
    "/events",
    responses={
        409: {"description": "Интеграция не настроена или выключена"},
        502: {"description": "Ошибка запроса к платформе СИГМА"},
    },
)
async def sigma_events(
    processed: bool | None = None,
    level: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    _require_configured()
    try:
        events = await sigma_integration.service.list_events(
            processed=processed, level=level, limit=limit
        )
    except SigmaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc

    result = []
    for event in events:
        msg = event.get("msg") or {}
        payload = msg.get("payload") or {}
        result.append(
            {
                "id": event.get("id"),
                "created_at": event.get("created_at"),
                "description": msg.get("description"),
                "timestamp": msg.get("timestamp"),
                "level": event.get("level"),
                "processed": event.get("level") in ("INFO", "WARN", "CRIT"),
                "event_type": payload.get("event_type"),
                "correlation_id": payload.get("correlation_id"),
            }
        )
    return {"status": "ok", "events": result}


@router.get("/dead")
async def sigma_dead(limit: int = Query(20, ge=1, le=100)):
    if sigma_integration.service is None:
        return {"status": "ok", "items": []}
    return {
        "status": "ok",
        "items": await sigma_integration.service.dead_items(limit),
    }
