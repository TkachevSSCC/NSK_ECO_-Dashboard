# Доступ к результатам WRF через DAP-интерфейс (ncStore)

Онлайн-чтение NetCDF-файлов результатов (wrfout_d0X, geo_em и т.п.) **без скачивания**:
срезы по времени/широте/долготе/вертикали, вывод в JSON или сырые байты numpy.
Аналог OPeNDAP/PyDAP, реализован в WRFWebQueue (модуль ncStore).

## 1. Точка входа и доступы

| Параметр | Значение |
|---|---|
| Base URL | `http://84.237.86.54:8090` (сервис слушает `0.0.0.0:8090`) |
| Веб-интерфейс очереди | `http://84.237.86.54:8090/queue`, логин `admin` / пароль из `wrfwebqueue.conf` (`WRF_QUEUE_ADMIN_PASS`) |
| IMDAF.web (визуализация) | `http://84.237.86.54:8002`, `admin` / `Admin123!` (для регистрации файлов из WRFWebQueue используется `IMDAF_WEB_ADMIN_USER/PASS` из конфига) |

**Авторизация обязательна для всех `/api/*`**, два способа:

1. **Веб-сессия** — логин на странице `/login`, дальше браузер сам передаёт cookie.
2. **API-ключ** (для скриптов):
   - заголовок `X-API-Key: <token>` или query-параметр `api_key=<token>`;
   - создание ключа: `POST /api/auth/tokens` (form-поле `label`) — возвращает
     `{"token": "...", "label": "..."}`; **сырой токен показывается один раз**,
     в БД хранится только sha256-хэш. Также можно создать через веб-страницу `/api-keys`.

```bash
# пример: создать ключ
curl -s -X POST http://84.237.86.54:8090/api/auth/tokens \
  -H "X-API-Key: <существующий_ключ>" -d "label=dap-client"
```

## 2. Эндпоинты

### 2.1 `GET /api/ncstore/info` — метаданные файла

| Параметр | Описание |
|---|---|
| `path` | абсолютный путь к `.nc` файлу |

Возвращает размеры (dimensions), список переменных с их атрибутами и глобальные
атрибуты. Кэшируется 60 секунд (`NCSTORE_INFO_CACHE_TTL`).

```bash
curl -s "http://84.237.86.54:8090/api/ncstore/info?path=/mnt/store/LIMA/WRF4/LIMAChem_arctic/OUTPUT/wrfout_d01" \
  -H "X-API-Key: $KEY" | python3 -m json.tool | head -40
```

### 2.2 `GET /api/ncstore/data` — данные переменной со срезами

| Параметр | Описание | Пример |
|---|---|---|
| `path` | абсолютный путь к `.nc` | `/mnt/store/LIMA/.../wrfout_d01` |
| `variable` | имя переменной | `TT`, `UU`, `VV`, `PM25` |
| `time` | срез по времени (измерение `Time`) | `0`, `0:5`, `:10` |
| `south_north` | срез по широте | `50:150` |
| `west_east` | срез по долготе | `50:150` |
| `bottom_top` | срез по вертикали | `0`, `0:10` |
| `slices` | **универсальный** срез — через запятую, в порядке измерений переменной | `0:37,0:81,0:87` |
| `outputFormat` | `json` (по умолчанию) или `binary` | `binary` |

Синтаксис среза: `N` — один индекс; `A:B`, `:B`, `A:` — как в Python.
Именованные параметры (`time`, `south_north`, ...) сопоставляются с измерениями
**по имени без учёта регистра**; если задан `slices`, именованные игнорируются.

Ограничения (из `wrfwebqueue.conf`):
- размер ответа ≤ `NCSTORE_MAX_RESPONSE_MB` = **50000 МБ**, иначе 400 «Уменьшите срезы»;
- таймаут чтения `NCSTORE_REQUEST_TIMEOUT` = **600 с**, иначе 504 — уменьшите диапазон времени или используйте `outputFormat=binary`.

**JSON-режим** (gzip): `{success, path, variable, dimensions, shape, dtype, slices_used, attributes, data, size_bytes}`.

```bash
# TT на первом временном шаге, нижний уровень, центр сетки 100x100
curl -s "http://84.237.86.54:8090/api/ncstore/data?path=/mnt/store/LIMA/WRF4/LIMAChem_arctic/OUTPUT/wrfout_d01&variable=TT&time=0&bottom_top=0&slices=:1,0,100:200,100:200" \
  -H "X-API-Key: $KEY" | python3 -m json.tool | head -30
```

**Binary-режим** — сырые байты numpy-массива (`application/octet-stream`) с
заголовками `X-NcStore-Variable`, `X-NcStore-Shape` (через запятую),
`X-NcStore-Dtype`, `X-NcStore-Size`. Для больших массивов быстрее JSON в разы.

```python
import requests, numpy as np

r = requests.get("http://84.237.86.54:8090/api/ncstore/data",
                 params={"path": "/mnt/store/LIMA/WRF4/LIMAChem_arctic/OUTPUT/wrfout_d01",
                         "variable": "TT", "slices": ":1,0,::", "outputFormat": "binary"},
                 headers={"X-API-Key": KEY}, timeout=600)
shape = tuple(int(x) for x in r.headers["X-NcStore-Shape"].split(","))
dtype = np.dtype(r.headers["X-NcStore-Dtype"])
tt = np.frombuffer(r.content, dtype=dtype).reshape(shape)   # (1, ny, nx)
```

### 2.3 `GET /api/ncstore/stats` — статистика по переменной

| Параметр | Описание |
|---|---|
| `path`, `variable` | как выше |
| `time` | срез по времени (опционально) |

Возвращает min, max, mean, std.

## 3. Ограничения доступа к файлам

- только расширения `.nc`, `.netcdf`;
- путь (после `realpath`) должен находиться в `/mnt/store/LIMA/` или
  `/home/imdafdevel/WRFEnv/` — иначе 403;
- обход через символические ссылки наружу не работает (проверяется realpath).

## 4. Логирование

Все DAP-запросы пишутся в `logs/dap_downloads.log` с указанием пользователя:

```
2026-09-20 19:55:01 [DAP] INFO request: file=wrfout_d01 user=admin
2026-09-20 19:55:03 [DAP] DATA request: file=wrfout_d01 var=TT user=dap-client slices=:1,0,...
```

## 5. Настройка (`wrfwebqueue.conf`)

| Ключ | По умолчанию | Назначение |
|---|---|---|
| `NCSTORE_ENABLED` | `True` | включить/выключить онлайн-доступ |
| `NCSTORE_MAX_RESPONSE_MB` | `50000` | лимит размера ответа, МБ |
| `NCSTORE_INFO_CACHE_TTL` | `60` | кэш метаданных, сек |
| `NCSTORE_REQUEST_TIMEOUT` | `600` | таймаут чтения данных, сек (для файлов 20 ГБ+) |

## 6. Типовые сценарии

1. **Разведка файла**: `/api/ncstore/info` → выбрать переменную и размеры измерений.
2. **Профиль по вертикали в одной точке**: `slices=0,0,J,I` (Time, bottom_top, south_north, west_east).
3. **Поле на одном уровне за весь прогон**: `slices=:N,0,:,:` — при превышении лимита
   разбить по времени (`time=A:B`) или взять `outputFormat=binary`.
4. **Контроль качества без скачивания**: `/api/ncstore/stats` по ключевым переменным (TT, U10, PM25).
