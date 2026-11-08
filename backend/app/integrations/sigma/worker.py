"""Фоновый воркер доставки событий в СИГМУ."""
from __future__ import annotations

import asyncio

from app.integrations.sigma.service import SigmaService


class SigmaWorker:
    def __init__(self, service: SigmaService):
        self.service = service
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="sigma-outbox-worker")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        interval = max(0.5, float(self.service.config.poll_interval))
        while True:
            try:
                await self.service.process_batch(limit=20)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # сбой одного батча не должен убивать воркер
                print(f"sigma worker error: {exc}")
            await asyncio.sleep(interval)
