"""Smoke-тест интеграции СИГМА на локальном моке.

Запуск из каталога backend:
    ..\\venv\\Scripts\\python.exe tests\\sigma_smoke.py

Проверяет протокол клиента (токен, регистрация источника, отправка,
чтение, PATCH), сборку сообщений, правила уровней и детектор переходов.
Не требует БД, Redis и доступа к реальной СИГМЕ.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

import requests
import uvicorn

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (BACKEND, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

from sigma_mock import app as mock_app  # noqa: E402

from app.integrations.sigma import rules, schema  # noqa: E402
from app.integrations.sigma.client import SigmaClient  # noqa: E402
from app.integrations.sigma.config import SigmaConfig  # noqa: E402
from app.integrations.sigma.detector import SigmaDetector  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS  {name}")
        return
    FAILURES.append(name)
    print(f"FAIL  {name}" + (f": {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_config(base: str, source_id: str = "") -> SigmaConfig:
    return SigmaConfig(
        endpoint_url=f"{base}/api-gateway",
        keycloak_url=f"{base}/keycloak",
        realm="sigma",
        app_id="11111111-1111-1111-1111-111111111111",
        secret_key="test-secret",
        source_id=source_id,
        source_name="nsk-eco-test",
        source_domain="nsk-eco",
        enabled=True,
        test_enabled=True,
        demo_enrich=False,
        snapshot_interval=3600.0,
        poll_interval=0.1,
        station_cooldown=0.0,
        network_cooldown=0.0,
        max_events_per_minute=1000,
        max_attempts=2,
        outbox_max=100,
        outbox_ttl=3600,
        connect_timeout=2.0,
        read_timeout=5.0,
    )


def start_mock() -> tuple[str, uvicorn.Server, threading.Thread]:
    port = free_port()
    config = uvicorn.Config(
        mock_app, host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
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


def test_protocol(base: str) -> None:
    config = make_config(base)
    client = SigmaClient(config)

    app_card = client.register_application(
        user_token="user-token",
        user_id="user-1",
        name="nsk-eco-dashboard",
        description="Тестовое приложение",
        url="https://eco.curxtor.ru",
        secret_key="secret",
    )
    check("регистрация приложения", bool(app_card.get("app_id")))

    source = client.register_source(
        user_token="user-token",
        name="nsk-eco-test",
        description="Станции NSK_ECO",
        domain="nsk-eco",
        ip="0.0.0.0/0",
        active=True,
        data_schema=schema.SOURCE_SCHEMA,
    )
    source_id = source.get("source_id") or ""
    check("регистрация источника", bool(source_id), str(source))
    if not source_id:
        return
    config.source_id = source_id

    card = client.get_source()
    check("карточка источника active", bool(card.get("active")))

    station = {
        "id": 42,
        "latitude": 55.0302,
        "longitude": 82.9204,
        "PM_2_5": 38.4,
        "PM_10": 61.2,
        "overTLV": True,
        "type_st": 0,
        "battery_life": 80.0,
    }
    msg = schema.station_event("pollution.exceeded", station)
    check(
        "сообщение сериализуется без NaN",
        isinstance(json.dumps(msg, allow_nan=False), str),
    )

    result = client.send_event(msg)
    check("отправка события", result == "ok")
    check(
        "токен запрошен один раз",
        client.tokens.requests_count == 1,
        str(client.tokens.requests_count),
    )

    payload = msg["payload"]
    correlation_id = payload["correlation_id"]
    events = client.list_events(processed=False, order="desc", limit=100)
    found = [
        event
        for event in events
        if (event.get("msg") or {}).get("payload", {}).get("correlation_id")
        == correlation_id
    ]
    check("событие видно в processed=false", len(found) == 1, str(len(found)))

    # Повторная отправка не должна запрашивать новый токен.
    client.send_event(schema.station_event("pollution.normalized", station))
    check(
        "токен закэширован",
        client.tokens.requests_count == 1,
        str(client.tokens.requests_count),
    )

    # Принудительно протухаем токен: клиент должен обновить его и повторить.
    requests.post(f"{base}/mock/expire-token", timeout=2)
    client.send_event(schema.station_event("pollution.exceeded", station))
    check(
        "токен обновлён после 401",
        client.tokens.requests_count == 2,
        str(client.tokens.requests_count),
    )

    if found:
        event_id = int(found[0]["id"])
        regulations = rules.regulations_for(38.4, 61.2)
        client.patch_event(event_id, level="WARN", regulations=regulations)
        processed = client.list_events(
            processed=True, level="WARN", order="desc", limit=100
        )
        check(
            "PATCH уровня виден в processed=true",
            any(int(event["id"]) == event_id for event in processed),
        )
        count = client.count_events(processed="true", level="WARN")
        check("count событий", count >= 1, str(count))

    state = requests.get(f"{base}/mock/state", timeout=2).json()
    same_correlation = [
        event
        for event in state["events"]
        if (event.get("msg") or {}).get("payload", {}).get("correlation_id")
        == payload["correlation_id"]
    ]
    check(
        "повторная отправка без дублей в моке",
        len(same_correlation) == 1,
        str(len(same_correlation)),
    )

    client.close()


def test_detector() -> None:
    config = make_config("http://127.0.0.1:1", source_id="test-source")
    config.snapshot_interval = 3600.0
    detector = SigmaDetector(config)

    stations = [
        {"id": 1, "overTLV": False, "PM_2_5": 10.0, "PM_10": 20.0},
        {"id": 2, "overTLV": False, "PM_2_5": 12.0, "PM_10": 22.0},
    ]
    runtime = {
        "limit_exceeded": False,
        "buffer_overflow": False,
        "stations_count": 2,
        "active_count": 2,
        "silent_count": 0,
        "moving_count": 0,
        "danger_cluster_count": 0,
        "messages_per_second": 2.0,
        "weight_per_second_kb": 2.8,
        "buffer_fill_percent": 0.0,
        "buffer_used_kb": 0.0,
        "mode": "cluster_head",
    }

    check("детектор: базовая линия без событий", detector.observe(stations, runtime) == [])

    stations[0]["overTLV"] = True
    events = detector.observe(stations, runtime)
    check(
        "детектор: превышение",
        [event["payload"]["event_type"] for event in events]
        == ["pollution.exceeded"],
        str([event["payload"]["event_type"] for event in events]),
    )

    check("детектор: нет повтора на каждом тике", detector.observe(stations, runtime) == [])

    stations[0]["overTLV"] = False
    events = detector.observe(stations, runtime)
    check(
        "детектор: возврат в норму",
        [event["payload"]["event_type"] for event in events]
        == ["pollution.normalized"],
        str([event["payload"]["event_type"] for event in events]),
    )

    runtime["limit_exceeded"] = True
    events = detector.observe(stations, runtime)
    check(
        "детектор: лимит сети",
        [event["payload"]["event_type"] for event in events]
        == ["network.limit_exceeded"],
        str([event["payload"]["event_type"] for event in events]),
    )

    runtime["buffer_overflow"] = True
    events = detector.observe(stations, runtime)
    check(
        "детектор: переполнение буфера",
        [event["payload"]["event_type"] for event in events]
        == ["network.buffer_overflow"],
        str([event["payload"]["event_type"] for event in events]),
    )

    detector.config.snapshot_interval = 0.001
    detector._last_snapshot = 0.0
    events = detector.observe(stations, runtime)
    check(
        "детектор: срез сети",
        [event["payload"]["event_type"] for event in events]
        == ["network.snapshot"],
        str([event["payload"]["event_type"] for event in events]),
    )


def test_rules_and_schema() -> None:
    check(
        "правила: норма",
        rules.severity_hint(10.0, 20.0) == "INFO",
        rules.severity_hint(10.0, 20.0),
    )
    check(
        "правила: превышение",
        rules.severity_hint(40.0, 20.0) == "WARN",
        rules.severity_hint(40.0, 20.0),
    )
    check(
        "правила: критично",
        rules.severity_hint(60.0, 60.0) == "CRIT",
        rules.severity_hint(60.0, 60.0),
    )
    check(
        "правила: рекомендации при норме пусты",
        rules.recommendations_for(10.0, 20.0) == [],
    )
    check(
        "схема источника сериализуется",
        isinstance(json.dumps(schema.SOURCE_SCHEMA), str),
    )

    test_msg = schema.test_event()
    check(
        "тестовое событие: тип и correlation_id",
        test_msg["payload"]["event_type"] == "sigma.test"
        and len(test_msg["payload"]["correlation_id"]) == 36,
    )


def main() -> int:
    base, server, thread = start_mock()
    try:
        print(f"мок СИГМА: {base}")
        test_rules_and_schema()
        test_protocol(base)
        test_detector()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)} — {', '.join(FAILURES)}")
        return 1
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
