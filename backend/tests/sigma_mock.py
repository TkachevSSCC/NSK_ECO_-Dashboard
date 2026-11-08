"""Локальный мок платформы СИГМА для smoke-теста интеграции.

Запускается отдельным процессом:
    python tests/sigma_mock.py 8300

Повторяет только тот контракт, который использует дашборд:
  * Keycloak token endpoint (client_credentials);
  * регистрацию приложения/источника (пользовательский токен);
  * отправку, чтение, подсчёт и PATCH событий.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="Sigma mock")

_lock = threading.Lock()
state = {
    "token_seq": 0,
    "token_requests": 0,
    "current_token": None,
    "sources": {},
    "events": [],
    "event_seq": 0,
    "fail_next": 0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _auth(request: Request) -> str:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    if token.startswith("user-"):
        return "user"
    with _lock:
        valid = token == state["current_token"]
    if not valid:
        raise HTTPException(status_code=401, detail="invalid token")
    return "app"


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/keycloak/realms/{realm}/protocol/openid-connect/token")
async def token(realm: str, request: Request):
    body = (await request.body()).decode("utf-8")
    form = {
        key: values[0]
        for key, values in parse_qs(body, keep_blank_values=True).items()
    }
    if form.get("grant_type") != "client_credentials":
        raise HTTPException(status_code=400, detail="unsupported grant_type")
    if not str(form.get("client_id", "")).startswith("app-"):
        raise HTTPException(status_code=401, detail="bad client_id")
    if not form.get("client_secret"):
        raise HTTPException(status_code=401, detail="missing client_secret")
    with _lock:
        state["token_seq"] += 1
        state["token_requests"] += 1
        token_value = f"mock-token-{state['token_seq']}"
        state["current_token"] = token_value
    return {
        "access_token": token_value,
        "token_type": "Bearer",
        "expires_in": 300,
    }


@app.post("/api-gateway/auth/users/{user_id}/apps/")
async def register_app(user_id: str, request: Request):
    role = _auth(request)
    if role != "user":
        raise HTTPException(status_code=403, detail="user token required")
    body = await request.json()
    app_id = str(uuid.uuid4())
    return {
        "app_id": app_id,
        "user_id": user_id,
        "name": body.get("name", ""),
        "description": body.get("description", ""),
        "url": body.get("url", ""),
        "created_at": _now(),
    }


@app.post("/api-gateway/events/sources/")
async def register_source(request: Request):
    role = _auth(request)
    if role != "user":
        raise HTTPException(status_code=403, detail="user token required")
    body = await request.json()
    source_id = str(uuid.uuid4())
    card = {
        "source_id": source_id,
        "name": body.get("name", ""),
        "description": body.get("description", ""),
        "domain": body.get("domain", ""),
        "ip": body.get("ip", ""),
        "active": bool(body.get("active", True)),
        "data_schema": body.get("data_schema") or {},
        "created_at": _now(),
    }
    with _lock:
        state["sources"][source_id] = card
    return JSONResponse(
        content=f"http://127.0.0.1/api-gateway/events/sources/{source_id}",
        status_code=201,
    )


@app.get("/api-gateway/events/sources/")
async def list_sources(request: Request):
    _auth(request)
    with _lock:
        return list(state["sources"].values())


@app.get("/api-gateway/events/sources/{source_id}")
async def get_source(source_id: str, request: Request):
    _auth(request)
    with _lock:
        card = state["sources"].get(source_id)
    if card is None:
        raise HTTPException(status_code=404, detail="source not found")
    return card


@app.post("/api-gateway/events/sources/{source_id}/events/")
async def send_event(source_id: str, request: Request):
    _auth(request)
    with _lock:
        source = state["sources"].get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    if not source.get("active"):
        raise HTTPException(status_code=403, detail="source is inactive")

    with _lock:
        fail = state["fail_next"] > 0
        if fail:
            state["fail_next"] -= 1
    if fail:
        raise HTTPException(status_code=500, detail="mock failure")

    body = await request.json()
    msg = body.get("msg")
    if not isinstance(msg, dict):
        raise HTTPException(status_code=422, detail="msg is required")
    payload = msg.get("payload")
    if (
        not msg.get("description")
        or not msg.get("timestamp")
        or not isinstance(payload, dict)
        or not payload.get("event_type")
        or not payload.get("correlation_id")
        or "schema_version" not in payload
    ):
        raise HTTPException(status_code=422, detail="msg does not match schema")

    with _lock:
        state["event_seq"] += 1
        event = {
            "id": state["event_seq"],
            "source_id": source_id,
            "msg": msg,
            "level": None,
            "regulations": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        state["events"].append(event)
    return JSONResponse(content="ok", status_code=201)


def _filtered_events(request: Request) -> list[dict]:
    processed = request.query_params.get("processed")
    level = request.query_params.get("level")
    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        limit = 100
    try:
        skip = int(request.query_params.get("skip", 0))
    except ValueError:
        skip = 0

    with _lock:
        events = list(reversed(state["events"]))
    if processed == "true":
        events = [
            event for event in events if event["level"] in ("INFO", "WARN", "CRIT")
        ]
    elif processed == "false":
        events = [event for event in events if event["level"] is None]
    if level:
        events = [event for event in events if event["level"] == level]
    return events[skip : skip + max(0, limit)]


@app.get("/api-gateway/events/sources/{source_id}/events/")
async def list_events(source_id: str, request: Request):
    _auth(request)
    return _filtered_events(request)


@app.get("/api-gateway/events/sources/{source_id}/events/count")
async def count_events(source_id: str, request: Request):
    _auth(request)
    return {"count": len(_filtered_events(request))}


@app.patch("/api-gateway/events/sources/{source_id}/events/{event_id}")
async def patch_event(source_id: str, event_id: int, request: Request):
    _auth(request)
    body = await request.json()
    level = body.get("level")
    if level is not None and level not in ("INFO", "WARN", "CRIT"):
        raise HTTPException(status_code=422, detail="bad level")
    with _lock:
        event = next(
            (
                item
                for item in state["events"]
                if item["id"] == event_id and item["source_id"] == source_id
            ),
            None,
        )
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        if level is not None:
            event["level"] = level
        if "regulations" in body:
            event["regulations"] = body["regulations"]
        event["updated_at"] = _now()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# служебные эндпоинты только для теста
# ---------------------------------------------------------------------------
@app.get("/mock/state")
async def mock_state():
    with _lock:
        return {
            "token_requests": state["token_requests"],
            "current_token": state["current_token"],
            "sources": list(state["sources"].values()),
            "events": state["events"],
        }


@app.post("/mock/expire-token")
async def expire_token():
    with _lock:
        state["current_token"] = "rotated-away"
    return {"ok": True}


@app.post("/mock/fail-next")
async def fail_next(count: int = 1):
    with _lock:
        state["fail_next"] = max(0, count)
    return {"ok": True}


@app.post("/mock/reset")
async def reset():
    with _lock:
        state.update(
            {
                "token_seq": 0,
                "token_requests": 0,
                "current_token": None,
                "sources": {},
                "events": [],
                "event_seq": 0,
                "fail_next": 0,
            }
        )
    return {"ok": True}


def main() -> None:
    port = 8300
    import sys

    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
