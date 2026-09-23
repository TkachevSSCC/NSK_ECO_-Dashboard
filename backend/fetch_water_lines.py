"""Загрузчик линий рек/каналов (waterway=river/canal) из Overpass.

Сохраняет в app/data/water_lines.json список линий вида
{"waterway": "...", "pts": [[lat, lon], ...]} — только те, что пересекают
зону станций (с запасом).
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, ".")

SOUTH, WEST, NORTH, EAST = 54.75, 82.80, 55.22, 83.28

QUERY = f"""
[out:json][timeout:50];
(
  way["waterway"="river"]({SOUTH},{WEST},{NORTH},{EAST});
  way["waterway"="canal"]({SOUTH},{WEST},{NORTH},{EAST});
);
out geom;
"""

# зона станций + запас
ST_LAT0, ST_LAT1, ST_LON0, ST_LON1 = 54.81, 55.16, 82.87, 83.21
MARGIN = 0.012


def intersects_bbox(lats, lons):
    return not (
        max(lats) < ST_LAT0 - MARGIN
        or min(lats) > ST_LAT1 + MARGIN
        or max(lons) < ST_LON0 - MARGIN
        or min(lons) > ST_LON1 + MARGIN
    )


def main():
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=QUERY.encode("utf-8"),
        headers={"User-Agent": "nsk-eco-dashboard/dev contact=local"},
    )
    print("Downloading river/canal lines from Overpass...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    lines = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom:
            continue
        pts = [(g["lat"], g["lon"]) for g in geom]
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        if not intersects_bbox(lats, lons):
            continue
        lines.append(
            {
                "id": el["id"],
                "waterway": el.get("tags", {}).get("waterway", "river"),
                "pts": pts,
            }
        )

    out_path = os.path.join(os.path.dirname(__file__), "app", "data", "water_lines.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(lines, f)
    print(f"Saved {len(lines)} lines -> {out_path}")


if __name__ == "__main__":
    main()