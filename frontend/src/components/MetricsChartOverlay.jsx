import { useEffect, useRef, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

export default function MetricsChartOverlay({
  visible,
  onBufferOverflow,
  onWeightUpdate,
}) {
  const [data, setData] = useState([]);
  const [totalMessages, setTotalMessages] = useState(0);
  const [totalWeight, setTotalWeight] = useState(0);
  const [weightPerSecond, setWeightPerSecond] = useState(0);
  // средний заряд движущихся устройств; null — движущихся нет, не считаем
  const [batteryAvg, setBatteryAvg] = useState(null);
  const [buffer, setBuffer] = useState(null);

  // база отсчёта при обновлении страницы (сброс на фронте,
  // без влияния на другие вкладки): фиксируем серверные
  // накопительные значения в момент загрузки и вычитаем их
  const baselineRef = useRef(null);
  // база накопленных потерь: снимается при первом кадре, чтобы
  // показывать только потери текущей сессии
  const baseDroppedRef = useRef(null);
  // показываем предупреждение о переполнении один раз, пока буфер
  // переполнен: после автоскрытия новый тик с переполнением покажет его снова
  const overflowShownRef = useRef(false);
  // обработчик переполнения храним в ref, чтобы не переподключать
  // WebSocket при каждом рендере родителя
  const onOverflowRef = useRef(onBufferOverflow);

  useEffect(() => {
    onOverflowRef.current = onBufferOverflow;
  });

  useEffect(() => {
    if (!visible) return;

    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss://" : "ws://"}${location.host}/ws/metrics`
    );

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);

      // первый кадр после обновления страницы — точка отсчёта.
      // Все последующие счётчики показываем относительно неё,
      // т.е. при каждом обновлении страницы обнуляются
      // «всего передано сообщений» и «общий вес сообщений».
      if (baselineRef.current === null) {
        baselineRef.current = {
          messages: msg.total_messages ?? 0,
          weight: msg.total_message_weight_kb ?? 0,
        };
      }

      const base = baselineRef.current;
      setTotalMessages(Math.max(0, (msg.total_messages ?? 0) - base.messages));
      setTotalWeight(
        Math.max(0, (msg.total_message_weight_kb ?? 0) - base.weight)
      );
      setWeightPerSecond(msg.weight_per_second_kb ?? 0);
      onWeightUpdate?.(msg.weight_per_second_kb ?? 0);

      // заполнение буфера и потери при переполнении
      const totalDropped = msg.total_dropped_weight_kb ?? 0;
      if (baseDroppedRef.current === null) baseDroppedRef.current = totalDropped;
      setBuffer({
        capacity: msg.buffer_capacity_kb ?? 0,
        ratio: msg.buffer_rate_ratio ?? 1,
        processing: msg.buffer_processing_kb_s ?? 0,
        used: msg.buffer_used_kb ?? 0,
        fill: msg.buffer_fill_percent ?? 0,
        dropped: Math.max(0, totalDropped - baseDroppedRef.current),
        overflow: !!msg.buffer_overflow,
      });

      // переполнение буфера: сообщаем один раз, пока буфер переполнен
      if (msg.buffer_overflow) {
        if (!overflowShownRef.current) {
          overflowShownRef.current = true;
          onOverflowRef.current?.({
            dropped: msg.dropped_weight_kb ?? 0,
            used: msg.buffer_used_kb ?? 0,
            capacity: msg.buffer_capacity_kb ?? 0,
            ratio: msg.buffer_rate_ratio ?? 1,
            fill: msg.buffer_fill_percent ?? 0,
            weightPerSecond: msg.weight_per_second_kb ?? 0,
          });
        }
      } else {
        overflowShownRef.current = false;
      }

      // средний заряд только по движущимся: если их нет, бэкенд не считает
      // его (null) — в графике такая точка не рисуется
      setBatteryAvg(msg.battery_avg ?? null);
      setData((prev) => [
        ...prev.slice(-100), // ~1.5 минуты (кадр раз в секунду)
        {
          time: new Date(msg.timestamp).toLocaleTimeString(),
          value: msg.messages_per_second,
          total: msg.stations_count,
          battery: msg.battery_avg ?? null,
        },
      ]);
    };

    return () => ws.close();
  }, [visible]);

  // вторая (жёлтая) линия — все устройства, которые сейчас не передают:
  // общее количество устройств минус текущее значение. Не ниже 0.
  const chartData = data.map((d) => ({
    ...d,
    free: Math.max(0, (d.total ?? 0) - d.value),
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
            Вес сообщений в секунду, КБ/с:{" "}
            <b>
              {weightPerSecond.toLocaleString("ru-RU", {
                minimumFractionDigits: 3,
                maximumFractionDigits: 3,
              })}
            </b>
          </div>
          <div className="total-row">
            Общий вес сообщений, КБ:{" "}
            <b>
              {totalWeight.toLocaleString("ru-RU", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </b>
          </div>
          <div className="total-row">
            Средний заряд движущихся, %:{" "}
            <b>
              {batteryAvg === null
                ? "—"
                : batteryAvg.toLocaleString("ru-RU", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
            </b>
          </div>
          {buffer && (
            <>
              <div className="total-row">
                Буфер очереди, КБ:{" "}
                <b>
                  {buffer.used.toLocaleString("ru-RU", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}{" "}
                  / {buffer.capacity.toLocaleString("ru-RU", {
                    minimumFractionDigits: 1,
                    maximumFractionDigits: 1,
                  })}{" "}
                  ({buffer.fill.toFixed(1)}%)
                </b>
                {buffer.overflow ? (
                  <span className="buffer-badge buffer-badge-overflow">
                    переполнение
                  </span>
                ) : (
                  <span className="buffer-badge">в норме</span>
                )}
              </div>
              <div className="total-row">
                Скорость обработки буфера, × к весу сообщений/с:{" "}
                <b>
                  ×{buffer.ratio.toLocaleString("ru-RU", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </b>
                <span className="dim-note">
                  {" "}
                  ≈{" "}
                  {buffer.processing.toLocaleString("ru-RU", {
                    minimumFractionDigits: 1,
                    maximumFractionDigits: 1,
                  })}{" "}
                  КБ/с
                </span>
              </div>
              <div className="total-row">
                Потеряно при переполнении, КБ:{" "}
                <b>
                  {buffer.dropped.toLocaleString("ru-RU", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </b>
              </div>
              <div className="buffer-bar">
                <div
                  className={
                    buffer.overflow ? "buffer-fill overflow" : "buffer-fill"
                  }
                  style={{ width: `${Math.min(100, buffer.fill)}%` }}
                />
              </div>
            </>
          )}
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
            <span style={{ marginRight: 14 }}>
              <i
                className="legend-dot"
                style={{ background: "#facc15" }}
              />{" "}
              Передают в пределах зоны
            </span>
            <span>
              <i
                className="legend-dot"
                style={{ background: "#34d399" }}
              />{" "}
              Средний заряд движущихся, %
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
                  yAxisId="messages"
                  stroke="#94a3b8"
                  fontSize={11}
                  tickLine={false}
                  width={40}
                />
                <YAxis
                  yAxisId="battery"
                  orientation="right"
                  stroke="#34d399"
                  fontSize={11}
                  tickLine={false}
                  width={44}
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
                  yAxisId="messages"
                  type="monotone"
                  dataKey="value"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={false}
                  name="Передают в глобальную сеть"
                />
                <Line
                  yAxisId="messages"
                  type="monotone"
                  dataKey="free"
                  stroke="#facc15"
                  strokeWidth={2}
                  dot={false}
                  name="Передают в пределах зоны"
                />
                <Line
                  yAxisId="battery"
                  type="monotone"
                  dataKey="battery"
                  stroke="#34d399"
                  strokeWidth={2}
                  dot={false}
                  name="Средний заряд движущихся, %"
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
