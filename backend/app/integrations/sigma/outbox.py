"""Очередь исходящих событий в Redis.

Схема:
  sigma:outbox      — список сообщений к отправке (LPUSH + RPOPLPUSH)
  sigma:processing  — сообщения, взятые в работу (защита от потери)
  sigma:retry       — ZSET отложенных повторов (score = unix time)
  sigma:dead        — последние недоставленные сообщения (для диагностики)
  sigma:stats       — HASH со счётчиками и последней ошибкой
  sigma:dedup:*     — ключи дедупликации по correlation_id
  sigma:rate:*      — минутные счётчики рейт-лимита

Все операции синхронные; в асинхронном коде вызываются через
asyncio.to_thread.
"""
from __future__ import annotations

import json
import time

from app.core.redis_client import get_redis
from app.integrations.sigma.config import SigmaConfig


class SigmaOutbox:
    OUTBOX = "sigma:outbox"
    PROCESSING = "sigma:processing"
    RETRY = "sigma:retry"
    DEAD = "sigma:dead"
    STATS = "sigma:stats"
    DEDUP = "sigma:dedup:"
    RATE = "sigma:rate:"

    def __init__(self, config: SigmaConfig):
        self.config = config
        self.redis = get_redis()

    # -- счётчики ---------------------------------------------------------
    def bump(self, field: str, amount: int = 1) -> None:
        self.redis.hincrby(self.STATS, field, amount)

    def set_stat(self, field: str, value) -> None:
        self.redis.hset(self.STATS, field, "" if value is None else str(value))

    def stats(self) -> dict:
        return self.redis.hgetall(self.STATS) or {}

    # -- постановка в очередь --------------------------------------------
    def push(self, item: dict) -> bool:
        """Кладёт сообщение в очередь. False — отброшено дедупом/лимитом."""
        correlation_id = item.get("correlation_id") or ""

        if correlation_id:
            dedup_key = f"{self.DEDUP}{correlation_id}"
            if not self.redis.set(
                dedup_key, "1", nx=True, ex=self.config.outbox_ttl
            ):
                self.bump("skipped")
                return False

        minute = int(time.time() // 60)
        rate_key = f"{self.RATE}{minute}"
        current = self.redis.incr(rate_key)
        if current == 1:
            self.redis.expire(rate_key, 120)
        if current > self.config.max_events_per_minute:
            self.bump("skipped")
            return False

        # не даём очереди расти бесконечно: старые сообщения отбрасываем
        while self.redis.llen(self.OUTBOX) >= self.config.outbox_max:
            self.redis.rpop(self.OUTBOX)
            self.bump("dropped")

        self.redis.lpush(self.OUTBOX, json.dumps(item, ensure_ascii=False))
        self.bump("queued")
        return True

    # -- обработка --------------------------------------------------------
    def pop_processing(self) -> str | None:
        return self.redis.rpoplpush(self.OUTBOX, self.PROCESSING)

    def ack(self, raw: str) -> None:
        self.redis.lrem(self.PROCESSING, 1, raw)

    def retry(self, raw: str, item: dict, delay: float) -> None:
        self.ack(raw)
        self.redis.zadd(
            self.RETRY,
            {json.dumps(item, ensure_ascii=False): time.time() + max(0.0, delay)},
        )

    def dead(self, raw: str, item: dict, error: str) -> None:
        self.ack(raw)
        payload = {
            "item": item,
            "error": (error or "")[:1000],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        self.redis.lpush(self.DEAD, json.dumps(payload, ensure_ascii=False))
        self.redis.ltrim(self.DEAD, 0, 199)
        self.bump("dead")

    def promote_due(self, limit: int = 50) -> int:
        """Возвращает в outbox отложенные сообщения, срок которых наступил."""
        due = self.redis.zrangebyscore(
            self.RETRY, "-inf", time.time(), start=0, num=limit
        )
        promoted = 0
        for raw in due:
            if self.redis.zrem(self.RETRY, raw):
                self.redis.lpush(self.OUTBOX, raw)
                promoted += 1
        return promoted

    def recover(self) -> int:
        """После падения процесса возвращает processing обратно в очередь."""
        recovered = 0
        for _ in range(10000):
            raw = self.redis.rpoplpush(self.PROCESSING, self.OUTBOX)
            if raw is None:
                break
            try:
                item = json.loads(raw)
                item["attempts"] = int(item.get("attempts", 0)) + 1
                item["recovered"] = True
                self.redis.lrem(self.OUTBOX, 1, raw)
                self.redis.lpush(
                    self.OUTBOX, json.dumps(item, ensure_ascii=False)
                )
            except (ValueError, TypeError):
                pass
            recovered += 1
        return recovered

    # -- диагностика ------------------------------------------------------
    def sizes(self) -> dict:
        return {
            "outbox": int(self.redis.llen(self.OUTBOX)),
            "processing": int(self.redis.llen(self.PROCESSING)),
            "retry": int(self.redis.zcard(self.RETRY)),
            "dead": int(self.redis.llen(self.DEAD)),
        }

    def dead_items(self, limit: int = 20) -> list[dict]:
        result = []
        for raw in self.redis.lrange(self.DEAD, 0, max(0, limit - 1)):
            try:
                result.append(json.loads(raw))
            except (ValueError, TypeError):
                result.append({"error": "не удалось разобрать запись"})
        return result
