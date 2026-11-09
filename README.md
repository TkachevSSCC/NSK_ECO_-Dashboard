# AirMonitoring / nsk_eco

Веб-приложение для мониторинга качества воздуха в Новосибирске. Проект показывает станции на карте, моделирует передачу данных по глобальной сети и локальным кластерам, рассчитывает нагрузку, расход батарей и зоны повышенного загрязнения.

## Состав проекта

```text
backend/
├── app/                    # FastAPI-приложение
│   ├── api/v1/             # REST API и WebSocket
│   ├── db/                 # SQLAlchemy-модели и миграции Alembic
│   ├── metrics/            # расчёт метрик и планировщик
│   ├── services/           # бизнес-логика станций и кластеров
│   └── data/               # маски воды, дорог и водоёмов
├── .example.env            # шаблон переменных окружения
└── app/requirements.txt    # зависимости Python
frontend/
├── src/                    # React-приложение
├── package.json            # команды и зависимости npm
└── vite.config.js          # dev-сервер и proxy к API
data/                       # исходные данные WRF и станции
start.ps1                   # запуск компонентов на Windows
```

## Требования

- Python 3.10+;
- Node.js и npm;
- PostgreSQL;
- Redis;
- Git.

Для полноценной работы фоновых задач и динамического обновления станций должны быть доступны PostgreSQL и Redis.

## Установка

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r app/requirements.txt
```

Создайте файл `backend/.env` на основе `backend/.example.env` и укажите параметры подключения:

```env
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

DB_USER=postgres
DB_PASS=пароль
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=название_базы
```

Перед первым запуском убедитесь, что база данных PostgreSQL создана. Миграции выполняются из каталога `backend`:

```bash
alembic upgrade head
```

### 2. Frontend

```bash
cd frontend
npm install
```

## Запуск для разработки

Запускайте каждый процесс в отдельном терминале.

### Backend API

```bash
cd backend
source .venv/bin/activate       # Linux/macOS
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API будет доступен по адресу <http://127.0.0.1:8000>, документация Swagger — по адресу <http://127.0.0.1:8000/docs>.

### Frontend

```bash
cd frontend
npm run dev
```

Интерфейс откроется по адресу <http://localhost:5173>. Vite автоматически проксирует запросы `/stations` и `/ws` на backend `http://127.0.0.1:8000`.

### Celery

Для фоновых задач запустите worker и beat. Если используется один процесс Celery:

```bash
cd backend
source .venv/bin/activate
celery -A app.core.celery_app.celery worker -B --loglevel=info -P solo
```

В окружениях, где worker и beat запускаются отдельно:

```bash
celery -A app.core.celery_app.celery worker --loglevel=info -P solo
celery -A app.core.celery_app.celery beat --loglevel=info
```

### Быстрый запуск Windows

Скрипт `start.ps1` пытается запустить Redis, службу PostgreSQL, backend, Celery worker, Celery beat и frontend в отдельных окнах:

```powershell
.\start.ps1
```

Скрипт ожидает виртуальное окружение `venv\Scripts\python.exe`. Если используется `.venv`, запускайте команды вручную либо измените путь `$venvBin` в скрипте.

## REST API

Все основные REST-маршруты имеют префикс `/stations`.

### Станции

| Метод | Маршрут | Назначение |
|---|---|---|
| `GET` | `/stations` | Получить список станций |
| `GET` | `/stations/{station_id}/` | Получить данные станции |
| `POST` | `/stations/add?latitude=...&longitude=...&type_st=...` | Добавить устройство |
| `POST` | `/stations/{station_id}/remove` | Удалить устройство |
| `POST` | `/stations/set_count?count=...&moving_count=...` | Изменить количество станций |
| `POST` | `/stations/reset` | Сбросить типы и батареи станций |
| `POST` | `/stations/charge_battery?charge=100` | Зарядить батареи |

### Загрязнение и сценарии

| Метод | Маршрут | Назначение |
|---|---|---|
| `POST` | `/stations/{station_id}/pollute?pm25=...&pm10=...` | Установить загрязнение станции |
| `POST` | `/stations/{station_id}/clear_pollution` | Убрать загрязнение станции |
| `POST` | `/stations/clear_pollutions` | Убрать загрязнение всех станций |
| `POST` | `/stations/fake_pollutions` | Увеличить искусственное загрязнение |
| `POST` | `/stations/clear_fake_pollutions` | Сбросить искусственное загрязнение |
| `GET` | `/stations/load_scenarios` | Получить прогноз нагрузки для сценариев |
| `POST` | `/stations/transmit_all?enabled=true` | Включить или отключить передачу всеми станциями |

