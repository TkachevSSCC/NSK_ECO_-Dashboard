import numpy as np

# порог критического превышения: PM2.5 + PM10 суммарно
DANGER_SUM_THRESHOLD = 200

# временный кластер: сам превысивший узел + DANGER_NEIGHBORS ближайших
# устройств (значение общее с фронтендом, где рисуется круг на карте)
DANGER_NEIGHBORS = 5
DANGER_CLUSTER_SIZE = DANGER_NEIGHBORS + 1

# каждый участник временного кластера передаёт в глобальную сеть во столько
# же раз чаще, сколько задано множителем: 6 участников × 5 сообщений,
# то есть 30 сообщений в глобальную сеть с каждого временного кластера
DANGER_CLUSTER_MSG_MULTIPLIER = 5
DANGER_CLUSTER_MESSAGES = DANGER_CLUSTER_SIZE * DANGER_CLUSTER_MSG_MULTIPLIER

# тип устройства «кластер-хэд» (стандартная/движущаяся/хэд)
CLUSTER_HEAD_TYPE = 2


def _active_danger_ids(stations: list[dict]) -> set[int]:
    """
    id узлов с активным временным кластером: те, у кого сумма
    PM2.5 + PM10 выше порога. Кластер существует ровно пока держится
    превышение, поэтому состояние вести не нужно — как только сумма падает
    до порога или ниже, кластер исчезает, а новое превышение создаёт
    его снова.
    """
    active: set[int] = set()
    for s in stations:
        pm25 = float(s.get("PM_2_5") or 0)
        pm10 = float(s.get("PM_10") or 0)
        if pm25 + pm10 > DANGER_SUM_THRESHOLD:
            active.add(int(s["id"]))
    return active


def count_danger_clusters(stations: list[dict]) -> int:
    """
    Число активных временных кластеров: станций с суммой PM2.5 + PM10 > 200.
    Каждый такой кластер существует, пока держится превышение порога, и
    передаёт в глобальную сеть DANGER_CLUSTER_MESSAGES сообщений.
    """
    return len(_active_danger_ids(stations))


def _outside_mask(
    stations: list[dict],
    centroids: list[list[float]] | None,
    radii: list[float] | None,
    moving_only: bool = False,
) -> np.ndarray:
    """Маска устройств, вышедших за пределы своего кластера."""
    if not stations or not centroids or not radii:
        return np.zeros(len(stations), dtype=bool)

    coords = np.array(
        [[s["latitude"], s["longitude"]] for s in stations], dtype=float
    )
    centers = np.array(centroids, dtype=float)
    rad = np.array(radii, dtype=float)
    if len(centers) == 0 or len(centers) != len(rad):
        return np.zeros(len(stations), dtype=bool)

    # каждая станция относится к ближайшему центроиду
    dists = np.linalg.norm(coords[:, None, :] - centers[None, :, :], axis=2)
    nearest = np.argmin(dists, axis=1)
    dist = dists[np.arange(len(coords)), nearest]

    outside = dist > rad[nearest]
    if moving_only:
        moving = np.array([s.get("type_st") == 1 for s in stations])
        outside = outside & moving
    return outside


def out_of_cluster_ids(
    stations: list[dict],
    centroids: list[list[float]] | None,
    radii: list[float] | None,
) -> set[int]:
    """id устройств, вышедших за пределы своего кластера."""
    outside = _outside_mask(stations, centroids, radii)
    return {int(s["id"]) for s, out in zip(stations, outside) if out}


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
    return int(_outside_mask(stations, centroids, radii, moving_only).sum())


def danger_cluster_members(
    stations: list[dict], cluster_size: int = DANGER_CLUSTER_SIZE
) -> list[int]:
    """
    id участников активных временных кластеров: сам превысивший узел
    и cluster_size-1 ближайших к нему устройств. Они передают сообщение
    в глобальную сеть дополнительно к основному режиму.
    """
    active = _active_danger_ids(stations)
    if not active:
        return []

    coords = np.array(
        [[s["latitude"], s["longitude"]] for s in stations], dtype=float
    )
    ids = np.array([s["id"] for s in stations])
    index = {int(sid): i for i, sid in enumerate(ids)}

    members: set[int] = set()
    for sid in active:
        i = index.get(sid)
        if i is None:
            continue
        dists = np.linalg.norm(coords - coords[i], axis=1)
        nearest = np.argsort(dists)[:cluster_size]
        members.update(int(ids[j]) for j in nearest)
    return sorted(members)


def global_sender_ids(
    stations: list[dict],
    mode: str,
    centroids: list[list[float]] | None = None,
    radii: list[float] | None = None,
    danger_ids: list[int] | tuple[int, ...] = (),
    timezone_heads: list[int] | tuple[int, ...] = (),
) -> set[int]:
    """
    id устройств, которые за тик передают сообщение в глобальную сеть.
    Остальные устройства передают в пределах зоны.
    """
    known = {int(s["id"]) for s in stations}
    danger = {int(i) for i in danger_ids} & known

    if mode == "clusters":
        return known | danger

    if mode == "timezone":
        # в глобальную сеть передают только зоны — по одному устройству от зоны
        return ({int(i) for i in timezone_heads} & known) | danger

    if mode == "cluster_head":
        heads = {int(s["id"]) for s in stations if s.get("type_st") == CLUSTER_HEAD_TYPE}
        outside = out_of_cluster_ids(stations, centroids, radii)
        return (heads | outside | danger) & known

    # без оверлея в сеть передают только временные кластеры
    return danger


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
        # каждый временный кластер добавляет DANGER_CLUSTER_MESSAGES
        return timezone_count + danger_cluster_count * DANGER_CLUSTER_MESSAGES

    if mode == "clusters":
        # «передают все» — все станции передают, плюс каждый временный
        # кластер дополнительно передаёт DANGER_CLUSTER_MESSAGES сообщений
        # в глобальную сеть
        return len(stations) + danger_cluster_count * DANGER_CLUSTER_MESSAGES

    if mode == "cluster_head":
        # в глобальную сеть передают: кластер-хэды (по одному на кластер)
        # плюс устройства, вышедшие за пределы своего кластера,
        # плюс DANGER_CLUSTER_MESSAGES сообщений каждого временного кластера
        out = count_out_of_cluster(stations, cluster_centroids, cluster_radii)
        return cluster_count + out + danger_cluster_count * DANGER_CLUSTER_MESSAGES

    # без оверлея в сеть передают только временные кластеры:
    # DANGER_CLUSTER_MESSAGES сообщений на кластер
    return danger_cluster_count * DANGER_CLUSTER_MESSAGES