"""Загрузка полигонов воды с выбором endpoint/bbox (для поиска рабочего)."""
import json
import sys
import urllib.request
import os

sys.path.insert(0, ".")

ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

SOUTH, WEST, NORTH, EAST = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])

QUERY = f"""
[out:json][timeout:90];
(
  way["natural"="water"]({SOUTH},{WEST},{NORTH},{EAST});
  way["waterway"="riverbank"]({SOUTH},{WEST},{NORTH},{EAST});
  way["landuse"="reservoir"]({SOUTH},{WEST},{NORTH},{EAST});
);
out geom;
"""


def poly_area(points):
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def run(endpoint):
    req = urllib.request.Request(
        endpoint,
        data=QUERY.encode("utf-8"),
        headers={"User-Agent": "nsk-eco-dashboard/dev contact=local"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


polygons = []
for ep in ENDPOINTS:
    try:
        print(f"trying {ep} ...")
        data = run(ep)
        for el in data.get("elements", []):
            geom = el.get("geometry")
            if not geom:
                continue
            pts = [(g["lat"], g["lon"]) for g in geom]
            if len(pts) >= 4 and poly_area(pts) >= 1e-6:
                polygons.append(pts)
        print(f"OK from {ep}: {len(polygons)} polygons")
        break
    except Exception as e:
        print(f"  failed: {e}")

if polygons:
    out_path = os.path.join(os.path.dirname(__file__), "app", "data", "water.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(polygons, f)
    print(f"Saved {len(polygons)} polygons -> {out_path}")
else:
    print("NO DATA")