"""HTTP-клиент платформы СИГМА.

Реализует ровно тот протокол, который описан в руководствах:
  * токен приложения через Keycloak (grant_type=client_credentials,
    client_id = app-<app_id>, тело form-urlencoded);
  * вызовы Gateway с заголовком Authorization: Bearer ...;
  * регистрация приложения и источника (пользовательский токен);
  * отправка, чтение, подсчёт и PATCH событий.

Сознательно не используем SDK sigma: wheel-пакета в проекте нет, а
зависимости (requests) уже установлены. Сетевые вызовы синхронные;
в асинхронном коде их нужно вызывать через asyncio.to_thread.
"""
from __future__ import annotations

import json
import threading
import time
from uuid import uuid4

import requests

from app.integrations.sigma.config import SigmaConfig


# --------------------------------------------------------------------------
# ошибки
# --------------------------------------------------------------------------
class SigmaError(Exception):
    """Базовая ошибка интеграции."""


class SigmaNetworkError(SigmaError):
    """Сеть недоступна, таймаут, DNS, TLS."""


class SigmaAuthError(SigmaError):
    """Keycloak не выдал токен."""


class SigmaApiError(SigmaError):
    """Gateway вернул ошибочный HTTP-статус."""

    def __init__(self, status: int, body: str, url: str = ""):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} {url}: {body[:300]}")


def _bearer(token: str) -> str:
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


# --------------------------------------------------------------------------
# токен приложения
# --------------------------------------------------------------------------
class TokenProvider:
    """client_credentials с кэшем до истечения срока, потокобезопасно."""

    def __init__(self, config: SigmaConfig):
        self.config = config
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self.requests_count = 0

    def get(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._expires_at - 30:
                return self._token
            return self._fetch_locked()

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def _fetch_locked(self) -> str:
        if not self.config.app_id or not self.config.secret_key:
            raise SigmaAuthError("не заданы SIGMA_APP_ID и SIGMA_SECRET_KEY")

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.secret_key,
        }
        try:
            response = requests.post(
                self.config.token_url,
                data=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=(self.config.connect_timeout, self.config.read_timeout),
            )
        except requests.RequestException as exc:
            raise SigmaNetworkError(f"Keycloak недоступен: {exc}") from exc

        self.requests_count += 1
        if response.status_code != 200:
            raise SigmaAuthError(
                f"Keycloak вернул HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SigmaAuthError(
                f"не JSON в ответе Keycloak: {response.text[:200]}"
            ) from exc

        token = data.get("access_token")
        if not token:
            raise SigmaAuthError(
                f"нет access_token в ответе Keycloak: {response.text[:200]}"
            )
        self._token = token
        try:
            expires_in = int(data.get("expires_in", 300))
        except (TypeError, ValueError):
            expires_in = 300
        self._expires_at = time.time() + max(30, expires_in)
        return token


