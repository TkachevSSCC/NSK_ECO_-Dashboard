"""Одноразовый загрузчик полигонов воды для Новосибирска из Overpass API.

Сохраняет в app/data/water.json список полигонов вида [[lat, lon], ...].
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, ".")

BBOX = "54.75,82.80,55.22,83.28"  # (запад-юг-восток-север — lat,lon,lat,lon)
#  надёжнее: (s, w, n, e)
SOUTH, WEST, NORTH, EAST = 54.75, 82.80, 55.22, 83.28

QUERY = f"""
[out:json][timeout:50];
(
  way["natural"="water"]({SOUTH},{WEST},{NORTH},{EAST});
  way["waterway"="riverbank"]({SOUTH},{WEST},{NORTH},{EAST});
  relation["natural"="water"]({SOUTH},{WEST},{NORTH},{EAST});
);
out geom;
"""


def poly_area(points):
    """Площадь полигона (лат-лон) методом шнурка в градусах^2 (примерно)."""
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def main():
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=QUERY.encode("utf-8"),
        headers={"User-Agent": "nsk-eco-dashboard/dev contact=local"},
    )
    print("Downloading water polygons from Overpass...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    polygons = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom:
            continue
        # (lat, lon) список
        pts = [(g["lat"], g["lon"]) for g in geom]
        if len(pts) < 4:
            continue
        area = poly_area(pts)
        # отсекаем совсем крошечные лужи и однолинейные объекты
        if area < 1e-6:
            continue
        polygons.append(pts)

    # убираем почти-дубли (одинаковые первые точки полигонов одного типа) — не критично
    out_path = os.path.join(os.path.dirname(__file__), "app", "data", "water.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(polygons, f)
    print(f"Saved {len(polygons)} polygons -> {out_path}")


if __name__ == "__main__":
    main()