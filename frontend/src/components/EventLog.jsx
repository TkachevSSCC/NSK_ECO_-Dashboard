import React, { useState } from "react";

// время события фиксируется при его добавлении, здесь только форматируется
const timeFmt = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

// уровень события: подпись и цвет для Excel совпадают с окраской в отчёте
const KIND_LABELS = {
  crit: { label: "Превышение", color: "#ef4444" },
  warn: { label: "Буфер", color: "#eab308" },
  bat: { label: "Батарея", color: "#fb923c" },
  act: { label: "Действие", color: "#38bdf8" },
  err: { label: "Ошибка", color: "#f87171" },
};

// файл за сегодняшние события: отчёт о событиях 2026-09-30 14-05.xlsx
function exportFileName() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `отчет о событиях ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(
    d.getDate()
  )} ${p(d.getHours())}-${p(d.getMinutes())}.xlsx`;
}

// библиотека переводит Date в серийный номер Excel по UTC-компонентам, из-за
// чего в файле время уезжает на смещение зоны относительно экрана (в
// Новосибирске это −7 ч). Сдвигаем метку так, чтобы UTC-компоненты совпали с
// локальным настенным временем события.
function localWallClockDate(at) {
  const d = new Date(at);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000);
}

// библиотека грузится только по клику: она нужна исключительно при
// выгрузке и иначе почти на треть раздувает основной бандл
async function buildFile(events) {
  const { default: writeXlsxFile } = await import("write-excel-file/browser");

  const header = [
    { value: "Время", fontWeight: "bold", backgroundColor: "#1e293b" },
    { value: "Уровень", fontWeight: "bold", backgroundColor: "#1e293b" },
    { value: "Событие", fontWeight: "bold", backgroundColor: "#1e293b" },
  ];

  // порядок как в отчёте на экране: новые сверху
  const rows = events.map((e) => {
    const kind = KIND_LABELS[e.kind] || { label: e.kind, color: undefined };
    return [
      // настоящая дата, а не текст: в Excel по ней можно сортировать и считать
      { value: localWallClockDate(e.at), type: Date, format: "dd.mm.yyyy hh:mm:ss" },
      { value: kind.label, textColor: kind.color },
      { value: e.text },
    ];
  });

  await writeXlsxFile([header, ...rows], {
    sheet: "События",
    columns: [{ width: 21 }, { width: 14 }, { width: 72 }],
    stickyRowsCount: 1,
  }).toFile(exportFileName());
}

export default function EventLog({ events, onClear }) {
  const [open, setOpen] = useState(true);
  const [exportMsg, setExportMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const handleExport = async () => {
    if (busy) return;
    setBusy(true);
    setExportMsg("Готовлю файл…");
    try {
      await buildFile(events);
      setExportMsg(`Выгружено событий: ${events.length}`);
    } catch (err) {
      console.error("Ошибка выгрузки отчёта в Excel:", err);
      setExportMsg("Не удалось выгрузить отчёт в Excel");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2 className="card-toggle" onClick={() => setOpen(!open)}>
        Отчёт о событиях · {events.length}
        <span className={`chev${open ? " open" : ""}`}>▸</span>
      </h2>
      {open && (
        <>
          <div className="row">
            <button
              onClick={handleExport}
              disabled={events.length === 0 || busy}
            >
              {busy ? "Выгружаю…" : "Выгрузить в Excel"}
            </button>
            <button onClick={onClear} disabled={events.length === 0}>
              Очистить отчёт
            </button>
          </div>
          {exportMsg && <div className="count-msg">{exportMsg}</div>}

          {events.length === 0 ? (
            <p className="dim-note">Событий пока не было.</p>
          ) : (
            // обёртка нужна именно потому, что таблица не может быть
            // прокручиваемым блоком: overflow на <table> не применяется
            <div className="event-log">
              <table>
                <thead>
                  <tr>
                    <th>Время</th>
                    <th>Событие</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.key} className={`ev-${e.kind}`}>
                      <td className="ev-time">
                        {timeFmt.format(new Date(e.at))}
                      </td>
                      <td className="ev-text">{e.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}