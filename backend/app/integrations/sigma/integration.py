"""Синглтон интеграции: связывает детектор, очередь, воркер и статус."""
from __future__ import annotations

from app.integrations.sigma.config import SigmaConfig
from app.integrations.sigma.detector import SigmaDetector
from app.integrations.sigma.service import SigmaService
from app.integrations.sigma.worker import SigmaWorker


class SigmaIntegration:
    def __init__(self):
        self.config = SigmaConfig.from_env()
        self.detector = SigmaDetector(self.config)
        self.service = SigmaService(self.config) if self.config.enabled else None
        self.worker = SigmaWorker(self.service) if self.service else None
        self._started = False
        self._start_error = ""

    async def start(self) -> None:
        if not self.config.enabled or self.service is None:
            return
        if self._started:
            return
        try:
            recovered = await self.service.recover()
        except Exception as exc:  # noqa: BLE001
            self._start_error = f"Redis недоступен: {exc}"
            print(f"sigma: {self._start_error}")
            return

        await self.worker.start()
        self._started = True
        self._start_error = ""
        suffix = f", восстановлено {recovered}" if recovered else ""
        print(
            "sigma: интеграция включена, источник "
            f"{self.config.masked_source_id}{suffix}"
        )

    async def stop(self) -> None:
        if self.worker is not None:
            await self.worker.stop()
        self._started = False
        if self.service is not None:
            self.service.close()

    async def observe(
        self,
        stations: list[dict],
        runtime_metrics: dict,
        runtime_state: dict | None = None,
    ) -> None:
        if not self._started or self.service is None:
            return
        try:
            events = self.detector.observe(
                stations, runtime_metrics, runtime_state
            )
        except Exception as exc:  # noqa: BLE001
            print(f"sigma detector error: {exc}")
            return
        if not events:
            return
        try:
            await self.service.enqueue_many(events)
        except Exception as exc:  # noqa: BLE001
            print(f"sigma enqueue error: {exc}")

    async def status(self) -> dict:
        result = {
            "configured": self.config.credentials_ready,
            "enabled": self.config.enabled,
            "credentials_ready": self.config.credentials_ready,
            "missing": self.config.missing,
            "endpoint_url": self.config.endpoint_url,
            "source_id": self.config.masked_source_id,
            "source_name": self.config.source_name,
            "snapshot_interval_seconds": self.config.snapshot_interval,
            "test_enabled": self.config.test_enabled,
            "demo_enrich": self.config.demo_enrich,
            "worker_running": bool(self.worker and self.worker.running),
            "start_error": self._start_error,
            "queue_size": 0,
            "queues": {"outbox": 0, "processing": 0, "retry": 0, "dead": 0},
            "counters": {
                "sent": 0,
                "failed": 0,
                "dead": 0,
                "dropped": 0,
                "skipped": 0,
                "queued": 0,
            },
            "last_sent_at": None,
            "last_error": None,
            "last_error_at": None,
            "last_status": None,
            "last_correlation_id": None,
            "last_event_id": None,
            "last_enrich_error": None,
        }
        if self.service is not None:
            result.update(await self.service.status())
        return result


sigma_integration = SigmaIntegration()
