"""Регистрация приложения и источника СИГМА.

Скрипт нужен только на этапе настройки: он вызывает Gateway с
пользовательским токеном, создаёт приложение и источник, после чего
сохраняет SIGMA_* в backend/.sigma.env (файл не попадает в git).

Примеры:
    python tools/sigma_setup.py register ^
        --user-token "<access_token>" ^
        --user-id "<uuid пользователя>" ^
        --app-url "https://eco.curxtor.ru"

    python tools/sigma_setup.py check --test-event

Пользовательский токен берётся из Keycloak (вход человека), а не из
приложения. Приложение потом ходит в Gateway своим токеном
client_credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

from app.integrations.sigma import schema  # noqa: E402
from app.integrations.sigma.client import SigmaClient, SigmaError  # noqa: E402
from app.integrations.sigma.config import (  # noqa: E402
    DEFAULT_ENDPOINT,
    DEFAULT_KEYCLOAK,
    DEFAULT_REALM,
    SigmaConfig,
)

DEFAULT_ENV_FILE = BACKEND / ".sigma.env"


def _base_config(args) -> SigmaConfig:
    base = SigmaConfig.from_env()
    return replace(
        base,
        endpoint_url=args.endpoint.rstrip("/"),
        keycloak_url=args.keycloak.rstrip("/"),
        realm=args.realm,
        enabled=True,
    )


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Сгенерировано tools/sigma_setup.py; файл не коммитится.",
        *[f"{key}={value}" for key, value in values.items()],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cmd_register(args) -> int:
    user_token = args.user_token or os.environ.get("SIGMA_USER_TOKEN", "")
    if not user_token:
        print("Ошибка: нужен --user-token (пользовательский токен Keycloak)")
        return 2
    if not args.user_id:
        print("Ошибка: нужен --user-id (UUID пользователя)")
        return 2

    base = _base_config(args)

    if args.app_id and args.secret:
        app_id, secret = args.app_id, args.secret
        print(f"Использую существующее приложение: {app_id}")
    else:
        if not args.app_url:
            print("Ошибка: нужен --app-url (публичный URL дашборда)")
            return 2
        secret = args.secret or secrets.token_urlsafe(48)
        client = SigmaClient(base)
        card = client.register_application(
            user_token=user_token,
            user_id=args.user_id,
            name=args.app_name,
            description=args.app_description,
            url=args.app_url,
            secret_key=secret,
        )
        app_id = str(card.get("app_id") or "")
        if not app_id:
            print(f"Ошибка: в ответе нет app_id: {card}")
            return 1
        print(f"Приложение создано: app_id={app_id}")

    if args.source_id:
        source_id = args.source_id
        print(f"Использую существующий источник: {source_id}")
    else:
        source_config = replace(
            base, app_id=app_id, secret_key=secret, source_id=""
        )
        client = SigmaClient(source_config)
        source = client.register_source(
            user_token=user_token,
            name=args.source_name,
            description=args.source_description,
            domain=args.source_domain,
            ip=args.ip,
            active=not args.inactive,
            data_schema=schema.SOURCE_SCHEMA,
        )
        source_id = str(
            source.get("source_id") or source.get("id") or ""
        )
        if not source_id:
            print(f"Ошибка: в карточке источника нет source_id: {source}")
            return 1
        print(f"Источник создан: source_id={source_id}")
        if not source.get("active", True):
            print("Внимание: источник создан неактивным (active=false).")

    values = {
        "SIGMA_ENABLED": "true",
        "SIGMA_ENDPOINT_URL": base.endpoint_url,
        "SIGMA_KEYCLOAK_URL": base.keycloak_url,
        "SIGMA_REALM": base.realm,
        "SIGMA_APP_ID": app_id,
        "SIGMA_SECRET_KEY": secret,
        "SIGMA_SOURCE_ID": source_id,
        "SIGMA_SOURCE_NAME": args.source_name,
        "SIGMA_SOURCE_DOMAIN": args.source_domain,
    }

    if args.no_write_env:
        print("\nSIGMA_* (сохраните вручную):")
        for key, value in values.items():
            print(f"{key}={value}")
    else:
        _write_env(Path(args.env_file), values)
        print(f"\nНастройки записаны в {args.env_file}")
        print("Секрет приложения хранится только в этом файле.")
        print(
            "Скопируйте строки в backend/.env или передайте файл "
            "через окружение перед запуском backend."
        )

    print("\nПроверка: python tools/sigma_setup.py check --test-event")
    return 0


def cmd_check(args) -> int:
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    config = SigmaConfig.from_env()
    if not config.credentials_ready:
        print(
            "Интеграция не настроена, отсутствуют: "
            + ", ".join(config.missing)
        )
        print(
            "Запустите register или добавьте SIGMA_* в backend/.env "
            "(см. backend/.example.env)."
        )
        return 2

    client = SigmaClient(config)
    try:
        source = client.get_source()
        print("Источник:")
        print(json.dumps(source, ensure_ascii=False, indent=2))
        if not source.get("active", False):
            print("Внимание: источник неактивен, события не будут приниматься.")

        if args.test_event:
            msg = schema.test_event()
            client.send_event(msg)
            correlation_id = msg["payload"]["correlation_id"]
            print(f"\nТестовое событие отправлено: correlation_id={correlation_id}")
            try:
                event_id = client.find_event_by_correlation(correlation_id)
            except SigmaError:
                event_id = None
            if event_id:
                print(f"event_id={event_id}")

        if args.list_events:
            events = client.list_events(processed=False, order="desc", limit=5)
            print("\nПоследние необработанные события:")
            for event in events:
                msg = event.get("msg") or {}
                payload = msg.get("payload") or {}
                print(
                    f"  #{event.get('id')} {msg.get('timestamp')} "
                    f"{payload.get('event_type')}: {msg.get('description')}"
                )
    except SigmaError as exc:
        print(f"Ошибка СИГМА: {exc}")
        return 1
    finally:
        client.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Настройка интеграции NSK_ECO Dashboard с СИГМА"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(sub):
        sub.add_argument(
            "--endpoint",
            default=os.environ.get("SIGMA_ENDPOINT_URL", DEFAULT_ENDPOINT),
        )
        sub.add_argument(
            "--keycloak",
            default=os.environ.get("SIGMA_KEYCLOAK_URL", DEFAULT_KEYCLOAK),
        )
        sub.add_argument(
            "--realm",
            default=os.environ.get("SIGMA_REALM", DEFAULT_REALM),
        )

    register = subparsers.add_parser(
        "register", help="создать приложение и источник"
    )
    common(register)
    register.add_argument("--user-token", default="")
    register.add_argument("--user-id", default="")
    register.add_argument("--app-id", default="")
    register.add_argument("--secret", default="")
    register.add_argument(
        "--app-name", default="NSK_ECO Dashboard"
    )
    register.add_argument(
        "--app-description",
        default="Дашборд мониторинга качества воздуха NSK_ECO",
    )
    register.add_argument(
        "--app-url", default="https://eco.curxtor.ru"
    )
    register.add_argument("--source-id", default="")
    register.add_argument("--source-name", default="nsk-eco-stations")
    register.add_argument(
        "--source-description",
        default=(
            "Станции мониторинга воздуха NSK_ECO: превышения ПДК, "
            "возврат в норму, состояние сети передачи данных"
        ),
    )
    register.add_argument("--source-domain", default="nsk-eco")
    register.add_argument("--ip", default="0.0.0.0/0")
    register.add_argument("--inactive", action="store_true")
    register.add_argument(
        "--env-file", default=str(DEFAULT_ENV_FILE)
    )
    register.add_argument("--no-write-env", action="store_true")
    register.set_defaults(func=cmd_register)

    check = subparsers.add_parser(
        "check", help="проверить источник и отправить тестовое событие"
    )
    common(check)
    check.add_argument(
        "--env-file", default=str(DEFAULT_ENV_FILE)
    )
    check.add_argument("--test-event", action="store_true")
    check.add_argument("--list-events", action="store_true")
    check.set_defaults(func=cmd_check)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
