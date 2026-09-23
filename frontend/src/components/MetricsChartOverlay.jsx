import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

export default function MetricsChartOverlay({ visible }) {
  const [data, setData] = useState([]);
  const [totalMessages, setTotalMessages] = useState(0);
  const [totalWeight, setTotalWeight] = useState(0);

  useEffect(() => {
    if (!visible) return;

    const ws = new WebSocket("ws://127.0.0.1:8000/ws/metrics");

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      setTotalMessages(msg.total_messages ?? 0);
      setTotalWeight(msg.total_message_weight ?? 0);
      setData((prev) => [
        ...prev.slice(-100), // ~2 минуты (40 * 3 сек)
        {
          time: new Date(msg.timestamp).toLocaleTimeString(),
          value: msg.messages_per_second,
          total: msg.stations_count,
        },
      ]);
    };

    return () => ws.close();
  }, [visible]);

  // вторая (жёлтая) линия — все устройства, которые сейчас не передают:
  // общее количество устройств минус текущее значение
  const chartData = data.map((d) => ({
    ...d,
    free: (d.total ?? 0) - d.value,
  }));

  return (
    <div>
      {visible && data.length > 0 ? (
        <>
          <div className="total-row">
            Всего передано сообщений:{" "}
            <b>{totalMessages.toLocaleString("ru-RU")}</b>
          </div>
          <div className="total-row">
            Общий вес сообщений:{" "}
            <b>{totalWeight.toFixed(1)}</b>
          </div>
          <div
            style={{
              fontSize: 12,
              color: "var(--dim)",
              margin: "0 0 6px 4px",
            }}
          >
            <span style={{ marginRight: 14 }}>
              <i
                className="legend-dot"
                style={{ background: "#38bdf8" }}
              />{" "}
              Передают в глобальную сеть
            </span>
            <span>
              <i
                className="legend-dot"
                style={{ background: "#facc15" }}
              />{" "}
              Передают в пределах зоны
            </span>
          </div>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis
                  dataKey="time"
                  stroke="#94a3b8"
                  fontSize={11}
                  tickLine={false}
                />
                <YAxis
                  stroke="#94a3b8"
                  fontSize={11}
                  tickLine={false}
                  width={40}
                />
                <Tooltip
                  contentStyle={{
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 8,
                    color: "#e2e8f0",
                  }}
                  labelStyle={{ color: "#94a3b8" }}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={false}
                  name="Передают в глобальную сеть"
                />
                <Line
                  type="monotone"
                  dataKey="free"
                  stroke="#facc15"
                  strokeWidth={2}
                  dot={false}
                  name="Передают в пределах зоны"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      ) : (
        <p className="dim-note">
          Метрики появятся при включении таймзон или кластеров на карте.
        </p>
      )}
    </div>
  );
}