### Настройки передачи и данные карты

| Метод | Маршрут | Назначение |
|---|---|---|
| `GET` | `/stations/buffer` | Получить параметры буфера |
| `POST` | `/stations/set_buffer?capacity_kb=...` | Изменить ёмкость буфера |
| `POST` | `/stations/set_buffer_rate?rate_ratio=...` | Изменить скорость обработки буфера |
| `GET` | `/stations/msg_weights` | Получить веса сообщений |
| `POST` | `/stations/set_msg_weights` | Изменить веса сообщений |
| `GET` | `/stations/water_mask` | Получить маску водоёмов |
| `GET` | `/stations/roads` | Получить данные дорог |

### Визуализация расписаний

| Метод | Маршрут | Назначение |
|---|---|---|
| `GET` | `/stations/plot/cluster` | Данные кластерного распределения |
| `GET` | `/stations/plot/timezone` | Данные распределения по временным зонам |
| `GET` | `/stations/plot/{option}` | Графическое изображение расписания |
| `POST` | `/stations/plot/cluster_heads` | Построить кластерные головы |

Полный список параметров и интерактивные примеры доступны в Swagger: <http://127.0.0.1:8000/docs>.

## WebSocket метрик

Frontend подключается к:

```text
ws://localhost:5173/ws/metrics
```

В dev-режиме Vite проксирует соединение на backend:

```text
ws://127.0.0.1:8000/ws/metrics
```

Соединение отправляет JSON с текущими метриками примерно раз в секунду: количеством сообщений, весом трафика, заполнением буфера, потерями и средним зарядом движущихся станций.

## Проверка проекта

Frontend:

```bash
cd frontend
npm run lint       # ESLint
npm run build      # production-сборка
npm run preview    # локальный просмотр production-сборки
```

Backend:

```bash
cd backend
.venv/bin/python -m compileall -q app
.venv/bin/python -c "import app.main; print('backend import: ok')"
```

## Production-сборка frontend

```bash
cd frontend
npm run build
```

Готовые файлы будут созданы в `frontend/dist`. Для публикации в production нужен веб-сервер, который отдаёт эти файлы и проксирует `/stations` и `/ws` к FastAPI.

## Примечания
## 3. Интеграция с платформой СИГМА

Дашборд выступает **источником событий**: отправляет в СИГМУ изменения
качества воздуха и состояния сети, а не ждёт запросов от платформы.
Интеграция работает на бэкенде, секреты не попадают во фронтенд и в git.

### 3.1 Что отправляется

| Тип события | Когда | Содержимое |
|---|---|---|
| `pollution.exceeded` | станция перешла из нормы в превышение ПДК | PM2.5/PM10, координаты, id станции, подсказка уровня |
| `pollution.normalized` | станция вернулась в норму | те же измерения после возврата |
| `network.snapshot` | раз в `SIGMA_SNAPSHOT_INTERVAL` (по умолчанию 5 минут) | агрегаты сети: активные устройства, средние/максимальные PM, превышения |
| `network.limit_exceeded` | впервые превышен лимит передачи | сообщения/с, порог, параметры сети |
| `network.buffer_overflow` | впервые переполнился буфер очереди | потерянный вес, заполнение буфера |
| `sigma.test` | кнопка «Проверить связь» | тестовое событие |

События отправляются **по переходам состояний**, а не каждый тик. Повторные
срабатывания ограничены кулдаунами (`SIGMA_STATION_COOLDOWN`,
`SIGMA_NETWORK_COOLDOWN`), поэтому одна и та же грязная станция не создаёт
поток сообщений.

### 3.2 Настройка

1. Получите пользовательский токен Keycloak (вход человека) и UUID
   пользователя.
2. Зарегистрируйте приложение и источник:
   ```bash
   cd backend
   python tools/sigma_setup.py register ^
       --user-token "<access_token>" ^
       --user-id "<uuid>" ^
       --app-url "https://eco.curxtor.ru"
   ```
   Скрипт создаст приложение, сгенерирует `secret_key`, создаст источник
   с JSON-схемой и запишет `backend/.sigma.env` (файл в git не попадает).
