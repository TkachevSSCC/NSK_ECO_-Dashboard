import React, { useState, Fragment } from "react";
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

  const [zones, setZones] = useState([]);
  const [showZones, setShowZones] = useState(false);

  const [showClusters, setShowClusters] = useState(false);
  const [showClusterHeads, setShowClusterHeads] = useState(false);
  const [showBatteryHeads, setShowBatteryHeads] = useState(false);

  const [clusters, setClusters] = useState(null);

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
      const response = await fetch(`${BASE}/stations/plot/timezone`);
      const data = await response.json();
      setZones(data.zones || []);
      setShowZones(true);
    }
  };

  const handlePollutionsAdd = async () => {
    await fetch(`${BASE}/stations/fake_pollutions`, { method: "POST" });
  };
  const handlePollutionsMin = async () => {
    await fetch(`${BASE}/stations/clear_fake_pollutions`, { method: "POST" });
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

  const topPolluted = [...stations]
    .sort((a, b) => (b["PM_2_5"] ?? -1) - (a["PM_2_5"] ?? -1))
    .slice(0, 8);

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
                ? "Скрыть кластеры с батареями"
                : "Показать кластеры с батареями"}
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
            <span className="key">
              <span className="dot" style={{ background: "#22c55e" }} />
              PM2.5 &lt; 15 — норма
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
              <button onClick={handlePollutionsAdd}>Добавить загрязнения</button>
              <button onClick={handlePollutionsMin}>Убрать загрязнения</button>
            </div>
          </section>

          <section className="card">
            <h2>Сетевая нагрузка</h2>
            <MetricsChartOverlay visible={chartVisible} />
          </section>

          <section className="card">
            <h2>Самые загрязнённые</h2>
            <table>
              <thead>
                <tr>
                  <th>№</th>
                  <th>Тип</th>
                  <th>Лат</th>
                  <th>Лон</th>
                  <th>PM2.5</th>
                  <th>PM10</th>
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