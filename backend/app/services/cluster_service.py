"""
Общая логика построения кластеров и кластер-хэдов.

Используется и эндпоинтом /stations/plot/cluster, и автоматическим
перестроением в metrics_loop, когда более 50% движущихся устройств
выходят за пределы своих кластеров.
"""
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from skimage import measure

from app.services.station_service import StationService
from app.state.runtime import runtime_state
from app.core.redis_client import get_redis, POLLUTION_OVERRIDE_KEY


def _build_polygons(coords, kmeans, h=0.001, pad=0.01):
    """Контурные зоны кластеров (та же сетка, что и раньше)."""
    x_min, x_max = coords[:, 0].min() - pad, coords[:, 0].max() + pad
    y_min, y_max = coords[:, 1].min() - pad, coords[:, 1].max() + pad
    xx, yy = np.meshgrid(
        np.arange(x_min, x_max, h),
        np.arange(y_min, y_max, h),
    )
    Z = kmeans.predict(np.c_[xx.ravel(), yy.ravel()]).reshape(xx.shape)

    polygons = []
    for cluster_id in np.unique(Z):
        mask = Z == cluster_id
        padded_mask = np.pad(mask, pad_width=1, mode="constant", constant_values=0)
        contours = measure.find_contours(padded_mask.astype(float), 0.5)

        for contour in contours:
            latitudes = xx[0, 0] + contour[:, 1] * h
            longitudes = yy[0, 0] + contour[:, 0] * h
            polygon = [[float(lat), float(lon)] for lat, lon in zip(latitudes, longitudes)]
            polygons.append({"cluster_id": int(cluster_id), "points": polygon})

    return polygons


def _pick_head(cluster_id, kmeans, coords, station_ids, batteries, type_sts, variant):
    """Выбирает хэд кластера: battery_life — из стационарных и с макс. батареей,
    иначе — ближайший к центроиду."""
    if variant == "battery_life":
        valid_mask = (kmeans.labels_ == cluster_id) & (type_sts == 0)
    else:
        valid_mask = kmeans.labels_ == cluster_id

    cluster_points = coords[valid_mask]
    cluster_ids = station_ids[valid_mask]
    if len(cluster_points) == 0:
        return None, None

    if variant == "battery_life":
        cluster_batteries = batteries[valid_mask]
        sorted_idx = np.argsort(cluster_batteries)[::-1][:1]
    else:
        dists = np.linalg.norm(
            cluster_points - kmeans.cluster_centers_[cluster_id], axis=1
        )
        sorted_idx = np.argsort(dists)[:1]

    return cluster_ids[sorted_idx], cluster_points[sorted_idx]


async def recluster(capacity: int = 10, variant: str | None = None) -> dict:
    """
    Перестраивает кластеры и пересчитывает кластер-хэды.

    variant:
      None          — просто кластеры (без хэдов), mode="clusters";
      "head"        — кластеры с хэдами (хэд — ближайший к центроиду);
      "battery_life"— хэды только из стационарных с учётом батареи.

    Обновляет runtime_state: mode, cluster_count, центроиды, радиусы,
    вариант кластеризации. Возвращает polygons/heads/members.
    """
    stations_list = await StationService.find_all()

    stations = [
        {
            "id": st.id,
            "latitude": st.latitude,
            "longitude": st.longitude,
            "PM_2_5": st.PM_2_5,
            "PM_10": st.PM_10,
            "overTLV": st.overTLV,
            "battery_life": st.battery_life,
            "type_st": st.type_st,
        }
        for st in stations_list
    ]

    coords = np.array([[s["latitude"], s["longitude"]] for s in stations])
    station_ids = np.array([s["id"] for s in stations])
    batteries = np.array([s["battery_life"] for s in stations])
    type_sts = np.array([s["type_st"] for s in stations])

    if coords.shape[0] == 0:
        return {"polygons": [], "heads": [], "members": []}

    num_clusters = int(coords.shape[0] // capacity) + 5
    kmeans = KMeans(n_clusters=num_clusters, random_state=0).fit(
        pd.DataFrame(coords, columns=["lat", "lon"])
    )

    polygons = _build_polygons(coords, kmeans)
    cluster_heads = []

    if variant:
        # перерасчёт хэдов: сначала сбрасываем старые хэды, затем назначаем новые
        reset_ids = await StationService.reset_all_types()
        if reset_ids:
            rc = get_redis()
            for sid in reset_ids:
                rc.hdel(POLLUTION_OVERRIDE_KEY, str(sid))

        for cluster_id in range(num_clusters):
            selected_ids, selected_heads = _pick_head(
                cluster_id, kmeans, coords, station_ids, batteries, type_sts, variant
            )
            if selected_ids is None:
                continue
            for sid in selected_ids:
                await StationService.update_type(int(sid))

            cluster_heads_entry = {"cluster_id": int(cluster_id), "heads": []}
            for sid, coords_pair in zip(selected_ids, selected_heads):
                cluster_heads_entry["heads"].append({
                    "id": int(sid),
                    "coords": coords_pair.tolist(),
                    "type": "battery_head" if variant == "battery_life" else "cluster_head",
                })
            cluster_heads.append(cluster_heads_entry)

        runtime_state["mode"] = "cluster_head"
        runtime_state["cluster_count"] = len(polygons)
        runtime_state["cluster_variant"] = variant
        # центроиды и радиусы — для метрики «в глобальную сеть»:
        # сообщения = кластеры + устройства, вышедшие за пределы кластера
        runtime_state["cluster_centroids"] = kmeans.cluster_centers_.tolist()
        radii = np.zeros(num_clusters)
        for c in range(num_clusters):
            cp = coords[kmeans.labels_ == c]
            if len(cp):
                radii[c] = float(
                    np.linalg.norm(cp - kmeans.cluster_centers_[c], axis=1).max()
                )
        runtime_state["cluster_radii"] = radii.tolist()
    else:
        runtime_state["mode"] = "clusters"
        runtime_state["stations_count"] = len(stations)
        runtime_state["cluster_variant"] = None

    # состав каждого кластера: какие устройства в него входят
    members = []
    for cluster_id in range(num_clusters):
        mask = kmeans.labels_ == cluster_id
        cl_ids = [int(sid) for sid in station_ids[mask]]
        members.append({
            "cluster_id": int(cluster_id),
            "count": len(cl_ids),
            "stations": cl_ids,
        })

    return {"polygons": polygons, "heads": cluster_heads, "members": members}