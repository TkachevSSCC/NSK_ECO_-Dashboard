from celery import Celery
from app.core.config import settings
from app.metrics.scheduler import TICK_SECONDS

celery = Celery(
    'station_updater',
    broker=settings.CELERY_BROKER_URL,   # можно RabbitMQ
    backend=settings.CELERY_BROKER_URL,
    include=['app.tasks.update_stations']
)

celery.conf.beat_schedule = {
    'update-stations-every-second': {
        'task': 'app.tasks.update_stations.update_stations',
        # тот же тик, что и у метрик: станции обновляются раз в секунду
        'schedule': float(TICK_SECONDS),
    },
}
celery.conf.timezone = 'UTC'
