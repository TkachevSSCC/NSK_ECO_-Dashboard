import React, { useState, Fragment, useEffect } from "react";
import "./App.css";
import "leaflet/dist/leaflet.css";
import MapView from "./components/MapView";
import MetricsChartOverlay from "./components/MetricsChartOverlay";
import { useStations } from "./hooks/useStations";

const BASE = "http://127.0.0.1:8000";

export default function App() {
  const stations = useStations();

  const [stationCount, setStationCount] = useState(100);
  const [movingCount, setMovingCount] = useState(100);
  const [countMsg, setCountMsg] = useState("");

  // точечное загрязнение станции
  const [pollId, setPollId] = useState(1);
  const [pollPM25, setPollPM25] = useState("40");
  const [pollPM10, setPollPM10] = useState("70");
  const [pollMsg, setPollMsg] = useState("");
  const [pollFormOpen, setPollFormOpen] = useState(false);

  const [zones, setZones] = useState([]);
  const [showZones, setShowZones] = useState(false);

  const [showClusters, setShowClusters] = useState(false);
  const [showClusterHeads, setShowClusterHeads] = useState(false);
  const [showBatteryHeads, setShowBatteryHeads] = useState(false);

  const [clusters, setClusters] = useState(null);
  // «передают все»: все станции передают в глобальную сеть (mode="clusters")
  const [allTransmit, setAllTransmit] = useState(false);

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

  const handleToggleClusters = async () => {
    if (showClusters) {
      setShowClusters(false);
      clearHighlights();
      await resetOverlays();
    } else {
      setShowClusterHeads(false);
      setShowBatteryHeads(false);
      // кластеры и таймзоны одновременно не показываем
      setShowZones(false);
      setZones([]);
      setAllTransmit(false);
      const response = await fetch(`${BASE}/stations/plot/cluster`);
      const data = await response.json();
      setClusters(data);
      setShowClusters(true);
    }
  };

  const handleToggleClusterHeads = async () => {
    if (showClusterHeads) {
      setShowClusterHeads(false);
      clearHighlights();
      await resetOverlays();
    } else if (showBatteryHeads) {
      setShowBatteryHeads(false);
      clearHighlights();
      await resetOverlays();
    } else if (showClusters) {
      setShowClusters(false);
      clearHighlights();
      await resetOverlays();
    } else {
      setShowZones(false);
      setZones([]);
      setAllTransmit(false);
      const response = await fetch(`${BASE}/stations/plot/cluster?mode=head`);
      const data = await response.json();
      setClusters(data);
      setShowClusterHeads(true);
    }
  };

  const handleToggleBatteryHeads = async () => {
    if (showBatteryHeads) {
      setShowBatteryHeads(false);
      clearHighlights();
      await resetOverlays();
    } else if (showClusterHeads) {
      setShowClusterHeads(false);
      clearHighlights();
      await resetOverlays();
    } else if (showClusters) {
      setShowClusters(false);
      clearHighlights();
      await resetOverlays();
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
    }
  };

  const handleToggleZones = async () => {
    if (showZones) {
      setShowZones(false);
      clearHighlights();
    } else {
      // таймзоны и кластеры одновременно не показываем
      setShowClusters(false);
      setShowClusterHeads(false);
      setShowBatteryHeads(false);
      setClusters(null);
      clearHighlights();
      setAllTransmit(false);
      await resetOverlays();
      const response = await fetch(`${BASE}/stations/plot/timezone`);
      const data = await response.json();
      setZones(data.zones || []);
      setShowZones(true);
    }
  };

  const handleToggleAllTransmit = async () => {
    const next = !allTransmit;
    setAllTransmit(next);
    try {
      await fetch(`${BASE}/stations/transmit_all?enabled=${next}`, {
        method: "POST",
      });
    } catch (err) {
      console.error("Ошибка переключения режима «передают все»:", err);
    }
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
    } catch (err) {
      console.error("Ошибка удаления загрязнений:", err);
      setCountMsg("Не удалось удалить загрязнения");
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
    } catch (err) {
      console.error("Ошибка добавления загрязнения:", err);
      setPollMsg("Не удалось добавить загрязнение");
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
    } catch (err) {
      console.error("Ошибка сброса загрязнения:", err);
      setPollMsg("Не удалось сбросить загрязнение");
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

      // сбросить все оверлеи (таймзоны/кластеры устарели)
      setShowZones(false);
      setShowClusters(false);
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
    showZones || showClusters || showClusterHeads || showBatteryHeads;

  // ---- «Самые загрязнённые»: половина списка (8 → 4), ранжирование с учётом
  // PM_2_5 + PM_10 (и флага превышения TLV), состав обновляется каждые 5 тиков
  // (тик = опрос станций раз в 2 с, т.е. ~раз в 10 с) ----
  const TOP_LIST_ROWS = 4; // половина от прежних 8
  const REFRESH_EVERY_TICKS = 5;

  const pollutionScore = (s) =>
    (s.overTLV ? 1e6 : 0) + (s["PM_2_5"] ?? 0) + (s["PM_10"] ?? 0);

  const [pollTick, setPollTick] = useState(0);
  const [topIds, setTopIds] = useState([]);

  useEffect(() => {
    setPollTick((t) => t + 1);
  }, [stations]);

  useEffect(() => {
    if (pollTick % REFRESH_EVERY_TICKS !== 0 && topIds.length > 0) return;
    const ranked = [...stations]
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
    .filter(Boolean);

  const typeName = (t) =>
    t === 0 ? "Стац." : t === 1 ? "Движ." : "Класт.";

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
        <section className="card">
          <h2>Карта станций</h2>
          <div className="row">
            <button
              onClick={handleToggleZones}
              className={showZones ? "active" : ""}
            >
              {showZones ? "Скрыть таймзоны" : "Показать таймзоны"}
            </button>
            <button
              onClick={handleToggleClusters}
              className={showClusters ? "active" : ""}
            >
              {showClusters ? "Скрыть кластеры" : "Показать кластеры"}
            </button>
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
          </div>

          <div id="map">
            <MapView
              stations={stations}
              zones={zones}
              showZones={showZones}
              clusters={clusters}
              showClusters={showClusters}
              showClusterHeads={showClusterHeads}
              showBatteryHeads={showBatteryHeads}
              highlightIds={highlightIds}
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
          </div>
        </section>

        <div className="side">
          <section className="card">
            <h2>Управление</h2>
            <div className="row">
              <span className="mode-label">Устройств:</span>
              <input
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
            {countMsg && <div className="count-msg">{countMsg}</div>}
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
          </section>

          <section className="card">
            <h2>Сетевая нагрузка</h2>
            <MetricsChartOverlay visible={chartVisible} />
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
                  <tr key={s.id}>
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
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {zones.map((z, i) => {
                      const key = `z:${z.zone_id ?? i}`;
                      return (
                        <Fragment key={key}>
                          <tr
                            className="clickable"
                            onClick={() => {
                              toggleExpanded(key);
                              toggleHighlight(z.stations || []);
                            }}
                          >
                            <td>{i}</td>
                            <td>{z.count ?? (z.stations || []).length}</td>
                            <td className="toggle-sym">
                              {expanded[key] ? "▼" : "▶"}
                            </td>
                          </tr>
                          {expanded[key] && (
                            <tr>
                              <td colSpan="3" className="member-list">
                                {(z.stations || []).length === 0
                                  ? "—"
                                  : (z.stations || []).map((id) => (
                                      <span key={id} className="chip">
                                        #{id}
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

            {(showClusters ||
              showClusterHeads ||
              showBatteryHeads) &&
              clusters?.members?.length > 0 && (
                <>
                  <h3 className="table-title">Кластеры</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Кластер</th>
                        <th>Устройств</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {clusters.members.map((m) => {
                        const key = `c:${m.cluster_id}`;
                        return (
                          <Fragment key={key}>
                            <tr
                              className="clickable"
                              onClick={() => {
                                toggleExpanded(key);
                                toggleHighlight(m.stations || []);
                              }}
                            >
                              <td>{m.cluster_id}</td>
                              <td>{m.count ?? (m.stations || []).length}</td>
                              <td className="toggle-sym">
                                {expanded[key] ? "▼" : "▶"}
                              </td>
                            </tr>
                            {expanded[key] && (
                              <tr>
                                <td colSpan="3" className="member-list">
                                  {(m.stations || []).length === 0
                                    ? "—"
                                    : (m.stations || []).map((id) => (
                                        <span key={id} className="chip">
                                          #{id}
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

            {!showZones &&
              !showClusters &&
              !showClusterHeads &&
              !showBatteryHeads && (
                <p className="dim-note">
                  Включите «Показать таймзоны» или «Показать кластеры»,
                  чтобы увидеть состав зон и кластеров.
                </p>
              )}
          </section>
        </div>
      </main>
    </div>
  );
}