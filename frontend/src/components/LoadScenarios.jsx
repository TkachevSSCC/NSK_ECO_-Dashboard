import { useCallback, useEffect, useState } from "react";

// тот же базовый адрес, что в App.jsx: запросы идут на тот же origin
const BASE = "";

// срок до разряда обычно меньше часа, поэтому показываем минуты,
// а не «0,2 ч» — так честнее и понятнее
function formatHours(hours) {
  if (hours == null) return "—";
  const totalMinutes = Math.round(hours * 60);
  if (totalMinutes < 90) return `${totalMinutes} мин`;
  return `${hours.toFixed(1).replace(".", ",")} ч`;
}

/**
 * Сравнение нагрузки на сеть по режимам передачи: «передают все»
 * против кластерных. Данные приходят с /stations/load_scenarios —
 * это расчёт по текущему состоянию станций, он ничего не переключает.
 */
export default function LoadScenarios() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${BASE}/stations/load_scenarios`);
      const json = await res.json();
      if (!res.ok || json.status === "error") {
        setError(json.message || `Ошибка ${res.status}`);
        return;
      }
      setData(json);
      setError("");
    } catch {
      setError("Не удалось получить прогноз нагрузки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // пересчитываем раз в 5 секунд: состав станций и кластеров меняется
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  if (error) {
    return <p className="dim-note">Не удалось построить прогноз: {error}</p>;
  }

  if (!data) {
    return (
      <p className="dim-note">
        {loading ? "Считаю прогноз нагрузки…" : "Нажмите, чтобы пересчитать"}
      </p>
    );
  }

  const { scenarios } = data;
  // масштаб полосок — от максимума по всем режимам
  const maxGlobal = Math.max(1, ...scenarios.map((s) => s.global_kb));
  // у зональной полоски свой максимум: иначе она рисуется от чужой величины
  // и визуально недооценивает зональный трафик
  const maxZone = Math.max(1, ...scenarios.map((s) => s.zone_kb));

  return (
    <div className="load-scenarios">
      {scenarios.map((s) => {
        // Экономия считается по всему трафику (сеть + зона), а не только по
        // глобальной сети: в кластерном режиме сообщения не исчезают, они
        // уходят из сети в зону. Считать только сеть — завышало выгоду
        // почти вдвое (−89% вместо −53%).
        const saving =
          scenarios[0].total_kb > 0
            ? Math.max(0, Math.round((1 - s.total_kb / scenarios[0].total_kb) * 100))
            : 0;
        // отдельно доля трафика, ушедшего из глобальной сети
        const netSaving =
          scenarios[0].global_kb > 0
            ? Math.max(0, Math.round((1 - s.global_kb / scenarios[0].global_kb) * 100))
            : 0;
        return (
          <div
            className={`scenario${s.is_current ? " scenario-current" : ""}`}
            key={s.mode}
          >
            <div className="scenario-head">
              <span className="scenario-title">{s.title}</span>
              {s.is_current && <span className="scenario-badge">сейчас</span>}
              {!s.is_current && saving > 0 && (
                <span
                  className="scenario-saving"
                  title="Весь трафик: в глобальную сеть плюс в пределах зоны"
                >
                  −{saving}% трафика
                  <span className="scenario-saving-net">
                    {" "}
                    (из сети −{netSaving}%)
                  </span>
                </span>
              )}
            </div>
            <div className="scenario-hint">{s.hint}</div>

            {s.global_weight_factor > 1 && (
              <div className="scenario-weight">
                загрязнение у отправителей: ×{s.global_weight_factor} к весу сообщения
              </div>
            )}

            <div className="scenario-bar-row">
              <span className="scenario-bar-label">в глобальную сеть</span>
              <div className="scenario-bar">
                <div
                  className="scenario-bar-fill global"
                  style={{ width: `${(s.global_kb / maxGlobal) * 100}%` }}
                />
              </div>
              <span className="scenario-bar-value">
                {s.global_kb.toLocaleString("ru-RU")} КБ/с
              </span>
            </div>

            <div className="scenario-bar-row">
              <span className="scenario-bar-label">в пределах зоны</span>
              <div className="scenario-bar">
                <div
                  className="scenario-bar-fill zone"
                  style={{ width: `${(s.zone_kb / maxZone) * 100}%` }}
                />
              </div>
              <span className="scenario-bar-value">
                {s.zone_kb.toLocaleString("ru-RU")} КБ/с
              </span>
            </div>

            <div className="scenario-figs">
              <span>
                всего трафика:{" "}
                <b>{s.total_kb.toLocaleString("ru-RU")}</b> КБ/с
              </span>
              <span>
                сообщений в сеть:{" "}
                <b>{s.global_messages.toLocaleString("ru-RU")}</b>/с
              </span>
              {s.hours_to_empty != null && (
                <span>
                  первый разряд:{" "}
                  <b>{formatHours(s.hours_to_empty)}</b>
                </span>
              )}
              <span title="Сумма списания по всем устройствам: на сколько процентов уменьшится суммарный заряд за секунду. Устройство в сети теряет 0,1 %/с, в зоне 0,06 %/с">
                расход заряда:{" "}
                <b>{s.battery_drain_pct.toLocaleString("ru-RU")}</b> %/с
              </span>
            </div>
          </div>
        );
      })}

      <button className="btn-recalc" onClick={load} disabled={loading}>
        {loading ? "Пересчитываю…" : "Пересчитать"}
      </button>
    </div>
  );
}
