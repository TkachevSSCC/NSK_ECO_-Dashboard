import time

import numpy as np

# порог критического превышения: PM2.5 + PM10 суммарно
DANGER_SUM_THRESHOLD = 200
# каждый временный кластер = узел + 3 ближайших = 4 устройства,
# каждое передаёт по сообщению в глобальную сеть
DANGER_CLUSTER_MESSAGES = 4
# временный кластер существует 60 секунд с момента первого превышения
DANGER_CLUSTER_TTL_SECONDS = 60

# когда каждый узел впервые превысил порог (монотонные часы)
_danger_since: dict[int, float] = {}
# узлы, чей 60-секундный кластер уже «отгорел», но уровень всё ещё выше
# порога: новый временный кластер не перезаводится, пока уровень не упадёт
_danger_expired: set[int] = set()


def count_danger_clusters(stations: list[dict]) -> int:
    """
    Число активных временных кластеров: станций с суммой PM2.5 + PM10 > 200.
    Каждый временный кластер существует 60 секунд с момента первого
    превышения, затем исчезает и не перезаводится, пока уровень станции
    не опустится ниже порога (новое событие запускает новый кластер).
    """
    now = time.monotonic()
    n = 0
    for s in stations:
        sid = s["id"]
        pm25 = float(s.get("PM_2_5") or 0)
        pm10 = float(s.get("PM_10") or 0)
        if pm25 + pm10 <= DANGER_SUM_THRESHOLD:
            # превышение закончилось — состояние узла сбрасываем
            _danger_since.pop(sid, None)
            _danger_expired.discard(sid)
            continue
        if sid in _danger_expired:
            # 60 секунд уже прошли, уровень всё ещё высокий:
            # кластер не перезаводится до нового события
            continue
        started = _danger_since.get(sid)
        if started is None:
            _danger_since[sid] = now
            n += 1
        elif now - started <= DANGER_CLUSTER_TTL_SECONDS:
            n += 1
        else:
            # время жизни кластера истекло
            _danger_since.pop(sid, None)
            _danger_expired.add(sid)
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
        # каждая станция временного кластера (4) передаёт в сеть дополнительно
        return timezone_count + danger_cluster_count * DANGER_CLUSTER_MESSAGES

    if mode == "clusters":
        # «передают все» — все станции передают, плюс каждый временный
        # кластер дополнительно передаёт 4 сообщения в глобальную сеть
        return len(stations) + danger_cluster_count * DANGER_CLUSTER_MESSAGES

    if mode == "cluster_head":
        # в глобальную сеть передают: кластер-хэды (по одному на кластер)
        # плюс устройства, вышедшие за пределы своего кластера,
        # плюс 4 станции каждого временного кластера
        out = count_out_of_cluster(stations, cluster_centroids, cluster_radii)
        return cluster_count + out + danger_cluster_count * DANGER_CLUSTER_MESSAGES

    # без оверлея в сеть передают только временные кластеры:
    # узел + 3 ближайших = 4 сообщения на кластер
    return danger_cluster_count * DANGER_CLUSTER_MESSAGES