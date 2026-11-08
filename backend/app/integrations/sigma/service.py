"""Сервис интеграции: постановка событий в очередь и их доставка.

Все сетевые и Redis-вызовы синхронные; публичные методы оборачивают их в
asyncio.to_thread, чтобы не блокировать event loop FastAPI.
"""
from __future__ import annotations

import asyncio
import json

from app.integrations.sigma import rules, schema
from app.integrations.sigma.client import (
    SigmaApiError,
    SigmaAuthError,
    SigmaClient,
    SigmaError,
    SigmaNetworkError,
)
from app.integrations.sigma.config import SigmaConfig
from app.integrations.sigma.outbox import SigmaOutbox

COUNTER_FIELDS = ("sent", "failed", "dead", "dropped", "skipped", "queued")


def _item_from_message(msg: dict) -> dict:
    payload = msg.get("payload") or {}
    return {
        "msg": msg,
        "correlation_id": payload.get("correlation_id")
        or schema.new_correlation_id(),
        "event_type": payload.get("event_type"),
        "severity": payload.get("severity_hint"),
        "regulations": rules.regulations_for_message(msg),
        "attempts": 0,
        "created_at": schema.now_iso(),
    }


class SigmaService:
    def __init__(self, config: SigmaConfig):
        self.config = config
        self.client = SigmaClient(config)
        self.outbox = SigmaOutbox(config)

    # -- постановка в очередь ---------------------------------------------
    async def enqueue_many(self, messages: list[dict]) -> int:
        if not messages:
            return 0
        return await asyncio.to_thread(self._enqueue_many_sync, messages)

    def _enqueue_many_sync(self, messages: list[dict]) -> int:
        accepted = 0
        for msg in messages:
            if self.outbox.push(_item_from_message(msg)):
                accepted += 1
        return accepted

    # -- восстановление после падения -------------------------------------
    async def recover(self) -> int:
        return await asyncio.to_thread(self.outbox.recover)

    # -- обработка очереди ------------------------------------------------
    async def process_batch(self, limit: int = 20) -> int:
        return await asyncio.to_thread(self._process_batch_sync, limit)

    def _process_batch_sync(self, limit: int) -> int:
        self.outbox.promote_due()
        processed = 0
        for _ in range(max(1, limit)):
            raw = self.outbox.pop_processing()
            if raw is None:
                break
            self._handle_raw(raw)
            processed += 1
        return processed

    def _handle_raw(self, raw: str) -> None:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError):
            self.outbox.dead(raw, {}, "не удалось разобрать JSON из очереди")
            self._record_failure("не удалось разобрать JSON из очереди")
            return

        msg = item.get("msg") or {}
        correlation_id = item.get("correlation_id") or ""
        attempts = int(item.get("attempts") or 0)

        # Повтор или восстановление после падения: возможно, событие уже
        # принято платформой, но ответ потерялся. Сверяемся по correlation_id.
        if attempts > 0 or item.get("recovered"):
            existing = self._find_existing(correlation_id)
            if existing is not None:
                self._ack(raw, item, existing)
                return

        try:
            self.client.send_event(msg)
        except SigmaApiError as exc:
            if exc.status >= 500 or exc.status == 429:
                self._retry(raw, item, attempts, str(exc))
            else:
                self._dead(raw, item, str(exc))
        except (SigmaNetworkError, SigmaAuthError) as exc:
            existing = self._find_existing(correlation_id)
            if existing is not None:
                self._ack(raw, item, existing)
            else:
                self._retry(raw, item, attempts, str(exc))
        except SigmaError as exc:
            self._retry(raw, item, attempts, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._retry(raw, item, attempts, f"{type(exc).__name__}: {exc}")
        else:
            event_id = None
            if self.config.demo_enrich:
                event_id = self._enrich(item)
            self._ack(raw, item, event_id)

    # -- результат попытки -------------------------------------------------
    def _ack(self, raw: str, item: dict, event_id: int | None) -> None:
        self.outbox.ack(raw)
        self.outbox.bump("sent")
        self.outbox.set_stat("last_sent_at", schema.now_iso())
        self.outbox.set_stat(
            "last_correlation_id", item.get("correlation_id") or ""
        )
        self.outbox.set_stat("last_event_id", event_id or "")
        self.outbox.set_stat("last_status", "ok")
        # успешная доставка снимает прошлую ошибку
        self.outbox.set_stat("last_error", "")
        self.outbox.set_stat("last_error_at", "")

    def _retry(self, raw: str, item: dict, attempts: int, error: str) -> None:
        if attempts + 1 >= self.config.max_attempts:
            self._dead(raw, item, error)
            return

        self.outbox.bump("failed")
        self._record_failure(error)

        delay = min(300.0, 5.0 * (2 ** attempts))
        item["attempts"] = attempts + 1
        item["last_error"] = error[:1000]
        item["last_attempt_at"] = schema.now_iso()
        self.outbox.retry(raw, item, delay)

    def _dead(self, raw: str, item: dict, error: str) -> None:
        self.outbox.bump("failed")
        self.outbox.dead(raw, item, error)
        self._record_failure(error)

    def _record_failure(self, error: str) -> None:
        self.outbox.set_stat("last_error", (error or "")[:1000])
        self.outbox.set_stat("last_error_at", schema.now_iso())
        self.outbox.set_stat("last_status", "error")

    # -- сверка и обогащение ----------------------------------------------
    def _find_existing(self, correlation_id: str) -> int | None:
        if not correlation_id:
            return None
        try:
            return self.client.find_event_by_correlation(correlation_id)
        except SigmaError:
            return None

    def _enrich(self, item: dict) -> int | None:
        """Демо-обогащение: PATCH level/regulations после успешной отправки.

        В боевом контуре уровень назначает Kafka-обработчик, а не дашборд.
        """
        if item.get("event_type") == "sigma.test":
            return None
        event_id = self._find_existing(item.get("correlation_id") or "")
        if event_id is None:
            return None
        level = item.get("severity")
        if level not in rules.LEVELS:
            level = None
        try:
            self.client.patch_event(
                event_id,
                level=level,
                regulations=item.get("regulations") or None,
            )
        except SigmaError as exc:
            self.outbox.set_stat("last_enrich_error", str(exc)[:500])
            return event_id
        return event_id

    # -- ручная проверка ---------------------------------------------------
    async def send_test(self) -> dict:
        return await asyncio.to_thread(self._send_test_sync)

    def _send_test_sync(self) -> dict:
        msg = schema.test_event()
        correlation_id = msg["payload"]["correlation_id"]
        try:
            self.client.send_event(msg)
        except Exception as exc:
            self.outbox.bump("failed")
            self._record_failure(str(exc))
            raise
        event_id = self._find_existing(correlation_id)
        self.outbox.bump("sent")
        self.outbox.set_stat("last_sent_at", schema.now_iso())
        self.outbox.set_stat("last_correlation_id", correlation_id)
        self.outbox.set_stat("last_event_id", event_id or "")
        self.outbox.set_stat("last_status", "ok")
        self.outbox.set_stat("last_error", "")
        self.outbox.set_stat("last_error_at", "")
        return {
            "correlation_id": correlation_id,
            "event_id": event_id,
            "http_status": 201,
        }

    # -- чтение и диагностика ---------------------------------------------
    async def list_events(
        self,
        *,
        processed: bool | None = None,
        level: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self.client.list_events,
            processed=processed,
            level=level,
            order="desc",
            limit=limit,
            skip=0,
        )

    async def dead_items(self, limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(self.outbox.dead_items, limit)

    async def status(self) -> dict:
        try:
            sizes, stats = await asyncio.gather(
                asyncio.to_thread(self.outbox.sizes),
                asyncio.to_thread(self.outbox.stats),
            )
            redis_error = ""
        except Exception as exc:  # noqa: BLE001
            sizes = {"outbox": 0, "processing": 0, "retry": 0, "dead": 0}
            stats = {}
            redis_error = f"Redis недоступен: {exc}"

        counters = {}
        for field in COUNTER_FIELDS:
            try:
                counters[field] = int(stats.get(field, 0) or 0)
            except (TypeError, ValueError):
                counters[field] = 0

        result = {
            "queue_size": (
                sizes["outbox"] + sizes["processing"] + sizes["retry"]
            ),
            "queues": sizes,
            "counters": counters,
            "last_sent_at": stats.get("last_sent_at") or None,
            "last_error": stats.get("last_error") or None,
            "last_error_at": stats.get("last_error_at") or None,
            "last_status": stats.get("last_status") or None,
            "last_correlation_id": stats.get("last_correlation_id") or None,
            "last_event_id": stats.get("last_event_id") or None,
            "last_enrich_error": stats.get("last_enrich_error") or None,
        }
        if redis_error:
            result["redis_error"] = redis_error
        return result

    def close(self) -> None:
        self.client.close()
