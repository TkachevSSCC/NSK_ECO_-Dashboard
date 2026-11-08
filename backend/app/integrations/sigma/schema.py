"""Сборка сообщений и JSON Schema источника событий СИГМА.

Формат сообщения описан в руководстве:
  msg = {description, timestamp, payload}
Поля description и timestamp обязательны, payload проверяется схемой
источника. Все значения приводятся к JSON-безопасному виду: без NaN,
Infinity и None.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from uuid import uuid4

from app.integrations.sigma import rules

SCHEMA_VERSION = 1
SOURCE_LABEL = "nsk-eco-dashboard"


# --------------------------------------------------------------------------
# JSON Schema источника
# --------------------------------------------------------------------------
SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "timestamp": {"type": "string", "format": "date-time"},
        "payload": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string"},
                "correlation_id": {"type": "string"},
                "schema_version": {"type": "integer"},
                "source": {"type": "string"},
                "station": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "type": {"type": "integer"},
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                    },
                },
                "measurements": {
                    "type": "object",
                    "properties": {
                        "pm25": {"type": "number"},
                        "pm10": {"type": "number"},
                        "over_tlv": {"type": "boolean"},
                        "pm25_limit": {"type": "number"},
                        "pm10_limit": {"type": "number"},
                        "pm25_ratio": {"type": "number"},
                        "pm10_ratio": {"type": "number"},
                        "pm25_avg": {"type": "number"},
                        "pm10_avg": {"type": "number"},
                        "pm25_max": {"type": "number"},
                        "pm10_max": {"type": "number"},
                        "over_tlv_count": {"type": "integer"},
                    },
                },
                "network": {
                    "type": "object",
                    "properties": {
                        "stations_total": {"type": "integer"},
                        "active": {"type": "integer"},
                        "silent": {"type": "integer"},
                        "moving": {"type": "integer"},
                        "danger_clusters": {"type": "integer"},
                        "cluster_count": {"type": "integer"},
                        "mode": {"type": "string"},
                        "messages_per_second": {"type": "number"},
                        "weight_per_second_kb": {"type": "number"},
                        "buffer_fill_percent": {"type": "number"},
                        "buffer_used_kb": {"type": "number"},
                        "buffer_overflow": {"type": "boolean"},
                        "limit_exceeded": {"type": "boolean"},
                        "dropped_weight_kb": {"type": "number"},
                    },
                },
                "previous": {"type": "object"},
                "severity_hint": {
                    "type": "string",
                    "enum": ["INFO", "WARN", "CRIT"],
                },
                "recommendations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["event_type", "correlation_id", "schema_version"],
        },
    },
    "required": ["description", "timestamp", "payload"],
}


# --------------------------------------------------------------------------
# вспомогательные функции
# --------------------------------------------------------------------------
def _num(value, digits: int = 2):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result):
        return None
    return round(result, digits)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean(value):
    """Убирает None рекурсивно: JSON Schema не принимает null в number."""
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_clean(item) for item in value if item is not None]
    return value


def now_iso() -> str:
    """Текущее время с часовым поясом, ISO 8601."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_correlation_id() -> str:
    return str(uuid4())


def _station_dict(station: dict) -> dict:
    return _clean(
        {
            "id": _int(station.get("id")),
            "type": _int(station.get("type_st")),
            "latitude": _num(station.get("latitude"), 6),
            "longitude": _num(station.get("longitude"), 6),
        }
    )


def _measurements(station: dict) -> dict:
    pm25 = _num(station.get("PM_2_5"))
    pm10 = _num(station.get("PM_10"))
    return _clean(
        {
            "pm25": pm25,
            "pm10": pm10,
            "over_tlv": bool(station.get("overTLV")),
            "pm25_limit": rules.PM25_LIMIT,
            "pm10_limit": rules.PM10_LIMIT,
            "pm25_ratio": _num(
                (pm25 / rules.PM25_LIMIT) if pm25 is not None else None, 2
            ),
            "pm10_ratio": _num(
                (pm10 / rules.PM10_LIMIT) if pm10 is not None else None, 2
            ),
        }
    )


def _network_dict(runtime_metrics: dict, runtime_state: dict | None) -> dict:
    runtime_state = runtime_state or {}
    return _clean(
        {
            "stations_total": _int(runtime_metrics.get("stations_count")),
            "active": _int(runtime_metrics.get("active_count")),
            "silent": _int(runtime_metrics.get("silent_count")),
            "moving": _int(runtime_metrics.get("moving_count")),
            "danger_clusters": _int(runtime_metrics.get("danger_cluster_count")),
            "cluster_count": _int(runtime_state.get("cluster_count")),
            "mode": str(runtime_metrics.get("mode") or ""),
            "messages_per_second": _num(runtime_metrics.get("messages_per_second")),
            "weight_per_second_kb": _num(
                runtime_metrics.get("weight_per_second_kb"), 3
            ),
            "buffer_fill_percent": _num(
                runtime_metrics.get("buffer_fill_percent"), 1
            ),
            "buffer_used_kb": _num(runtime_metrics.get("buffer_used_kb")),
            "buffer_overflow": bool(runtime_metrics.get("buffer_overflow")),
            "limit_exceeded": bool(runtime_metrics.get("limit_exceeded")),
            "dropped_weight_kb": _num(runtime_metrics.get("dropped_weight_kb")),
        }
    )


