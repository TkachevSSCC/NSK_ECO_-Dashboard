"""Пороги, уровни и рекомендации для событий СИГМА.

Уровень события (INFO/WARN/CRIT) назначается не при отправке в POST
(платформа поле level в теле запроса игнорирует), а обработчиком
обогащения. Здесь мы формируем подсказку severity_hint и рекомендации,
чтобы обработчику было проще классифицировать событие.
"""
from __future__ import annotations

from math import isfinite

# ПДК, используемые в проекте: PM2.5 = 25, PM10 = 50 мкг/м³
PM25_LIMIT = 25.0
PM10_LIMIT = 50.0

# порог временного «опасного» кластера: сумма PM2.5 + PM10
DANGER_SUM_THRESHOLD = 200.0

WHO_AIR_URL = (
    "https://www.who.int/news-room/fact-sheets/detail/"
    "ambient-(outdoor)-air-quality-and-health"
)

LEVELS = ("INFO", "WARN", "CRIT")


def _num(value) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if isfinite(result) else 0.0


def pollution_ratio(pm25, pm10) -> float:
    """Во сколько раз максимум из двух показателей превышает свой ПДК."""
    return max(_num(pm25) / PM25_LIMIT, _num(pm10) / PM10_LIMIT)


def severity_hint(pm25, pm10) -> str:
    """Подсказка уровня: INFO — норма, WARN — превышение, CRIT — более 2 ПДК."""
    ratio = pollution_ratio(pm25, pm10)
    total = _num(pm25) + _num(pm10)
    if ratio > 2.0 or total > DANGER_SUM_THRESHOLD:
        return "CRIT"
    if ratio > 1.0:
        return "WARN"
    return "INFO"


def recommendations_for(pm25, pm10) -> list[str]:
    """Что рекомендовать людям при текущем уровне загрязнения."""
    level = severity_hint(pm25, pm10)
    if level == "INFO":
        return []

    result = [
        "Сократите время пребывания на открытом воздухе.",
        "Закройте окна и при возможности включите очиститель воздуха.",
        "Водителям рекомендуется ограничить поездки в районе превышения.",
    ]
    if level == "CRIT":
        result.extend(
            [
                "Используйте респиратор FFP2/N95 при выходе на улицу.",
                "Людям из групп риска (дети, пожилые, астма, ССЗ) "
                "рекомендуется оставаться дома.",
                "Отложите физические нагрузки на улице.",
            ]
        )
    return result


def regulations_for(pm25, pm10) -> list[dict]:
    """Регламенты для PATCH/обогащения события превышения ПДК."""
    if severity_hint(pm25, pm10) == "INFO":
        return []
    return [
        {
            "name": "Превышение ПДК PM2.5/PM10",
            "link": WHO_AIR_URL,
            "recommendations": recommendations_for(pm25, pm10),
        }
    ]


def network_severity(kind: str) -> str:
    if kind == "network.limit_exceeded":
        return "WARN"
    if kind == "network.buffer_overflow":
        return "CRIT"
    return "INFO"


def network_recommendations(kind: str) -> list[str]:
    if kind == "network.limit_exceeded":
        return [
            "Проверьте режим передачи: часть устройств передаёт вне кластеров.",
            "При необходимости перестройте кластеры или ограничьте поток.",
        ]
    if kind == "network.buffer_overflow":
        return [
            "Буфер очереди переполнен: часть сообщений потеряна.",
            "Увеличьте ёмкость буфера или скорость обработки сети.",
        ]
    return []


def network_regulations(kind: str) -> list[dict]:
    recommendations = network_recommendations(kind)
    if not recommendations:
        return []
    return [
        {
            "name": "Перегрузка сети передачи данных",
            "link": None,
            "recommendations": recommendations,
        }
    ]


def regulations_for_message(msg: dict) -> list[dict]:
    """Регламенты по содержимому уже собранного сообщения."""
    payload = (msg or {}).get("payload") or {}
    measurements = payload.get("measurements") or {}
    event_type = payload.get("event_type") or ""

    if "pm25" in measurements or "pm10" in measurements:
        pm25 = measurements.get("pm25")
        pm10 = measurements.get("pm10")
        if pm25 is not None or pm10 is not None:
            return regulations_for(pm25, pm10)
    return network_regulations(event_type)
