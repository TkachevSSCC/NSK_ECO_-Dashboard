import numpy as np


def count_out_of_cluster(
    stations: list[dict],
    centroids: list[list[float]] | None,
    radii: list[float] | None,
) -> int:
    """
    Сколько устройств вышли за пределы своего кластера:
    расстояние от устройства до ближайшего центроида больше
    радиуса этого кластера (радиус зафиксирован при кластеризации).
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

    return int((dist > rad[nearest]).sum())


def calculate_messages_per_second(
    stations: list[dict],
    mode: str,
    cluster_count: int = 0,
    fake_pollutions: int = 0,
    timezone_count: int = 0,
    cluster_centroids: list[list[float]] | None = None,
    cluster_radii: list[float] | None = None,
) -> int:

    if mode == "timezone":
        # в режиме тайм-зон сообщения передают не все станции,
        # а только зоны — сообщений столько же, сколько тайм-зон
        return timezone_count

    if mode == "clusters":
        return len(stations)

    if mode == "cluster_head":
        # в глобальную сеть передают: кластер-хэды (по одному на кластер)
        # плюс устройства, вышедшие за пределы своего кластера
        out = count_out_of_cluster(stations, cluster_centroids, cluster_radii)
        return cluster_count + out

    return 0