# --------------------------------------------------------------------------
# сообщения
# --------------------------------------------------------------------------
def station_event(
    event_type: str,
    station: dict,
    *,
    previous_over_tlv: bool | None = None,
    timestamp: str | None = None,
) -> dict:
    """Событие по одной станции: превышение ПДК или возврат в норму."""
    station_id = _int(station.get("id"))
    measurements = _measurements(station)
    pm25 = measurements.get("pm25")
    pm10 = measurements.get("pm10")

    if event_type == "pollution.normalized":
        description = (
            f"Станция #{station_id}: загрязнение вернулось в норму — "
            f"PM2.5 {pm25 if pm25 is not None else '—'} мкг/м³, "
            f"PM10 {pm10 if pm10 is not None else '—'} мкг/м³"
        )
    else:
        parts = []
        if pm25 is not None:
            parts.append(
                f"PM2.5 {pm25} мкг/м³ (порог {rules.PM25_LIMIT:g})"
            )
        if pm10 is not None:
            parts.append(
                f"PM10 {pm10} мкг/м³ (порог {rules.PM10_LIMIT:g})"
            )
        detail = ", ".join(parts) if parts else "данные измерений отсутствуют"
        description = f"Станция #{station_id}: превышение ПДК — {detail}"

    severity = rules.severity_hint(pm25, pm10)
    payload = {
        "event_type": event_type,
        "correlation_id": new_correlation_id(),
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_LABEL,
        "station": _station_dict(station),
        "measurements": measurements,
        "severity_hint": severity,
        "recommendations": rules.recommendations_for(pm25, pm10),
    }
    if previous_over_tlv is not None:
        payload["previous"] = {"over_tlv": bool(previous_over_tlv)}

    return {
        "description": description,
        "timestamp": timestamp or now_iso(),
        "payload": _clean(payload),
    }


def network_event(
    event_type: str,
    runtime_metrics: dict,
    runtime_state: dict | None = None,
    *,
    previous_value=None,
    timestamp: str | None = None,
) -> dict:
    """Событие о состоянии сети: лимит передачи или переполнение буфера."""
    network = _network_dict(runtime_metrics, runtime_state)

    if event_type == "network.limit_exceeded":
        mps = network.get("messages_per_second", 0)
        description = (
            "Сеть: превышен лимит передачи — "
            f"{mps:g} сообщений/с при пороге "
            f"{runtime_metrics.get('limit_threshold', '—')}"
        )
    elif event_type == "network.buffer_overflow":
        dropped = network.get("dropped_weight_kb", 0)
        description = (
            "Сеть: переполнение буфера очереди — "
            f"за тик потеряно {dropped:g} КБ"
        )
    else:
        description = f"Сеть: событие {event_type}"

    severity = rules.network_severity(event_type)
    payload = {
        "event_type": event_type,
        "correlation_id": new_correlation_id(),
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_LABEL,
        "network": network,
        "severity_hint": severity,
        "recommendations": rules.network_recommendations(event_type),
    }
    if previous_value is not None:
        payload["previous"] = {"value": bool(previous_value)}

    return {
        "description": description,
        "timestamp": timestamp or now_iso(),
        "payload": _clean(payload),
    }


def snapshot_event(
    stations: list[dict],
    runtime_metrics: dict,
    runtime_state: dict | None = None,
    *,
    timestamp: str | None = None,
) -> dict:
    """Периодический срез состояния сети."""
    active = [
        station
        for station in stations
        if _num(station.get("battery_life"), 2) is None
        or (_num(station.get("battery_life"), 2) or 0) > 0
    ]

    pm25_values = [
        value
        for value in (_num(station.get("PM_2_5")) for station in active)
        if value is not None
    ]
    pm10_values = [
        value
        for value in (_num(station.get("PM_10")) for station in active)
        if value is not None
    ]
    over_count = sum(1 for station in active if station.get("overTLV"))

    def avg(values):
        return _num(sum(values) / len(values)) if values else None

    def maximum(values):
        return _num(max(values)) if values else None

    measurements = _clean(
        {
            "pm25_avg": avg(pm25_values),
            "pm10_avg": avg(pm10_values),
            "pm25_max": maximum(pm25_values),
            "pm10_max": maximum(pm10_values),
            "over_tlv_count": over_count,
        }
    )
    network = _network_dict(runtime_metrics, runtime_state)

    description = (
        f"Срез сети: активных устройств {network.get('active', 0)}, "
        f"PM2.5 средн. {measurements.get('pm25_avg', '—')} / "
        f"макс. {measurements.get('pm25_max', '—')} мкг/м³, "
        f"превышений ПДК {over_count}"
    )

    payload = {
        "event_type": "network.snapshot",
        "correlation_id": new_correlation_id(),
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_LABEL,
        "network": network,
        "measurements": measurements,
        "severity_hint": "WARN" if over_count else "INFO",
    }
    return {
        "description": description,
        "timestamp": timestamp or now_iso(),
        "payload": _clean(payload),
    }


def test_event() -> dict:
    return {
        "description": (
            "Тестовое событие NSK_ECO Dashboard: "
            "интеграция с платформой СИГМА настроена"
        ),
        "timestamp": now_iso(),
        "payload": {
            "event_type": "sigma.test",
            "correlation_id": new_correlation_id(),
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE_LABEL,
            "severity_hint": "INFO",
        },
    }