# --------------------------------------------------------------------------
# клиент Gateway
# --------------------------------------------------------------------------
class SigmaClient:
    def __init__(self, config: SigmaConfig, tokens: TokenProvider | None = None):
        self.config = config
        self.tokens = tokens or TokenProvider(config)
        self._session = requests.Session()
        self._lock = threading.Lock()

    # -- низкий уровень ---------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.config.endpoint_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        user_token: str | None = None,
        auth: bool = True,
        retry_auth: bool = True,
    ) -> requests.Response:
        url = self._url(path)
        headers = {
            "Accept": "application/json",
            "X-Request-ID": str(uuid4()),
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"

        if user_token:
            headers["Authorization"] = f"Bearer {_bearer(user_token)}"
        elif auth:
            headers["Authorization"] = f"Bearer {self.tokens.get()}"

        try:
            with self._lock:
                response = self._session.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=headers,
                    timeout=(
                        self.config.connect_timeout,
                        self.config.read_timeout,
                    ),
                )
        except requests.RequestException as exc:
            raise SigmaNetworkError(f"{method} {url}: {exc}") from exc

        # 401 означает, что токен не принят: обновляем и повторяем один раз.
        # Для POST это безопасно: при 401 запрос не обработан.
        if (
            response.status_code == 401
            and auth
            and not user_token
            and retry_auth
        ):
            self.tokens.invalidate()
            headers["Authorization"] = f"Bearer {self.tokens.get(force=True)}"
            try:
                with self._lock:
                    response = self._session.request(
                        method,
                        url,
                        json=json_body,
                        params=params,
                        headers=headers,
                        timeout=(
                            self.config.connect_timeout,
                            self.config.read_timeout,
                        ),
                    )
            except requests.RequestException as exc:
                raise SigmaNetworkError(f"{method} {url}: {exc}") from exc

        return response

    @staticmethod
    def _decode(response: requests.Response):
        if not response.text:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # -- события ----------------------------------------------------------
    def send_event(self, msg: dict) -> str:
        """POST events/sources/<source_id>/events/. Успех — 201 и "ok"."""
        try:
            json.dumps(msg, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise SigmaError(f"сообщение не сериализуется в JSON: {exc}") from exc

        response = self._request(
            "POST",
            f"events/sources/{self.config.source_id}/events/",
            json_body={"msg": msg},
        )
        if response.status_code not in (200, 201):
            raise SigmaApiError(
                response.status_code, response.text, "POST events"
            )
        if (response.text or "").strip().strip('"') != "ok":
            raise SigmaApiError(
                response.status_code,
                f"неожиданный ответ: {response.text[:200]}",
                "POST events",
            )
        return "ok"

    def list_events(
        self,
        *,
        processed: bool | None = None,
        level: str | None = None,
        order: str = "desc",
        limit: int = 100,
        skip: int = 0,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        params = {
            "order": order,
            "limit": limit,
            "skip": skip,
        }
        if processed is not None:
            params["processed"] = "true" if processed else "false"
        if level:
            params["level"] = level
        if date:
            params["date"] = date
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to

        response = self._request(
            "GET",
            f"events/sources/{self.config.source_id}/events/",
            params=params,
        )
        if response.status_code != 200:
            raise SigmaApiError(response.status_code, response.text, "GET events")
        data = self._decode(response)
        return data if isinstance(data, list) else []

    def count_events(self, **params) -> int:
        response = self._request(
            "GET",
            f"events/sources/{self.config.source_id}/events/count",
            params=params,
        )
        if response.status_code != 200:
            raise SigmaApiError(
                response.status_code, response.text, "GET events/count"
            )
        data = self._decode(response)
        if isinstance(data, dict):
            return int(data.get("count", 0))
        try:
            return int(data)
        except (TypeError, ValueError):
            return 0

    def patch_event(
        self,
        event_id: int,
        *,
        level: str | None = None,
        regulations: list | None = None,
    ) -> None:
        body: dict = {}
        if level is not None:
            if level not in ("INFO", "WARN", "CRIT"):
                raise ValueError("level должен быть INFO, WARN или CRIT")
            body["level"] = level
        if regulations is not None:
            body["regulations"] = regulations

        response = self._request(
            "PATCH",
            f"events/sources/{self.config.source_id}/events/{event_id}",
            json_body=body,
        )
        if response.status_code not in (200, 204):
            raise SigmaApiError(
                response.status_code,
                response.text,
                f"PATCH event {event_id}",
            )

    def find_event_by_correlation(
        self, correlation_id: str, *, limit: int = 100
    ) -> int | None:
        """Ищет event_id по correlation_id в payload (защита от дублей)."""
        if not correlation_id:
            return None
        for processed in (False, True):
            try:
                events = self.list_events(
                    processed=processed, order="desc", limit=limit, skip=0
                )
            except SigmaError:
                continue
            for event in events:
                payload = (event.get("msg") or {}).get("payload") or {}
                if payload.get("correlation_id") == correlation_id:
                    try:
                        return int(event.get("id"))
                    except (TypeError, ValueError):
                        return None
        return None

    # -- источники (нужен пользовательский токен) --------------------------
    def register_application(
        self,
        *,
        user_token: str,
        user_id: str,
        name: str,
        description: str,
        url: str,
        secret_key: str,
    ) -> dict:
        response = self._request(
            "POST",
            f"auth/users/{user_id}/apps/",
            json_body={
                "name": name,
                "description": description,
                "url": url,
                "secret_key": secret_key,
            },
            user_token=user_token,
        )
        if response.status_code not in (200, 201):
            raise SigmaApiError(
                response.status_code, response.text, "POST auth apps"
            )
        data = self._decode(response)
        if not isinstance(data, dict):
            raise SigmaApiError(
                response.status_code,
                f"неожиданный ответ: {response.text[:200]}",
                "POST auth apps",
            )
        return data

    def register_source(
        self,
        *,
        user_token: str,
        name: str,
        description: str,
        domain: str,
        ip: str = "0.0.0.0/0",
        active: bool = True,
        data_schema: dict,
    ) -> dict:
        response = self._request(
            "POST",
            "events/sources/",
            json_body={
                "name": name,
                "description": description,
                "domain": domain,
                "ip": ip,
                "active": active,
                "data_schema": data_schema,
            },
            user_token=user_token,
        )
        if response.status_code not in (200, 201):
            raise SigmaApiError(
                response.status_code, response.text, "POST events/sources"
            )

        # Успешный ответ — URL строкой; UUID источника берём из списка.
        for item in self.list_sources(user_token=user_token):
            if item.get("name") == name:
                return item
        raise SigmaApiError(
            response.status_code,
            "источник создан, но его UUID не найден в списке",
            "POST events/sources",
        )

    def list_sources(self, *, user_token: str | None = None) -> list[dict]:
        response = self._request(
            "GET", "events/sources/", user_token=user_token
        )
        if response.status_code != 200:
            raise SigmaApiError(
                response.status_code, response.text, "GET events/sources"
            )
        data = self._decode(response)
        return data if isinstance(data, list) else []

    def get_source(
        self, source_id: str | None = None, *, user_token: str | None = None
    ) -> dict:
        sid = source_id or self.config.source_id
        response = self._request(
            "GET", f"events/sources/{sid}", user_token=user_token
        )
        if response.status_code != 200:
            raise SigmaApiError(
                response.status_code,
                response.text,
                f"GET events/sources/{sid}",
            )
        data = self._decode(response)
        return data if isinstance(data, dict) else {}

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass
