import React, {
  useState,
  Fragment,
  useEffect,
  useRef,
  useCallback,
} from "react";
import "./App.css";
import "leaflet/dist/leaflet.css";
import MapView from "./components/MapView";
import EventLog from "./components/EventLog";
import MetricsChartOverlay from "./components/MetricsChartOverlay";
import LoadScenarios from "./components/LoadScenarios";
import { useStations } from "./hooks/useStations";
import {
  buildHeatmapField,
  NOVOSIBIRSK_ANALYSIS_BOUNDS,
  pickInstallSites,
  renderHeatmap,
} from "./lib/heatmap";

const BASE = "";

// отчёт о событиях: новые сверху, держим последние EVENT_LOG_LIMIT записей
const EVENT_LOG_LIMIT = 200;

// временный кластер вокруг узла с превышением: сам узел плюс столько
// ближайших соседей, радиус покрывает крайнего из них
const DANGER_NEIGHBORS = 5;
// предел радиуса кластера, м: с пятью соседями крайний нередко выходит
// за 8 км, и круг перестаёт покрывать его целиком
const DANGER_MAX_RADIUS_M = 12000;

// устройство с ненулевым зарядом: разряженные не передают и не показываются
const isLive = (s) => (s.battery_life ?? 0) > 0;

// расстояние между узлами по гаверсинусу, км
function haversineKm(a, b) {
  const R = 6371;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export default function App() {
  const stations = useStations();

  // id устройств с ненулевым зарядом: разряженные не передают,
  // поэтому не показываются ни в списках, ни в составе зон/кластеров
  const liveIds = new Set(stations.filter(isLive).map((s) => s.id));
  const filterLive = (ids) => (ids || []).filter((id) => liveIds.has(id));

  const [stationCount, setStationCount] = useState("");
  const [movingCount, setMovingCount] = useState(0);
  const [countMsg, setCountMsg] = useState("");

  // ---- отчёт о событиях ----
  // время берётся в момент вызова addEvent, а не при рендере, иначе все
  // строки показали бы одно и то же время; ключ — счётчик, а не индекс,
  // чтобы ключи не «переезжали» при обрезке старых записей
  const [events, setEvents] = useState([]);
  const eventsSeqRef = useRef(0);
  // useCallback с пустым списком зависимостей: addEvent стабилен, поэтому
  // его можно звать из эффекта по [stations] без риска бесконечного цикла
  const addEvent = useCallback((kind, text) => {
    setEvents((prev) =>
      [
        { key: ++eventsSeqRef.current, at: Date.now(), kind, text },
        ...prev,
      ].slice(0, EVENT_LOG_LIMIT)
    );
  }, []);
  const clearEvents = useCallback(() => setEvents([]), []);

  // поле «Устройств» показывает фактическое количество станций;
  // пока пользователь его редактирует, значение не перезаписываем
  const stationCountRef = useRef(null);
  useEffect(() => {
    if (document.activeElement === stationCountRef.current) return;
    if (stations.length > 0) setStationCount(stations.length);
  }, [stations.length]);

  // точечное загрязнение станции
  const [pollId, setPollId] = useState(1);
  const [pollPM25, setPollPM25] = useState("40");
  const [pollPM10, setPollPM10] = useState("70");
  const [pollMsg, setPollMsg] = useState("");
  const [pollFormOpen, setPollFormOpen] = useState(false);
  const [controlOpen, setControlOpen] = useState(false);

  const [zones, setZones] = useState([]);
  const [showZones, setShowZones] = useState(false);

  // ёмкость буфера очереди, КБ (0–1000000)
  const [bufferCapacity, setBufferCapacity] = useState("1000");
  const [bufferMsg, setBufferMsg] = useState("");
  // скорость обработки буфера сетью, во сколько раз от веса сообщений/с
  const [bufferRate, setBufferRate] = useState("1");
  const [bufferRateMsg, setBufferRateMsg] = useState("");
  const [weightPerSecond, setWeightPerSecond] = useState(0);

  // вес одного сообщения в глобальную сеть и в пределах зоны, КБ
  const [msgGlobal, setMsgGlobal] = useState("1");
  const [msgZone, setMsgZone] = useState("0.4");
  const [msgWeightMsg, setMsgWeightMsg] = useState("");

  // при загрузке подтягиваем ёмкость и скорость обработки буфера с бэкенда,
  // чтобы поля показывали реальные значения, а не значения по умолчанию
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${BASE}/stations/buffer`);
        const data = await res.json();
        if (!res.ok || data.status === "error") return;
        if (!cancelled) {
          setBufferCapacity(data.buffer_capacity_kb);
          setBufferMsg(`Ёмкость буфера: ${data.buffer_capacity_kb} КБ за тик`);
          setBufferRate(data.buffer_rate_ratio);
          setBufferRateMsg(
            `Скорость обработки буфера: ×${data.buffer_rate_ratio} от веса сообщений в секунду`
          );
        }
      } catch (err) {
        if (!cancelled) console.error("Ошибка чтения буфера:", err);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  // при загрузке подтягиваем актуальные веса сообщений с бэкенда
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${BASE}/stations/msg_weights`);
        const data = await res.json();
        if (!res.ok || data.status === "error") return;
        if (!cancelled) {
          setMsgGlobal(String(data.global_msg_kb));
          setMsgZone(String(data.zone_msg_kb));
        }
      } catch (err) {
        if (!cancelled) {
          console.error("Ошибка загрузки весов сообщений:", err);
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const [showClusterHeads, setShowClusterHeads] = useState(false);
  const [showBatteryHeads, setShowBatteryHeads] = useState(false);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [heatmapImage, setHeatmapImage] = useState(null);
  const [heatmapBounds, setHeatmapBounds] = useState(null);
  const [installSites, setInstallSites] = useState([]);

  const [clusters, setClusters] = useState(null);
  // «передают все»: все станции передают в глобальную сеть (mode="clusters").
  // При загрузке страницы режим включается автоматически
  const [allTransmit, setAllTransmit] = useState(true);

  // добавление устройства кликом по карте: addPhase — режим размещения,
  // addType — тип (0 стационарное, 1 движущееся)
  const [addPhase, setAddPhase] = useState(false);
  const [addType, setAddType] = useState(0);
  const [addMsg, setAddMsg] = useState("");
  const [addBusy, setAddBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const enable = async () => {
      try {
        await fetch(`${BASE}/stations/transmit_all?enabled=true`, {
          method: "POST",
        });
      } catch (err) {
        if (!cancelled) {
          console.error("Ошибка включения режима «передают все»:", err);
        }
      }
    };
    setAllTransmit(true);
    enable();
    return () => {
      cancelled = true;
    };
  }, []);

  // подсветка устройств и раскрытые строки таблицы зон/кластеров
  const [highlightIds, setHighlightIds] = useState([]);
  const [expanded, setExpanded] = useState({});

  const toggleHighlight = (ids = []) => {
    setHighlightIds((prev) => {
      const set = new Set(prev);
      // если группа уже вся подсвечена — снимаем, иначе добавляем
      if (ids.length > 0 && ids.every((id) => set.has(id))) {
        ids.forEach((id) => set.delete(id));
      } else {
        ids.forEach((id) => set.add(id));
      }
      return [...set];
    });
  };

  const toggleExpanded = (key) =>
    setExpanded((p) => ({ ...p, [key]: !p[key] }));

  const clearHighlights = () => {
    setHighlightIds([]);
    setExpanded({});
  };

  const resetOverlays = async () => {
    const res = await fetch(`${BASE}/stations/reset`, { method: "POST" });
    return res.json();
  };

  const handleToggleClusterHeads = async () => {
    if (showClusterHeads) {
      setShowClusterHeads(false);
      clearHighlights();
      await resetOverlays();
      addEvent("act", "Кластеры с хедами скрыты, оверлеи сброшены");
    } else if (showBatteryHeads) {
      setShowBatteryHeads(false);
      clearHighlights();
      await resetOverlays();
      addEvent("act", "Кластеры с питанием скрыты, оверлеи сброшены");
    } else {
      setShowZones(false);
      setZones([]);
      setAllTransmit(false);
      const response = await fetch(`${BASE}/stations/plot/cluster?mode=head`);
      const data = await response.json();
      setClusters(data);
      setShowClusterHeads(true);
      addEvent("act", `Показаны кластеры с хедами: ${(data || []).length} шт.`);
    }
  };

  const handleToggleBatteryHeads = async () => {
    if (showBatteryHeads) {
      setShowBatteryHeads(false);
      clearHighlights();
      await resetOverlays();
      addEvent("act", "Кластеры с питанием скрыты, оверлеи сброшены");
    } else if (showClusterHeads) {
      setShowClusterHeads(false);
      clearHighlights();
      await resetOverlays();
      addEvent("act", "Кластеры с хедами скрыты, оверлеи сброшены");
    } else {
      setShowZones(false);
      setZones([]);
      setAllTransmit(false);
      const response = await fetch(
        `${BASE}/stations/plot/cluster?mode=battery_life`
      );
      const data = await response.json();
      setClusters(data);
      setShowBatteryHeads(true);
      addEvent(
        "act",
        `Показаны кластеры с питанием: ${(data || []).length} шт.`
      );
    }
  };

  const handleToggleAllTransmit = async () => {
    const next = !allTransmit;
    setAllTransmit(next);
    addEvent(
      "act",
      next
        ? "Режим «передают все» включён"
        : "Режим «передают все» выключен"
    );
    try {
      await fetch(`${BASE}/stations/transmit_all?enabled=${next}`, {
        method: "POST",
      });
    } catch (err) {
      console.error("Ошибка переключения режима «передают все»:", err);
      addEvent("err", "Ошибка переключения режима «передают все»");
    }
  };

  // размещение нового устройства в точке клика по карте
  const handleAddDevice = useCallback(
    async (lat, lng) => {
      if (!addPhase || addBusy) return;
      setAddBusy(true);
      const typeLabel = addType === 1 ? "движущееся" : "стационарное";
      try {
        const res = await fetch(
          `${BASE}/stations/add?latitude=${lat}&longitude=${lng}&type_st=${addType}`,
          { method: "POST" }
        );
        const data = await res.json();
        if (!res.ok || data.status === "error") {
          setAddMsg(
            data.message || `Ошибка добавления устройства (код ${res.status})`
          );
          addEvent("err", "Ошибка добавления устройства");
          return;
        }
        // движущееся устройство привязали к дороге — сообщаем, насколько сдвинули
        const roadNote =
          typeof data.road_offset_m === "number"
            ? ` → дорога (сдвиг ${Math.round(data.road_offset_m)} м)`
            : "";
        const msg = `Добавлено устройство №${data.station_id}: ${data.latitude.toFixed(
          5
        )}, ${data.longitude.toFixed(5)} (${typeLabel})${roadNote}`;
        setAddMsg(msg);
        addEvent("act", msg);
        // состав изменился — таймзоны/кластеры на карте устарели
        setShowZones(false);
        setShowClusterHeads(false);
        setShowBatteryHeads(false);
        setZones([]);
        setClusters(null);
        clearHighlights();
      } catch (err) {
        console.error("Ошибка добавления устройства:", err);
        setAddMsg("Не удалось добавить устройство");
        addEvent("err", "Ошибка добавления устройства");
      } finally {
        setAddBusy(false);
      }
    },
    [addPhase, addBusy, addType, addEvent]
  );

  // удаление устройства по клику на кнопку в карточке станции
  const handleRemoveDevice = useCallback(
    async (id) => {
      try {
        const res = await fetch(`${BASE}/stations/${id}/remove`, {
          method: "POST",
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status === "error") {
          addEvent(
            "err",
            data.message || `Ошибка удаления устройства (код ${res.status})`
          );
          return false;
        }
        addEvent("act", `Удалено устройство №${id}`);
        // состав изменился — таймзоны/кластеры на карте устарели
        setShowZones(false);
        setShowClusterHeads(false);
        setShowBatteryHeads(false);
        setZones([]);
        setClusters(null);
        clearHighlights();
        return true;
      } catch (err) {
        console.error("Ошибка удаления устройства:", err);
        addEvent("err", "Ошибка удаления устройства");
        return false;
      }
    },
    [addEvent]
  );

  const handleToggleHeatmap = async () => {
    if (showHeatmap) {
      setShowHeatmap(false);
      setHeatmapImage(null);
      setHeatmapBounds(null);
      setInstallSites([]);
      addEvent("act", "Тепловая карта скрыта");
      return;
    }

    const liveStations = stations.filter(isLive);
    if (liveStations.length === 0) {
      addEvent("err", "Тепловую карту не удалось построить: нет активных станций");
      return;
    }

    const field = buildHeatmapField(liveStations, 36, NOVOSIBIRSK_ANALYSIS_BOUNDS);
    // маска воды считается на бэкенде (там лежит геометрия водоёма).
    // Если запрос не прошёл, подбор всё равно отсечёт край карты.
    let waterMask = null;
    try {
      const res = await fetch(`${BASE}/stations/water_mask?cells=36`);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.mask) && data.mask.length === 36 * 36) {
          waterMask = data.mask;
        }
      }
    } catch (err) {
      console.error("Не удалось получить маску воды:", err);
    }

    const sites = pickInstallSites(field, 3, waterMask);
    if (sites.length === 0) {
      addEvent(
        "err",
        "Подходящих мест вне воды и края карты не нашлось"
      );
    }
    setHeatmapImage(renderHeatmap(field));
    setHeatmapBounds([
      [NOVOSIBIRSK_ANALYSIS_BOUNDS.minLat, NOVOSIBIRSK_ANALYSIS_BOUNDS.minLon],
      [NOVOSIBIRSK_ANALYSIS_BOUNDS.maxLat, NOVOSIBIRSK_ANALYSIS_BOUNDS.maxLon],
    ]);
    setInstallSites(sites);
    setShowHeatmap(true);
    addEvent(
      "act",
      `Тепловая карта построена: рекомендовано точек установки — ${sites.length}`
    );
  };

  const handlePollutionsMin = async () => {
    setCountMsg("Убираю загрязнения…");
    try {
      const res = await fetch(`${BASE}/stations/clear_pollutions`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok || data.status === "error") {
        setCountMsg(data.message || "Ошибка удаления загрязнений");
        return;
      }
      setCountMsg(
        `Загрязнения удалены: ${data.stations_count} станций на фоновом уровне`
      );
      addEvent(
        "act",
        `Загрязнения сброшены к фону: ${data.stations_count} станций`
      );
    } catch (err) {
      console.error("Ошибка удаления загрязнений:", err);
      setCountMsg("Не удалось удалить загрязнения");
      addEvent("err", "Ошибка удаления загрязнений");
    }
  };

  const handleAddPollution = async () => {
    const id = parseInt(pollId, 10);
    if (!Number.isFinite(id) || id <= 0) {
      setPollMsg("Укажите корректный № станции");
      return;
    }
    const p25 = parseFloat(pollPM25);
    const p10 = parseFloat(pollPM10);
    if (Number.isNaN(p25) || Number.isNaN(p10)) {
      setPollMsg("Укажите числовые значения PM2.5 и PM10");
      return;
    }
    setPollMsg("Добавляю…");
    try {
      const res = await fetch(
        `${BASE}/stations/${id}/pollute?pm25=${encodeURIComponent(p25)}&pm10=${encodeURIComponent(p10)}`,
        { method: "POST" }
      );
      const data = await res.json();
      if (!res.ok || data.status === "error") {
        setPollMsg(data.message || "Ошибка добавления загрязнения");
        return;
      }
      setPollMsg(
        `Станция ${data.station_id}: PM2.5=${data.PM_2_5}, PM10=${data.PM_10} — ${data.ticks} тиков`
      );
      addEvent(
        "act",
        `Станция ${data.station_id}: внесено загрязнение PM2.5=${data.PM_2_5}, PM10=${data.PM_10} на ${data.ticks} тиков`
      );
    } catch (err) {
      console.error("Ошибка добавления загрязнения:", err);
      setPollMsg("Не удалось добавить загрязнение");
      addEvent("err", `Ошибка добавления загрязнения на станцию №${id}`);
    }
  };

  const handleClearPollution = async () => {
    const id = parseInt(pollId, 10);
    if (!Number.isFinite(id) || id <= 0) {
      setPollMsg("Укажите корректный № станции");
      return;
    }
    setPollMsg("Сбрасываю…");
    try {
      const res = await fetch(
        `${BASE}/stations/${id}/clear_pollution`,
        { method: "POST" }
      );
      const data = await res.json();
      if (!res.ok || data.status === "error") {
        setPollMsg(data.message || "Ошибка сброса загрязнения");
        return;
      }
      setPollMsg(
        `Станция ${data.station_id}: PM2.5=${data.PM_2_5}, PM10=${data.PM_10} — фоновый уровень`
      );
      addEvent("act", `Станция ${data.station_id}: сброс загрязнения к фону`);
    } catch (err) {
      console.error("Ошибка сброса загрязнения:", err);
      setPollMsg("Не удалось сбросить загрязнение");
      addEvent("err", `Ошибка сброса загрязнения на станции №${id}`);
    }
  };

  const handleSetBuffer = async () => {
    const kb = parseFloat(bufferCapacity);
    if (Number.isNaN(kb) || kb < 0) {
      setBufferMsg("Укажите ёмкость буфера от 0 до 1000000 КБ");
      return;
    }
    setBufferMsg("Применяю…");
    try {
      const res = await fetch(
        `${BASE}/stations/set_buffer?capacity_kb=${encodeURIComponent(kb)}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === "error") {
        const reason =
          data.message ||
          (Array.isArray(data.detail)
            ? data.detail[0]?.msg
            : data.detail) ||
          `код ${res.status}`;
        setBufferMsg(`Ошибка изменения ёмкости буфера: ${reason}`);
        return;
      }
      setBufferCapacity(data.buffer_capacity_kb);
      setBufferMsg(`Ёмкость буфера: ${data.buffer_capacity_kb} КБ за тик`);
      addEvent("act", `Объём буфера изменён: ${data.buffer_capacity_kb} КБ за тик`);
    } catch (err) {
      console.error("Ошибка изменения ёмкости буфера:", err);
      setBufferMsg("Не удалось изменить ёмкость буфера");
      addEvent("err", "Ошибка изменения ёмкости буфера");
    }
  };

  const handleSetBufferRate = async () => {
    const ratio = parseFloat(bufferRate);
    if (Number.isNaN(ratio) || ratio < 0) {
      setBufferRateMsg("Укажите скорость обработки буфера от 0 до 10");
      return;
    }
    setBufferRateMsg("Применяю…");
    try {
      const res = await fetch(
        `${BASE}/stations/set_buffer_rate?rate_ratio=${encodeURIComponent(ratio)}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === "error") {
        const reason =
          data.message ||
          (Array.isArray(data.detail)
            ? data.detail[0]?.msg
            : data.detail) ||
          `код ${res.status}`;
        setBufferRateMsg(`Ошибка изменения скорости обработки: ${reason}`);
        return;
      }
      setBufferRate(data.buffer_rate_ratio);
      setBufferRateMsg(
        `Скорость обработки буфера: ×${data.buffer_rate_ratio} от веса сообщений в секунду`
      );
      addEvent(
        "act",
        `Скорость обработки буфера: ×${data.buffer_rate_ratio} от веса сообщений/с`
      );
    } catch (err) {
      console.error("Ошибка изменения скорости обработки буфера:", err);
      setBufferRateMsg("Не удалось изменить скорость обработки буфера");
      addEvent("err", "Ошибка изменения скорости обработки буфера");
    }
  };

  const handleSetMsgWeights = async () => {
    const g = parseFloat(msgGlobal);
    const z = parseFloat(msgZone);
    if (Number.isNaN(g) || Number.isNaN(z) || g < 0 || z < 0) {
      setMsgWeightMsg("Укажите вес сообщения от 0 до 100 КБ");
      return;
    }
    setMsgWeightMsg("Применяю…");
    try {
      const res = await fetch(
        `${BASE}/stations/set_msg_weights?global_kb=${encodeURIComponent(
          g
        )}&zone_kb=${encodeURIComponent(z)}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === "error") {
        const reason =
          data.message ||
          (Array.isArray(data.detail)
            ? data.detail[0]?.msg
            : data.detail) ||
          `код ${res.status}`;
        setMsgWeightMsg(`Ошибка изменения весов сообщений: ${reason}`);
        return;
      }
      setMsgGlobal(String(data.global_msg_kb));
      setMsgZone(String(data.zone_msg_kb));
      setMsgWeightMsg(
        `Вес сообщения: сеть — ${data.global_msg_kb} КБ, зона — ${data.zone_msg_kb} КБ`
      );
      addEvent(
        "act",
        `Вес сообщения изменён: сеть — ${data.global_msg_kb} КБ, зона — ${data.zone_msg_kb} КБ`
      );
    } catch (err) {
      console.error("Ошибка изменения весов сообщений:", err);
      setMsgWeightMsg("Не удалось изменить веса сообщений");
      addEvent("err", "Ошибка изменения весов сообщений");
    }
  };

  const handleSetStationCount = async () => {
    const n = Math.max(1, Math.min(parseInt(stationCount, 10) || 100, 2000));
    const m = Math.max(0, Math.min(parseInt(movingCount, 10) || 0, n));
    setCountMsg("Обновляю…");
    try {
      const res = await fetch(
        `${BASE}/stations/set_count?count=${n}&moving_count=${m}`,
        { method: "POST" }
      );
      const data = await res.json();
      setStationCount(data.stations_count);
      setMovingCount(data.moving_count);
      setCountMsg(
        `Обновлено: ${data.stations_count} устройств, из них движущихся — ${data.moving_count}`
      );
      addEvent(
        "act",
        `Количество устройств изменено: ${data.stations_count} всего, ${data.moving_count} движущихся`
      );

      // сбросить все оверлеи (таймзоны/кластеры устарели)
      setShowZones(false);
      setShowClusterHeads(false);
      setShowBatteryHeads(false);
      setZones([]);
      setClusters(null);
      clearHighlights();
    } catch (err) {
      console.error("Ошибка смены количества устройств:", err);
      setCountMsg("Не удалось изменить количество устройств");
    }
  };

  const chartVisible =
    showZones ||
    showClusterHeads ||
    showBatteryHeads ||
    allTransmit;

  // ---- «Самые загрязнённые»: половина списка (8 → 4), ранжирование с учётом
  // PM_2_5 + PM_10 (и флага превышения TLV). Опрос станций идёт раз в секунду
  // (тик TICK_SECONDS на бэкенде), состав списка пересобирается каждые
  // 10 тиков, т.е. ~раз в 10 с ----
  const TOP_LIST_ROWS = 4; // половина от прежних 8
  const REFRESH_EVERY_TICKS = 10;

  const pollutionScore = (s) =>
    (s.overTLV ? 1e6 : 0) + (s["PM_2_5"] ?? 0) + (s["PM_10"] ?? 0);

  const [pollTick, setPollTick] = useState(0);
  const [topIds, setTopIds] = useState([]);

  useEffect(() => {
    setPollTick((t) => t + 1);
  }, [stations]);

  useEffect(() => {
    if (pollTick % REFRESH_EVERY_TICKS !== 0 && topIds.length > 0) return;
    const ranked = stations
      .filter(isLive)
      .sort(
        (a, b) =>
          pollutionScore(b) - pollutionScore(a) ||
          (b["PM_2_5"] ?? 0) - (a["PM_2_5"] ?? 0)
      )
      .slice(0, TOP_LIST_ROWS);
    setTopIds(ranked.map((s) => s.id));
  }, [pollTick, stations]);

  const topPolluted = topIds
    .map((id) => stations.find((s) => s.id === id))
    .filter((s) => s && isLive(s));

  // ---- всплывающие предупреждения о критическом превышении PM2.5 + PM10 ----
  const EXCEED_SUM_THRESHOLD = 200; // суммарно более 200
  const [alerts, setAlerts] = useState([]);
  const [dangerZones, setDangerZones] = useState([]);
  // узлы, по которым предупреждение уже показывалось: чтобы тост не
  // повторялся на каждом тике, пока сумма выше порога
  const alertedIdsRef = useRef(new Set());

  useEffect(() => {
    // временный кластер существует, пока сумма PM2.5 + PM10 выше порога:
    // как только уровень падает до порога или ниже, кластер исчезает,
    // а следующее превышение снова создаёт его
    const liveStations = stations.filter(isLive);
    const exceed = liveStations.filter(
      (s) =>
        (Number(s["PM_2_5"]) || 0) + (Number(s["PM_10"]) || 0) >
        EXCEED_SUM_THRESHOLD
    );
    const exceedIds = new Set(exceed.map((s) => s.id));

    // станции, опустившиеся ниже порога, снимаем с «блокировки» —
    // при новом превышении предупреждение снова сработает
    for (const id of alertedIdsRef.current) {
      if (!exceedIds.has(id)) alertedIdsRef.current.delete(id);
    }

    for (const s of exceed) {
      if (alertedIdsRef.current.has(s.id)) continue;
      alertedIdsRef.current.add(s.id);
      const pm25 = Number(s["PM_2_5"]) || 0;
      const pm10 = Number(s["PM_10"]) || 0;
      setAlerts((prev) => [
        ...prev.filter((a) => a.id !== s.id),
        {
          id: s.id,
          latitude: s.latitude,
          longitude: s.longitude,
          pm25,
          pm10,
        },
      ]);
      addEvent(
        "crit",
        `Критическое превышение на узле №${s.id}: PM2.5 + PM10 = ${(
          pm25 + pm10
        ).toFixed(1)} мкг/м³ (порог ${EXCEED_SUM_THRESHOLD})`
      );
      // автоскрытие через 10 секунд
      setTimeout(() => {
        setAlerts((prev) => prev.filter((a) => a.id !== s.id));
      }, 10000);
    }

    // временный кластер вокруг каждого узла с критическим превышением:
    // сам узел + DANGER_NEIGHBORS ближайших соседей, радиус покрывает
    // крайнего из них (но не больше DANGER_MAX_RADIUS_M)
    setDangerZones(
      exceed.map((s) => {
        const sum = (Number(s["PM_2_5"]) || 0) + (Number(s["PM_10"]) || 0);
        const nearest = liveStations
          .filter((o) => o.id !== s.id)
          .map((o) => ({
            id: o.id,
            latitude: o.latitude,
            longitude: o.longitude,
            distKm: haversineKm(s, o),
          }))
          .sort((a, b) => a.distKm - b.distKm)
          .slice(0, DANGER_NEIGHBORS);
        const members = [s.id, ...nearest.map((o) => o.id)];
        const farKm =
          nearest.length > 0 ? nearest[nearest.length - 1].distKm : 0;
        const radius = Math.min(
          Math.max(1200, (farKm + 0.4) * 1000),
          DANGER_MAX_RADIUS_M
        );
        return {
          id: s.id,
          latitude: s.latitude,
          longitude: s.longitude,
          sum,
          pm25: s["PM_2_5"],
          pm10: s["PM_10"],
          members,
          nearest,
          radius,
        };
      })
    );
  }, [stations, addEvent]);

  const typeName = (t) =>
    t === 0 ? "Стац." : t === 1 ? "Движ." : "Класт.";

  // ---- всплывающее сообщение о переполнении буфера очереди ----
  const BUFFER_ALERT_MS = 10000; // автоскрытие через 10 секунд
  const [bufferAlert, setBufferAlert] = useState(null);
  const bufferAlertIdRef = useRef(0);

  const handleBufferOverflow = (info) => {
    const id = ++bufferAlertIdRef.current;
    const dropped = Number(info.dropped) || 0;
    const ratio = Number(info.ratio) || 0;
    const capacity = Number(info.capacity) || 0;

    // рекомендация: сеть должна успевать за устройствами (скорость не ниже
    // ×1 от веса сообщений в секунду), а буфер — держать запас на всплеск
    const needCapacity = Math.max(Math.ceil(capacity + dropped * 3), 1);
    const advice =
      ratio < 1
        ? `Сеть не успевает за устройствами: увеличьте скорость обработки до ×1 от веса сообщений в секунду (сейчас ×${ratio})`
        : `Сеть успевает, но не хватает запаса: увеличьте объём буфера до ${needCapacity} КБ (сейчас ${capacity} КБ)`;

    setBufferAlert({ id, ...info, advice });
    addEvent(
      "warn",
      `Переполнение буфера очереди: потеряно ${dropped} КБ за тик, заполнение ${info.fill}% (${info.used}/${capacity} КБ)`
    );
    // скрываем только своё сообщение: если за это время пришло новое — не трогаем
    setTimeout(() => {
      setBufferAlert((prev) => (prev && prev.id === id ? null : prev));
    }, BUFFER_ALERT_MS);
  };

  return (
    <div className="app">
      <header>
        <span className="logo">🌿</span>
        <h1>НОВОСИБИРСК · МОНИТОРИНГ ВОЗДУХА</h1>
        <div id="status">
          <span className="pulse" />
          Станций: {stations.length} · в движении:{" "}
          {stations.filter((s) => s.type_st === 1).length}
        </div>
      </header>

      <main className="dash">
        <div className="main-col">
          <section className="card">
            <h2>Карта станций</h2>
            <div className="row">
              <button
                onClick={handleToggleClusterHeads}
                className={showClusterHeads ? "active" : ""}
              >
                {showClusterHeads
                  ? "Скрыть кластеры с хедами"
                  : "Показать кластеры с хедами"}
              </button>
              <button
                onClick={handleToggleBatteryHeads}
                className={showBatteryHeads ? "active" : ""}
              >
                {showBatteryHeads
                  ? "Скрыть кластеры с питанием"
                  : "Показать кластеры с питанием"}
              </button>
              <button
                onClick={handleToggleAllTransmit}
                className={allTransmit ? "active" : ""}
              >
                Передают все
              </button>
              <button
                onClick={handleToggleHeatmap}
                className={showHeatmap ? "active" : ""}
              >
                {showHeatmap ? "Скрыть тепловую карту" : "Тепловая карта"}
              </button>
            </div>

            <div id="map">
              <MapView
                stations={stations}
                zones={zones}
                showZones={showZones}
                clusters={clusters}
                showClusterHeads={showClusterHeads}
                showBatteryHeads={showBatteryHeads}
                highlightIds={highlightIds}
                dangerZones={dangerZones}
                heatmapImage={showHeatmap ? heatmapImage : null}
                heatmapBounds={showHeatmap ? heatmapBounds : null}
                installSites={showHeatmap ? installSites : []}
                weightPerSecond={weightPerSecond}
                onAddDevice={addPhase ? handleAddDevice : null}
                onRemoveDevice={handleRemoveDevice}
              />
            </div>

            <div className="legend">
              <div className="legend-group">PM2.5, мкг/м³</div>
              <span className="key">
                <span className="dot" style={{ background: "#22c55e" }} />
                &lt; 15 — норма
              </span>
              <span className="key">
                <span className="dot" style={{ background: "#eab308" }} />
                15–35 — умеренно
              </span>
              <span className="key">
                <span className="dot" style={{ background: "#f97316" }} />
                35–75 — высокое
              </span>
              <span className="key">
                <span className="dot" style={{ background: "#ef4444" }} />
                ≥ 75 — опасное
              </span>
              <div className="legend-group">PM10, мкг/м³</div>
              <span className="key">
                <span className="dot" style={{ background: "#22c55e" }} />
                &lt; 30 — норма
              </span>
              <span className="key">
                <span className="dot" style={{ background: "#eab308" }} />
                30–70 — умеренно
              </span>
              <span className="key">
                <span className="dot" style={{ background: "#f97316" }} />
                70–150 — высокое
              </span>
              <span className="key">
                <span className="dot" style={{ background: "#ef4444" }} />
                ≥ 150 — опасное
              </span>
              <div className="legend-note">
                Цвет маркера — по максимальному из PM2.5 / PM10
              </div>
              {showHeatmap && (
                <div className="heatmap-legend">
                  <span>
                    <span className="heatmap-gradient" />
                    Сумма PM2.5 + PM10: меньше → больше
                  </span>
                  <span className="heatmap-contour-key" />
                  Контуры плотности устройств
                </div>
              )}
            </div>
          </section>

          {showHeatmap && (
            <section className="card install-card">
              <h2>Рекомендованная точка установки</h2>
              <p className="dim-note">
                Точки выбраны там, где высокая расчётная загрязнённость
                сочетается с большим расстоянием до ближайшей станции.
                Места в воде и на самом краю карты не предлагаются.
              </p>
              {installSites.length > 0 ? (
                <table className="install-table">
                  <thead>
                    <tr>
                      <th>№</th>
                      <th>Координаты</th>
                      <th>Загрязнение</th>
                      <th>До станции</th>
                    </tr>
                  </thead>
                  <tbody>
                    {installSites.map((site) => (
                      <tr key={site.number}>
                        <td>{site.number}</td>
                        <td>
                          {site.latitude.toFixed(5)}, {site.longitude.toFixed(5)}
                        </td>
                        <td>{site.mass.toFixed(1)} мкг/м³</td>
                        <td>{site.gapKm.toFixed(1)} км</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="dim-note">Подходящих точек не найдено.</p>
              )}
            </section>
          )}

          <EventLog events={events} onClear={clearEvents} />
        </div>

        <div className="side">
          <section className="card">
            <h2
              className="card-toggle"
              onClick={() => setControlOpen(!controlOpen)}
            >
              Управление
              <span className={`chev${controlOpen ? " open" : ""}`}>▸</span>
            </h2>
            {controlOpen && (
              <>
                <div className="row">
                  <span className="mode-label">Устройств:</span>
                  <input
                    ref={stationCountRef}
                    type="number"
                    className="num-input"
                    min="1"
                    max="2000"
                    value={stationCount}
                    onChange={(e) => setStationCount(e.target.value)}
                  />
                  <button onClick={handleSetStationCount}>
                    Изменить количество
                  </button>
                </div>
                <div className="row">
                  <span className="mode-label">Движущихся:</span>
                  <input
                    type="number"
                    className="num-input"
                    min="0"
                    max="2000"
                    value={movingCount}
                    onChange={(e) => setMovingCount(e.target.value)}
                  />
                  <span className="dim-note">
                    остальные — стационарные
                  </span>
                </div>
                <div className="row">
                  <span className="dim-note">
                    движущиеся создаются на дорогах и едут по маршруту
                    длиной около 6 км (туда-обратно)
                  </span>
                </div>
                {countMsg && <div className="count-msg">{countMsg}</div>}
                <div className="row">
                  <button
                    onClick={() => {
                      setAddPhase((p) => !p);
                      setAddMsg("");
                    }}
                    className={addPhase ? "active" : ""}
                  >
                    {addPhase
                      ? "Отменить добавление"
                      : "Добавить устройство на карту"}
                  </button>
                  <span className="dim-note">
                    {addPhase
                      ? "→ кликните по карте"
                      : "размещение по клику на карте"}
                  </span>
                </div>
                {addPhase && (
                  <div className="row">
                    <span className="mode-label">Тип устройства:</span>
                    <button
                      className={addType === 0 ? "active" : ""}
                      onClick={() => setAddType(0)}
                    >
                      Стационарное
                    </button>
                    <button
                      className={addType === 1 ? "active" : ""}
                      onClick={() => setAddType(1)}
                    >
                      Движущееся
                    </button>
                  </div>
                )}
                {addMsg && <div className="count-msg">{addMsg}</div>}
                <div className="row">
                  <span className="mode-label">Объём буфера, КБ:</span>
                  <input
                    type="number"
                    className="num-input"
                    min="0"
                    max="1000000"
                    step="0.1"
                    value={bufferCapacity}
                    onChange={(e) => setBufferCapacity(e.target.value)}
                  />
                  <button onClick={handleSetBuffer}>Изменить объём</button>
                  <span className="dim-note">
                    сверх ёмкости сообщения теряются
                  </span>
                </div>
                {bufferMsg && <div className="count-msg">{bufferMsg}</div>}
                <div className="row">
                  <span className="mode-label">
                    Скорость обработки буфера, × к весу сообщений/с:
                  </span>
                  <input
                    type="number"
                    className="num-input"
                    min="0"
                    max="10"
                    step="0.05"
                    value={bufferRate}
                    onChange={(e) => setBufferRate(e.target.value)}
                  />
                  <button onClick={handleSetBufferRate}>
                    Изменить скорость
                  </button>
                  <span className="dim-note">
                    1 — ровно успевает, меньше — буфер копится
                  </span>
                </div>
                {bufferRateMsg && (
                  <div className="count-msg">{bufferRateMsg}</div>
                )}
                <div className="row">
                  <span className="mode-label">
                    Вес сообщения (сеть), КБ:
                  </span>
                  <input
                    type="number"
                    className="num-input"
                    min="0"
                    max="100"
                    step="0.05"
                    value={msgGlobal}
                    onChange={(e) => setMsgGlobal(e.target.value)}
                  />
                </div>
                <div className="row">
                  <span className="mode-label">
                    Вес сообщения (зона), КБ:
                  </span>
                  <input
                    type="number"
                    className="num-input"
                    min="0"
                    max="100"
                    step="0.05"
                    value={msgZone}
                    onChange={(e) => setMsgZone(e.target.value)}
                  />
                  <button onClick={handleSetMsgWeights}>Изменить веса</button>
                  <span className="dim-note">
                    × коэффициент загрязнения
                  </span>
                </div>
                {msgWeightMsg && (
                  <div className="count-msg">{msgWeightMsg}</div>
                )}
                <div className="row">
                  <button onClick={handlePollutionsMin}>Сброс значений</button>
                </div>

                <div className="row">
                  <button onClick={() => setPollFormOpen(!pollFormOpen)}>
                    {pollFormOpen ? "Скрыть поля" : "Добавить загрязнение"}
                  </button>
                </div>
                {pollFormOpen && (
                  <>
                    <h3 className="table-title">Загрязнение станции</h3>
                    <div className="row">
                      <span className="mode-label">Станция №</span>
                      <input
                        type="number"
                        className="num-input"
                        min="1"
                        value={pollId}
                        onChange={(e) => setPollId(e.target.value)}
                      />
                    </div>
                    <div className="row">
                      <span className="mode-label">PM2.5</span>
                      <input
                        type="number"
                        className="num-input"
                        min="0"
                        max="500"
                        step="any"
                        value={pollPM25}
                        onChange={(e) => setPollPM25(e.target.value)}
                      />
                      <span className="mode-label">PM10</span>
                      <input
                        type="number"
                        className="num-input"
                        min="0"
                        max="500"
                        step="any"
                        value={pollPM10}
                        onChange={(e) => setPollPM10(e.target.value)}
                      />
                    </div>
                    <div className="row">
                      <button onClick={handleAddPollution}>Внести загрязнение</button>
                      <button onClick={handleClearPollution}>Сбросить до фона</button>
                    </div>
                </>
              )}
                {pollMsg && <div className="count-msg">{pollMsg}</div>}
              </>
            )}
          </section>

          <section className="card">
            <h2>Сетевая нагрузка</h2>
            <MetricsChartOverlay
              visible={chartVisible}
              onBufferOverflow={handleBufferOverflow}
              onWeightUpdate={setWeightPerSecond}
            />
          </section>

          <section className="card">
            <h2>Сколько нужно ресурсов</h2>
            <LoadScenarios />
          </section>

          <section className="card">
            <h2>
              Самые загрязнённые · топ-{TOP_LIST_ROWS}
            </h2>
            <table>
              <thead>
                <tr>
                  <th>№</th>
                  <th>Тип</th>
                  <th>Лат</th>
                  <th>Лон</th>
                  <th>PM2.5</th>
                  <th>PM10</th>
                  <th>TLV</th>
                </tr>
              </thead>
              <tbody>
                {topPolluted.map((s) => (
                  <tr
                    key={s.id}
                    className={`clickable${
                      highlightIds.includes(s.id) ? " highlighted" : ""
                    }`}
                    onClick={() => toggleHighlight([s.id])}
                  >
                    <td>{s.id}</td>
                    <td>{typeName(s.type_st)}</td>
                    <td>{s.latitude.toFixed(3)}</td>
                    <td>{s.longitude.toFixed(3)}</td>
                    <td>{s["PM_2_5"]}</td>
                    <td>{s["PM_10"]}</td>
                    <td>{s.overTLV ? "⚠" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="card">
            <h2>Состав зон и кластеров</h2>

            {highlightIds.length > 0 && (
              <div className="row highlight-bar">
                <span className="dim-note">
                  Подсвечено устройств: {highlightIds.length}
                </span>
                <button onClick={() => setHighlightIds([])}>Сбросить</button>
              </div>
            )}

            {showZones && zones.length > 0 && (
              <>
                <h3 className="table-title">Тайм-зоны</h3>
                <table>
                  <thead>
                    <tr>
                      <th>Зона</th>
                      <th>Устройств</th>
                      <th>Хэд зоны</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {zones.map((z, i) => {
                      const key = `z:${z.zone_id ?? i}`;
                      const members = filterLive(z.stations);
                      const head = z.head ?? null;
                      return (
                        <Fragment key={key}>
                          <tr
                            className="clickable"
                            onClick={() => {
                              toggleExpanded(key);
                              toggleHighlight(members);
                            }}
                          >
                            <td>{i}</td>
                            <td>{members.length}</td>
                            <td>
                              {head !== null && members.includes(head) ? (
                                <span className="chip chip-head">★#{head}</span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="toggle-sym">
                              {expanded[key] ? "▼" : "▶"}
                            </td>
                          </tr>
                          {expanded[key] && (
                            <tr>
                              <td colSpan="4" className="member-list">
                                {members.length === 0
                                  ? "—"
                                  : members.map((id) => (
                                      <span
                                        key={id}
                                        className={
                                          id === head ? "chip chip-head" : "chip"
                                        }
                                      >
                                        {id === head ? `★#${id}` : `#${id}`}
                                      </span>
                                    ))}
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}

            {(showClusterHeads ||
              showBatteryHeads) &&
              clusters?.members?.length > 0 && (
                <>
                  <h3 className="table-title">Кластеры</h3>
                  <table>
                  <thead>
                    <tr>
                      <th>Кластер</th>
                      <th>Устройств</th>
                      <th>Хэд кластера</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {clusters.members.map((m) => {
                      const key = `c:${m.cluster_id}`;
                      const members = filterLive(m.stations);
                      const head = m.head ?? null;
                      return (
                        <Fragment key={key}>
                          <tr
                            className="clickable"
                            onClick={() => {
                              toggleExpanded(key);
                              toggleHighlight(members);
                            }}
                          >
                            <td>{m.cluster_id}</td>
                            <td>{members.length}</td>
                            <td>
                              {head !== null && members.includes(head) ? (
                                <span className="chip chip-head">★#{head}</span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="toggle-sym">
                              {expanded[key] ? "▼" : "▶"}
                            </td>
                          </tr>
                          {expanded[key] && (
                            <tr>
                              <td colSpan="4" className="member-list">
                                {members.length === 0
                                  ? "—"
                                  : members.map((id) => (
                                      <span
                                        key={id}
                                        className={
                                          id === head ? "chip chip-head" : "chip"
                                        }
                                      >
                                        {id === head ? `★#${id}` : `#${id}`}
                                      </span>
                                    ))}
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>

                  </table>
                </>
              )}

            {dangerZones.length > 0 && (
              <>
                <h3 className="table-title">
                  Временные кластеры (критическое превышение PM2.5 + PM10)
                </h3>
                <table>
                  <thead>
                    <tr>
                      <th>Узел</th>
                      <th>Состав (узел + 3 ближ.)</th>
                      <th>Координаты</th>
                      <th>PM2.5</th>
                      <th>PM10</th>
                      <th>Сумма</th>
                      <th>Радиус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dangerZones.map((dz) => (
                      <tr
                        key={dz.id}
                        className="clickable danger-row"
                        onClick={() => toggleHighlight(dz.members)}
                      >
                        <td>№{dz.id}</td>
                        <td className="member-list">
                          {dz.members.map((id) => (
                            <span key={id} className="chip">
                              #{id}
                            </span>
                          ))}
                        </td>
                        <td>
                          {dz.latitude.toFixed(5)}, {dz.longitude.toFixed(5)}
                        </td>
                        <td>{dz.pm25}</td>
                        <td>{dz.pm10}</td>
                        <td>{dz.sum}</td>
                        <td>{(dz.radius / 1000).toFixed(1)} км</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}

            {dangerZones.length === 0 &&
              !showZones &&
              !showClusterHeads &&
              !showBatteryHeads && (
                <p className="dim-note">
                  Включите «Показать таймзоны» или один из режимов кластеров,
                  чтобы увидеть состав зон и кластеров.
                </p>
              )}
          </section>
        </div>
      </main>

      {/* всплывающие предупреждения о критическом превышении PM2.5 + PM10 */}
      {(alerts.length > 0 || bufferAlert) && (
        <div className="toast-stack">
          {alerts.map((a) => (
            <div key={a.id} className="toast">
              <button
                className="toast-close"
                onClick={() =>
                  setAlerts((prev) => prev.filter((x) => x.id !== a.id))
                }
              >
                ✕
              </button>
              <div className="toast-title">
                ⚠ Критическое превышение PM2.5 + PM10
              </div>
              <div>
                Узел №{a.id} · координаты: {a.latitude.toFixed(5)},{" "}
                {a.longitude.toFixed(5)}
              </div>
              <div className="toast-detail">
                PM2.5 = {a.pm25}, PM10 = {a.pm10} (сумма ={" "}
                {(Number(a.pm25) || 0) + (Number(a.pm10) || 0)})
              </div>
            </div>
          ))}
          {bufferAlert && (
            <div className="toast toast-buffer">
              <button
                className="toast-close"
                onClick={() => setBufferAlert(null)}
              >
                ✕
              </button>
              <div className="toast-title">⚠ Переполнение буфера очереди</div>
              <div className="toast-detail">
                Потеряно за тик: {bufferAlert.dropped} КБ · ушло в сеть:{" "}
                {bufferAlert.weightPerSecond} КБ/с
              </div>
              <div className="toast-detail">
                Буфер: {bufferAlert.used} / {bufferAlert.capacity} КБ (
                {bufferAlert.fill}%)
              </div>
              <div className="toast-detail toast-advice">
                Рекомендация: {bufferAlert.advice}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
