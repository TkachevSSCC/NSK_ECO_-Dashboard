import numpy as np

# порог критического превышения: PM2.5 + PM10 суммарно
DANGER_SUM_THRESHOLD = 200


def count_danger_clusters(stations: list[dict]) -> int:
    """
    Число временных кластеров: станций с суммой PM2.5 + PM10 > 200.
    Каждый такой узел образует временный кластер (узел + 3 ближайших)
    и передаёт сообщение в глобальную сеть.
    """
    n = 0
    for s in stations:
        pm25 = float(s.get("PM_2_5") or 0)
        pm10 = float(s.get("PM_10") or 0)
        if pm25 + pm10 > DANGER_SUM_THRESHOLD:
            n += 1
    return n


def count_out_of_cluster(
    stations: list[dict],
    centroids: list[list[float]] | None,
    radii: list[float] | None,
    moving_only: bool = False,
) -> int:
    """
    Сколько устройств вышли за пределы своего кластера:
    расстояние от устройства до ближайшего центроида больше
    радиуса этого кластера (радиус зафиксирован при кластеризации).
    При moving_only=True учитываются только движущиеся (type_st == 1).
    """
    if not stations or not centroids or not radii:
        return 0

    coords = np.array(
        [[s["latitude"], s["longitude"]] for s in stations], dtype=float
    )
    centers = np.array(centroids, dtype=float)
    rad = np.array(radii, dtype=float)
    if len(centers) == 0 or len(centers) != len(rad):
        return 0

    # каждая станция относится к ближайшему центроиду
    dists = np.linalg.norm(coords[:, None, :] - centers[None, :, :], axis=2)
    nearest = np.argmin(dists, axis=1)
    dist = dists[np.arange(len(coords)), nearest]

    outside = dist > rad[nearest]
    if moving_only:
        moving = np.array([s.get("type_st") == 1 for s in stations])
        outside = outside & moving

    return int(outside.sum())


def calculate_messages_per_second(
    stations: list[dict],
    mode: str,
    cluster_count: int = 0,
    fake_pollutions: int = 0,
    timezone_count: int = 0,
    cluster_centroids: list[list[float]] | None = None,
    cluster_radii: list[float] | None = None,
    danger_cluster_count: int = 0,
) -> int:

    if mode == "timezone":
        # в режиме тайм-зон сообщения передают не все станции,
        # а только зоны — сообщений столько же, сколько тайм-зон;
        # временные кластеры передают в сеть дополнительно
        return timezone_count + danger_cluster_count

    if mode == "clusters":
        # «передают все» — все станции уже в сети, повторно не считаем
        return len(stations)

    if mode == "cluster_head":
        # в глобальную сеть передают: кластер-хэды (по одному на кластер)
        # плюс устройства, вышедшие за пределы своего кластера,
        # плюс временные кластеры
        out = count_out_of_cluster(stations, cluster_centroids, cluster_radii)
        return cluster_count + out + danger_cluster_count

    # без оверлея в сеть передают только временные кластеры
    return danger_cluster_count