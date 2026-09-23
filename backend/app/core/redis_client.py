"""Общий Redis-клиент для приложения (оверрайды загрязнений и пр.)."""
import redis

from app.core.config import settings

_redis = None

# Redis-hash с оверрайдами загрязнений: field = str(station_id), value = json
# {"pm25": float, "pm10": float, "ticks": int}
POLLUTION_OVERRIDE_KEY = "stations:pollution_overrides"


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(
            settings.CELERY_BROKER_URL, decode_responses=True
        )
    return _redis