import React, { useState } from "react";
import "./App.css";
import "leaflet/dist/leaflet.css";
import MapView from "./components/MapView";
import Modal from "./components/Modal";
import MetricsChartOverlay from "./components/MetricsChartOverlay";
import { useStations } from "./hooks/useStations";

const BASE = "http://127.0.0.1:8000";

export default function App() {
  const stations = useStations();

  const [option, setOption] = useState("1");
  const [plotUrl, setPlotUrl] = useState(null);
  const [showModal, setShowModal] = useState(false);

  const [stationCount, setStationCount] = useState(100);
  const [movingCount, setMovingCount] = useState(100);
  const [countMsg, setCountMsg] = useState("");

  const [zones, setZones] = useState([]);
  const [showZones, setShowZones] = useState(false);

  const [showClusters, setShowClusters] = useState(false);
  const [showClusterHeads, setShowClusterHeads] = useState(false);
  const [showBatteryHeads, setShowBatteryHeads] = useState(false);

  const [clusters, setClusters] = useState(null);

  const resetOverlays = async () => {
    const res = await fetch(`${BASE}/stations/reset`, { method: "POST" });
    return res.json();
  };

  const handleToggleClusters = async () => {
    if (showClusters) {
      setShowClusters(false);
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
      await resetOverlays();
    } else if (showBatteryHeads) {
      setShowBatteryHeads(false);
      await resetOverlays();
    } else if (showClusters) {
      setShowClusters(false);
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
      await resetOverlays();
    } else if (showClusterHeads) {
      setShowClusterHeads(false);
      await resetOverlays();
    } else if (showClusters) {
      setShowClusters(false);
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
    } catch (err) {
      console.error("Ошибка смены количества устройств:", err);
      setCountMsg("Не удалось изменить количество устройств");
    }
  };

  const handleGeneratePlot = async () => {
    try {
      const response = await fetch(`${BASE}/stations/plot/${option}`, {
        headers: { Accept: "image/png" },
      });

      if (!response.ok) {
        throw new Error(`Ошибка запроса: ${response.status}`);
      }

      const blob = await response.blob();
      const imgUrl = URL.createObjectURL(blob);
      setPlotUrl(imgUrl);
      setShowModal(true);
    } catch (err) {
      console.error("Ошибка загрузки графика:", err);
      alert("Не удалось загрузить расписание. Проверь сервер или адрес API.");
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
            <div className="row mode-row">
              <span className="mode-label">Расписание:</span>
              <select
                id="plot-select"
                value={option}
                onChange={(e) => setOption(e.target.value)}
              >
                <option value="1">Time Zone</option>
                <option value="2">Cluster</option>
                <option value="3">Cluster with cluster heads</option>
                <option value="4">Cluster with battery life based cluster heads</option>
              </select>
              <button onClick={handleGeneratePlot}>Построить</button>
            </div>
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
            <h2>Метрики системы</h2>
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
        </div>
      </main>

      {showModal && (
        <Modal
          plotUrl={plotUrl}
          onClose={() => {
            setShowModal(false);
            setPlotUrl(null);
          }}
        />
      )}
    </div>
  );
}