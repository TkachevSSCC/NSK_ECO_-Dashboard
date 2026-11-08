"""Настройки интеграции с платформой СИГМА.

Читаются из переменных окружения (и из backend/.env, если файл есть).
Секреты никогда не попадают в ответы API и логи.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# .env основной части приложения уже загружен к моменту импорта этого модуля;
# load_dotenv() без override не перетирает значения, заданные снаружи.
load_dotenv()

DEFAULT_ENDPOINT = "https://fwsigma.nsu.ru/api-gateway"
DEFAULT_KEYCLOAK = "https://fwsigma.nsu.ru/keycloak"
DEFAULT_REALM = "sigma"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class SigmaConfig:
    endpoint_url: str
    keycloak_url: str
    realm: str
    app_id: str
    secret_key: str
    source_id: str
    source_name: str
    source_domain: str
    enabled: bool
    test_enabled: bool
    demo_enrich: bool
    snapshot_interval: float
    poll_interval: float
    station_cooldown: float
    network_cooldown: float
    max_events_per_minute: int
    max_attempts: int
    outbox_max: int
    outbox_ttl: int
    connect_timeout: float
    read_timeout: float

    @classmethod
    def from_env(cls) -> "SigmaConfig":
        app_id = _env("SIGMA_APP_ID")
        secret_key = _env("SIGMA_SECRET_KEY")
        source_id = _env("SIGMA_SOURCE_ID")
        credentials_ready = bool(app_id and secret_key and source_id)

        return cls(
            endpoint_url=_env("SIGMA_ENDPOINT_URL", DEFAULT_ENDPOINT).rstrip("/"),
            keycloak_url=_env(
                "SIGMA_KEYCLOAK_URL",
                _env("KEYCLOAK_URL", DEFAULT_KEYCLOAK),
            ).rstrip("/"),
            realm=_env("SIGMA_REALM", _env("KEYCLOAK_REALM", DEFAULT_REALM)),
            app_id=app_id,
            secret_key=secret_key,
            source_id=source_id,
            source_name=_env("SIGMA_SOURCE_NAME", "nsk-eco-stations"),
            source_domain=_env("SIGMA_SOURCE_DOMAIN", "nsk-eco"),
            enabled=_bool("SIGMA_ENABLED", credentials_ready)
            and credentials_ready,
            test_enabled=_bool("SIGMA_TEST_ENABLED", True),
            demo_enrich=_bool("SIGMA_DEMO_ENRICH", False),
            snapshot_interval=_float("SIGMA_SNAPSHOT_INTERVAL", 300.0),
            poll_interval=_float("SIGMA_POLL_INTERVAL", 2.0),
            station_cooldown=_float("SIGMA_STATION_COOLDOWN", 600.0),
            network_cooldown=_float("SIGMA_NETWORK_COOLDOWN", 300.0),
            max_events_per_minute=_int("SIGMA_MAX_EVENTS_PER_MINUTE", 30),
            max_attempts=_int("SIGMA_MAX_ATTEMPTS", 8),
            outbox_max=_int("SIGMA_OUTBOX_MAX", 5000),
            outbox_ttl=_int("SIGMA_OUTBOX_TTL", 86400),
            connect_timeout=_float("SIGMA_CONNECT_TIMEOUT", 3.0),
            read_timeout=_float("SIGMA_READ_TIMEOUT", 10.0),
        )

    @property
    def token_url(self) -> str:
        return (
            f"{self.keycloak_url}/realms/{self.realm}"
            "/protocol/openid-connect/token"
        )

    @property
    def client_id(self) -> str:
        return f"app-{self.app_id}" if self.app_id else ""

    @property
    def missing(self) -> list[str]:
        missing = []
        if not self.app_id:
            missing.append("SIGMA_APP_ID")
        if not self.secret_key:
            missing.append("SIGMA_SECRET_KEY")
        if not self.source_id:
            missing.append("SIGMA_SOURCE_ID")
        return missing

    @property
    def credentials_ready(self) -> bool:
        return not self.missing

    @property
    def masked_source_id(self) -> str:
        if not self.source_id:
            return ""
        if len(self.source_id) <= 12:
            return self.source_id
        return f"{self.source_id[:8]}…{self.source_id[-4:]}"


def mask_secret(value: str) -> str:
    """Короткое представление секрета для диагностики (не раскрывает его)."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:2]}{'•' * 8}{value[-2:]}"
