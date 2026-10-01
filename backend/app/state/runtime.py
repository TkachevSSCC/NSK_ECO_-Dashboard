runtime_state = {
    "mode": "",
    "cluster_count": 0, 
    "stations_count": 0,
    "fake_pollutions": 0,
    "cluster_variant": None,          # "head" | "battery_life" | None
    "limit_alert_sent": False,        # предупреждение о лимите уже отправлено
    "rebuild_in_progress": False,     # идёт авто-перестроение кластеров
    "buffer_capacity_kb": 1000.0,     # ёмкость буфера очереди, КБ
    "buffer_rate_ratio": 1.0,          # скорость обработки буфера сетью, × от веса сообщений/с
    "buffer_level_kb": 0.0,           # накоплено в буфере, КБ
    "timezone_zone_heads": [],         # id устройств, передающих за тайм-зону
}