3. Скопируйте строки `SIGMA_*` в `backend/.env` (или передайте их в
   окружение) и перезапустите backend.
4. Проверьте связь:
   ```bash
   python tools/sigma_setup.py check --test-event
   ```
   Либо откройте вкладку **«Управление»** → блок **«Платформа СИГМА»** →
   **«Проверить связь»**.

Если приложение и источник уже выданы оператором, достаточно заполнить
`SIGMA_APP_ID`, `SIGMA_SECRET_KEY`, `SIGMA_SOURCE_ID` — скрипт не нужен.

### 3.3 Переменные окружения

Полный список — в `backend/.example.env`. Основные:

| Переменная | Назначение |
|---|---|
| `SIGMA_ENABLED` | включает интеграцию (по умолчанию — при наличии всех трёх секретов) |
| `SIGMA_ENDPOINT_URL` | Gateway, по умолчанию `https://fwsigma.nsu.ru/api-gateway` |
| `SIGMA_KEYCLOAK_URL` | база Keycloak **вместе с** `/keycloak` |
| `SIGMA_REALM` | realm, по умолчанию `sigma` |
| `SIGMA_APP_ID` / `SIGMA_SECRET_KEY` | учётные данные приложения |
| `SIGMA_SOURCE_ID` | UUID источника событий |
| `SIGMA_SNAPSHOT_INTERVAL` | период среза сети, сек |
| `SIGMA_TEST_ENABLED` | разрешает кнопку тестового события |
| `SIGMA_DEMO_ENRICH` | демо-режим: после отправки ставит `level` через PATCH |

`SIGMA_DEMO_ENRICH` нужен только для демонстрации: платформа игнорирует
поле `level` при POST. В боевом контуре уровень назначает Kafka-обработчик
`raw_<source_id> → enriched_<source_id>`, либо оператор ставит его вручную.

### 3.4 API дашборда

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/sigma/status` | состояние интеграции, очередь, счётчики, последняя ошибка |
| POST | `/sigma/test` | отправить `sigma.test` (если разрешено) |
| GET | `/sigma/events?processed=&level=&limit=` | последние события источника |
| GET | `/sigma/dead?limit=` | недоставленные события (dead-letter) |

Секреты в ответах не возвращаются: `source_id` маскируется, `secret_key`
не выводится никогда.

### 3.5 Надёжность

* Исходящие события складываются в Redis-outbox и отправляются фоновым
  воркером: короткий сбой сети или перезапуск backend не теряет события.
* Дедупликация по `correlation_id`, рейт-лимит
  `SIGMA_MAX_EVENTS_PER_MINUTE`.
* Повторы с экспоненциальной задержкой (5…300 с, до
  `SIGMA_MAX_ATTEMPTS`). При таймауте перед повтором выполняется сверка
  события по `correlation_id`, чтобы не создать дубль.
* `403` (источник неактивен/IP не в allowlist), `422` и прочие `4xx`
  сразу уходят в dead-letter; `5xx` и сетевые ошибки повторяются.
* При `401` токен приложения обновляется один раз и запрос повторяется.

Интеграция рассчитана на **один процесс uvicorn** (как и остальной
дашборд): воркер и детектор живут в памяти процесса. При нескольких
воркерах нужен отдельный сервис-отправитель.

### 3.6 Проверка без реальной СИГМЫ

```bash
cd backend
..\venv\Scripts\python.exe tests\sigma_smoke.py
```

Smoke-тест поднимает локальный мок (Keycloak + Gateway), проверяет
токен, регистрацию источника, отправку, чтение, PATCH, сборку сообщений,
правила уровней и детектор переходов. БД, Redis и доступ к СИГМЕ не нужны.

## Дополнительные примечания

- Frontend использует относительные URL API, поэтому отдельный `.env` для frontend в текущей конфигурации не требуется.
- Backend запускается на порту `8000`, frontend — на порту `5173`.
- Станции с нулевым зарядом не передают данные и не отображаются в активных расчётах.
- Файл `backend/.env` содержит локальные секреты и не должен публиковаться в Git.
