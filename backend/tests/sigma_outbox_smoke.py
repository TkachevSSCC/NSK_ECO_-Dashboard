"""Проверка Redis-outbox: очередь, повторы и dead-letter.

Требуется локальный Redis (тот же, что в backend/.env). Тест работает в
отдельной БД Redis и не трогает рабочие ключи. Запуск из backend:
    ..\\venv\\Scripts\\python.exe tests\\sigma_outbox_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time

import redis
import requests
import uvicorn
from redis.connection import parse_url

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (BACKEND, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

from sigma_mock import app as mock_app  # noqa: E402
from sigma_smoke import check, make_config  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.integrations.sigma import schema  # noqa: E402
from app.integrations.sigma.client import SigmaClient  # noqa: E402
from app.integrations.sigma.service import SigmaService  # noqa: E402


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_mock() -> tuple[str, uvicorn.Server, threading.Thread]:
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            mock_app, host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if requests.get(f"{base}/healthz", timeout=0.5).ok:
                return base, server, thread
        except requests.RequestException:
            pass
        time.sleep(0.05)
    raise RuntimeError("мок СИГМА не поднялся")


def open_test_redis():
    kwargs = parse_url(settings.CELERY_BROKER_URL)
    kwargs["db"] = 15
    client = redis.Redis(**kwargs, decode_responses=True)
    client.flushdb()
    return client


def count_correlation(base: str, correlation_id: str) -> int:
    state = requests.get(f"{base}/mock/state", timeout=2).json()
    return len(
        [
            event
            for event in state["events"]
            if (event.get("msg") or {})
            .get("payload", {})
            .get("correlation_id")
            == correlation_id
        ]
    )


async def run_checks(base: str) -> None:
    config = make_config(base)
    bootstrap = SigmaClient(config)
    source = bootstrap.register_source(
        user_token="user-token",
        name="nsk-eco-outbox",
        description="Outbox-тест",
        domain="nsk-eco",
        ip="0.0.0.0/0",
        active=True,
        data_schema=schema.SOURCE_SCHEMA,
    )
    config.source_id = str(source.get("source_id") or "")
    bootstrap.close()
    check("outbox: источник зарегистрирован", bool(config.source_id))

    service = SigmaService(config)
    test_redis = open_test_redis()
    service.outbox.redis = test_redis
    test_redis.flushdb()

    station = {
        "id": 7,
        "latitude": 55.0,
        "longitude": 82.9,
        "PM_2_5": 40.0,
        "PM_10": 70.0,
        "overTLV": True,
        "type_st": 0,
        "battery_life": 50.0,
    }
    message = schema.station_event("pollution.exceeded", station)
    correlation_id = message["payload"]["correlation_id"]

    accepted = await service.enqueue_many([message])
    check("outbox: событие поставлено в очередь", accepted == 1, str(accepted))
    processed = await service.process_batch()
    check("outbox: событие обработано", processed == 1, str(processed))
    check(
        "outbox: событие принято моком",
        count_correlation(base, correlation_id) == 1,
    )

    again = await service.enqueue_many([message])
    check("outbox: дедупликация", again == 0, str(again))

    status = await service.status()
    check(
        "outbox: счётчик sent",
        status["counters"]["sent"] == 1,
        str(status["counters"]),
    )
    check(
        "outbox: очередь пуста",
        status["queue_size"] == 0,
        str(status["queue_size"]),
    )

    # --- повтор после 500 ---
    requests.post(f"{base}/mock/fail-next?count=1", timeout=2)
    retry_message = schema.station_event("pollution.normalized", station)
    retry_correlation = retry_message["payload"]["correlation_id"]
    await service.enqueue_many([retry_message])
    await service.process_batch()
    status = await service.status()
    check(
        "outbox: 500 уводит событие в отложенные",
        status["queues"]["retry"] == 1,
        str(status["queues"]),
    )
    members = test_redis.zrange(service.outbox.RETRY, 0, -1)
    check("outbox: в retry один элемент", len(members) == 1, str(len(members)))
    if members:
        test_redis.zadd(service.outbox.RETRY, {members[0]: 0})
        await service.process_batch()
        check(
            "outbox: повтор доставлен",
            count_correlation(base, retry_correlation) == 1,
        )
        status = await service.status()
        check(
            "outbox: retry пуст после доставки",
            status["queues"]["retry"] == 0,
            str(status["queues"]),
        )
        check(
            "outbox: sent = 2",
            status["counters"]["sent"] == 2,
            str(status["counters"]),
        )

    # --- 422 уводит в dead-letter ---
    bad = {
        "timestamp": schema.now_iso(),
        "payload": {
            "event_type": "sigma.bad",
            "correlation_id": schema.new_correlation_id(),
            "schema_version": 1,
        },
    }
    await service.enqueue_many([bad])
    await service.process_batch()
    status = await service.status()
    check(
        "outbox: 422 уводит в dead-letter",
        status["counters"]["dead"] == 1,
        str(status["counters"]),
    )
    check(
        "outbox: dead-letter содержит запись",
        len(await service.dead_items()) == 1,
    )

    test_redis.flushdb()
    test_redis.close()
    service.close()


def main() -> int:
    from sigma_smoke import FAILURES

    base, server, thread = start_mock()
    test_client = None
    try:
        print(f"мок СИГМА: {base}")
        # проверяем доступность Redis до запуска сценария
        test_client = open_test_redis()
        test_client.flushdb()
        test_client.close()
        asyncio.run(run_checks(base))
    except Exception as exc:  # noqa: BLE001
        print(f"ОШИБКА: {type(exc).__name__}: {exc}")
        return 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)} — {', '.join(FAILURES)}")
        return 1
    print("ВСЕ ПРОВЕРКИ OUTBOX ПРОЙДЕНЫ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
