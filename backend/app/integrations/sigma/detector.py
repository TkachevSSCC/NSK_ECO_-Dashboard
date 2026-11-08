"""Детектор событий по данным тика метрик.

Работает в том же процессе, что и metrics_loop: раз в секунду получает
список станций и runtime-метрики, сравнивает с предыдущим состоянием и
возвращает готовые сообщения. На старте состояние заполняется молча,
чтобы после перезапуска не отправить пачку событий по уже грязным станциям.
"""
from __future__ import annotations

import time

from app.integrations.sigma import schema
from app.integrations.sigma.config import SigmaConfig


class SigmaDetector:
    def __init__(self, config: SigmaConfig):
        self.config = config
        self._initialized = False
        self._over_tlv: dict[int, bool] = {}
        self._limit_exceeded = False
        self._buffer_overflow = False
        self._last_emit: dict[tuple, float] = {}
        self._last_snapshot = 0.0

    def _cooldown_ok(self, key: tuple, now: float, seconds: float) -> bool:
        if seconds <= 0:
            self._last_emit[key] = now
            return True
        last = self._last_emit.get(key, 0.0)
        if now - last < seconds:
            return False
        self._last_emit[key] = now
        return True

    def observe(
        self,
        stations: list[dict],
        runtime_metrics: dict,
        runtime_state: dict | None = None,
    ) -> list[dict]:
        if not self.config.enabled or not stations:
            return []

        now = time.monotonic()

        # ---- базовая линия: при старте ничего не отправляем ----
        if not self._initialized:
            self._over_tlv = {
                int(station.get("id")): bool(station.get("overTLV"))
                for station in stations
            }
            self._limit_exceeded = bool(
                runtime_metrics.get("limit_exceeded")
            )
            self._buffer_overflow = bool(
                runtime_metrics.get("buffer_overflow")
            )
            self._last_snapshot = now
            self._initialized = True
            return []

        events: list[dict] = []
        present_ids: set[int] = set()

        # ---- превышения по станциям ----
        for station in stations:
            try:
                station_id = int(station.get("id"))
            except (TypeError, ValueError):
                continue
            present_ids.add(station_id)
            current = bool(station.get("overTLV"))
            previous = self._over_tlv.get(station_id)

            if previous is None:
                self._over_tlv[station_id] = current
                continue

            if current and not previous:
                if self._cooldown_ok(
                    ("exceeded", station_id),
                    now,
                    self.config.station_cooldown,
                ):
                    events.append(
                        schema.station_event(
                            "pollution.exceeded",
                            station,
                            previous_over_tlv=False,
                        )
                    )
            elif previous and not current:
                if self._cooldown_ok(
                    ("normalized", station_id),
                    now,
                    self.config.station_cooldown,
                ):
                    events.append(
                        schema.station_event(
                            "pollution.normalized",
                            station,
                            previous_over_tlv=True,
                        )
                    )

            self._over_tlv[station_id] = current

        # станции, которых больше нет, забываем
        for station_id in list(self._over_tlv):
            if station_id not in present_ids:
                self._over_tlv.pop(station_id, None)
        self._last_emit = {
            key: value
            for key, value in self._last_emit.items()
            if key[0] not in ("exceeded", "normalized") or key[1] in present_ids
        }

        # ---- переходы состояния сети ----
        limit_exceeded = bool(runtime_metrics.get("limit_exceeded"))
        if (
            limit_exceeded
            and not self._limit_exceeded
            and self._cooldown_ok(
                ("limit",), now, self.config.network_cooldown
            )
        ):
            events.append(
                schema.network_event(
                    "network.limit_exceeded",
                    runtime_metrics,
                    runtime_state,
                    previous_value=False,
                )
            )
        self._limit_exceeded = limit_exceeded

        buffer_overflow = bool(runtime_metrics.get("buffer_overflow"))
        if (
            buffer_overflow
            and not self._buffer_overflow
            and self._cooldown_ok(
                ("overflow",), now, self.config.network_cooldown
            )
        ):
            events.append(
                schema.network_event(
                    "network.buffer_overflow",
                    runtime_metrics,
                    runtime_state,
                    previous_value=False,
                )
            )
        self._buffer_overflow = buffer_overflow

        # ---- периодический срез ----
        interval = self.config.snapshot_interval
        if interval > 0 and now - self._last_snapshot >= interval:
            events.append(
                schema.snapshot_event(
                    stations, runtime_metrics, runtime_state
                )
            )
            self._last_snapshot = now

        return events
