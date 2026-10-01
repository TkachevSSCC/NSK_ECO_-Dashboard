from datetime import datetime

runtime_metrics = {
    "timestamp": None,
    "messages_per_second": 0,
    "weight_per_second_kb": 0.0,           # вес сообщений в секунду (ушло в сеть), КБ/с
    "global_weight_factor": 1.0,           # средний коэффициент веса по загрязнению (в сеть)
    "zone_weight_factor": 1.0,             # средний коэффициент веса по загрязнению (в зоне)
    "total_messages": 0,
    "total_zone_messages": 0,
    "total_message_weight_kb": 0.0,
    "mode": None,
    "limit_exceeded": False,
    "buffer_capacity_kb": 1000.0,        # ёмкость буфера очереди, КБ
    "buffer_rate_ratio": 1.0,           # скорость обработки буфера, × от веса сообщений/с
    "buffer_processing_kb_s": 0.0,        # пропускная способность сети сейчас, КБ/с
    "buffer_used_kb": 0.0,                # занято буфером за текущий тик, КБ
    "buffer_fill_percent": 0.0,           # заполнение буфера, %
    "dropped_weight_kb": 0.0,             # потеряно из-за переполнения за тик, КБ
    "total_dropped_weight_kb": 0.0,       # потеряно накопительно, КБ
    "buffer_overflow": False,             # буфер переполнен
    "battery_drained_global_pct": 0.0,    # списано за тик за передачу в сеть, %
    "battery_drained_zone_pct": 0.0,      # списано за тик за передачу в зоне, %
    "battery_drained_total_pct": 0.0,     # всего списано за тик, %
    "battery_avg": None,                    # средний заряд движущихся, % (None — не считаем)
    "battery_min": 100.0,                 # минимальный заряд, %
    "battery_max": 100.0,                 # максимальный заряд, %
    "battery_dead_count": 0,              # устройств с нулевым зарядом
    "moving_count": 0,                    # движущихся устройств
    "moving_battery_total": 0.0,          # общий заряд движущихся, %
    "moving_battery_avg": None,           # средний заряд движущихся, % (None — не считаем)
    "active_count": 0,                     # устройств с ненулевым зарядом
    "silent_count": 0,                     # устройств на нулевом заряде (молчат)
}
