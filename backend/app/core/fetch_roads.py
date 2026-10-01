"""Скачивает геометрию дорог региона из OpenStreetMap (Overpass).

Движущиеся станции ездят по реальным дорогам, поэтому нужны координаты
ведущих узлов улиц. Скрипт один раз кладёт их в app/data/roads.json,
дальше их читает app/core/roads.py (см. water.py / fetch_water*).

Запуск:  python -m app.core.fetch_roads

Что скачивается: только проезжаемые улицы внутри зоны станций
(54.81–55.16 с.ш., 82.87–83.21 в.д.). Пешеходные и велодорожки, парковки
и проезды сознательно исключены: устройство на тротуаре едет медленнее и
делает график нечитаемым, а объём данных с ними вырастает вчетверо.
"""
import json
import os
import time
import urllib.parse
import urllib.request

# зона станций (совпадает с GENERATION_LOW/HIGH в api/v1/stations.py)
_REGION_LAT0, _REGION_LON0 = 54.81, 82.87  # юго-западный угол
_REGION_LAT1, _REGION_LON1 = 55.16, 83.21  # северо-восточный угол

# классы дорог, по которым реально ездят
_HIGHWAY_CLASSES = (
    "residential",
    "unclassified",
    "living_street",
    "tertiary",
    "secondary",
    "primary",
)

# зеркала Overpass: первое обычно отвечает быстрее, но может быть занято
_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

_UA = "nsk-eco-dashboard/dev (air-monitoring demo) contact=local"

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_ROADS_PATH = os.path.join(_DATA_DIR, "roads.json")

# сколько знаков после запятой храним: 1e-5 градуса ≈ 1.1 м по широте,
# для движения по улице этого заведомо хватает, а файл получается компактным
_COORD_PRECISION = 5


def build_query() -> str:
    """Overpass QL: все нужные улицы зоны вместе с геометрией узлов."""
    classes = "|".join(_HIGHWAY_CLASSES)
    return (
        "[out:json][timeout:180];"
        f'way["highway"~"^({classes})$"]'
        f"({_REGION_LAT0},{_REGION_LON0},{_REGION_LAT1},{_REGION_LON1});"
        "out geom;"
    )


def download_overpass(query: str, timeout: int = 240) -> dict:
    """Скачивает ответ Overpass. Пробует зеркала по очереди."""
    payload = urllib.parse.urlencode({"data": query}).encode()
    last_error = None
    for endpoint in _OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    endpoint, data=payload, headers={"User-Agent": _UA}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # сеть/лимит — пробуем следующий
                last_error = exc
                print(f"  {endpoint} не ответил: {exc}")
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Overpass недоступен: {last_error}")


def extract_polylines(data: dict) -> list:
    """Из ответа Overpass оставляет полилинии улиц.

    Порядок узлов важен: по нему устройство едет вдоль улицы, поэтому
    точки не переставляем и не разворачиваем. Way без геометрии пропускаем.
    """
    polylines = []
    for element in data.get("elements", []):
        if element.get("type") != "way":
            continue
        geometry = element.get("geometry") or []
        pts = []
        for node in geometry:
            lat = round(float(node["lat"]), _COORD_PRECISION)
            lon = round(float(node["lon"]), _COORD_PRECISION)
            # подряд идущие дубли (мост/тоннель) в Overpass бывают —
            # они не дают продвижения и только раздувают файл
            if pts and pts[-1][0] == lat and pts[-1][1] == lon:
                continue
            pts.append([lat, lon])
        if len(pts) >= 2:
            polylines.append(pts)
    return polylines


def main() -> None:
    query = build_query()
    print("Запрашиваю Overpass…")
    started = time.time()
    data = download_overpass(query)
    polylines = extract_polylines(data)
    points = sum(len(p) for p in polylines)
    print(
        f"Скачано за {time.time() - started:.1f} с: "
        f"{len(polylines)} улиц, {points} точек"
    )

    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_ROADS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "bbox": [_REGION_LAT0, _REGION_LON0, _REGION_LAT1, _REGION_LON1],
                "classes": list(_HIGHWAY_CLASSES),
                "polylines": polylines,
            },
            f,
            separators=(",", ":"),
        )
    size_mb = os.path.getsize(_ROADS_PATH) / (1024 * 1024)
    print(f"Готово: {_ROADS_PATH} ({size_mb:.2f} МБ)")


if __name__ == "__main__":
    main()