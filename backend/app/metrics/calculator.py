def calculate_messages_per_second(
    stations: list[dict],
    mode: str,
    cluster_count: int = 0,
    fake_pollutions: int = 0,
    timezone_count: int = 0,
) -> int:

    if mode == "timezone":
        # в режиме тайм-зон сообщения передают не все станции,
        # а только зоны — сообщений столько же, сколько тайм-зон
        return timezone_count

    if mode == "clusters":
        return len(stations)

    if mode == "cluster_head":
        polluted = sum(1 for s in stations if s["overTLV"])
        return cluster_count + polluted + fake_pollutions

    return 0
