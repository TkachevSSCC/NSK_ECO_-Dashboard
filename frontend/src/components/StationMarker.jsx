import { memo, useCallback, useMemo, useState } from "react";
import { CircleMarker, Popup } from "react-leaflet";

const LEVEL_COLORS = ["#22c55e", "#eab308", "#f97316", "#ef4444"];

/** Степень загрязнения 0..3 по концентрации и порогам */
const pollutionLevel = (pm, cutoffs) => {
  if (pm == null || Number.isNaN(pm)) return -1;
  let lvl = 0;
  cutoffs.forEach((c) => {
    if (pm >= c) lvl += 1;
  });
  return lvl;
};

// пороги: PM2.5 и PM10 (ПДК в бэкенде: PM2.5 > 25, PM10 > 50)
const PM25_CUTOFFS = [15, 35, 75];
const PM10_CUTOFFS = [30, 70, 150];

/** Цвет маркера по максимальному уровню из PM2.5 / PM10 */
const levelColor = (pm25, pm10) => {
  const lvl = Math.max(
    pollutionLevel(pm25, PM25_CUTOFFS),
    pollutionLevel(pm10, PM10_CUTOFFS)
  );
  return lvl < 0 ? "#64748b" : LEVEL_COLORS[lvl];
};

// неизменный объект: подсветка не зависит от значений станции
const HIGHLIGHT_PATH_OPTIONS = {
  color: "#e0f2fe",
  weight: 2,
  dashArray: "4 4",
  fillColor: "#38bdf8",
  fillOpacity: 0.18,
};

// Опрашиваем станции раз в секунду, и каждый ответ — новый объект, поэтому
// memo со своим сравнением пропускает рендер станций, у которых не изменились
// ни координаты, ни показания, ни подсветка. Без этого карта перерисовывает
// все маркеры на каждом тике.
function StationMarker({ station, highlighted = false }) {
  const [isOpen, setIsOpen] = useState(false);

  const pm25 = Number(station["PM_2_5"]);
  const pm10 = Number(station["PM_10"]);
  const isStationary = station.type_st === 0;
  const isMoving = station.type_st === 1;
  const isClusterHead = station.type_st === 2;

  // Опрос станций идёт раз в секунду и приносит новые объекты, поэтому
  // center / pathOptions / eventHandlers нужно переиспользовать между
  // рендерами: react-leaflet сравнивает их по ссылке и на каждый новый
  // объект вызывает setLatLng, setStyle и переподключение обработчиков.
  // useMemo по значениям убирает эти вызовы, когда станция не изменилась.
  const center = useMemo(
    () => [station.latitude, station.longitude],
    [station.latitude, station.longitude]
  );

  const radius = isStationary ? 7 : isMoving ? 5 : 9;
  const pathOptions = useMemo(
    () => ({
      // рамка кластер-хэда — чёрная
      color: isClusterHead ? "#000000" : highlighted ? "#f8fafc" : "#e2e8f0",
      weight: highlighted ? 2.5 : isClusterHead ? 2 : 1,
      fillColor: levelColor(pm25, pm10),
      fillOpacity: isMoving ? 0.55 : 0.9,
    }),
    [isClusterHead, highlighted, pm25, pm10, isMoving]
  );

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const eventHandlers = useMemo(() => ({ click: open }), [open]);

  return (
    <>
      {highlighted && (
        <CircleMarker
          center={center}
          radius={15}
          pathOptions={HIGHLIGHT_PATH_OPTIONS}
        />
      )}
      <CircleMarker
        center={center}
        radius={radius}
        pathOptions={pathOptions}
        eventHandlers={eventHandlers}
      >
        {isOpen && (
          <Popup onClose={close}>
            <b>ПНЗ №{station.id}</b>
            <br />
            Тип:{" "}
            {isStationary
              ? "Стационарная"
              : isMoving
              ? "Движущаяся"
              : "Кластерная"}
            <br />
            Координаты: {station.latitude.toFixed(5)},{" "}
            {station.longitude.toFixed(5)}
            <br />
            PM 2.5: {station["PM_2_5"]}
            <br />
            PM 10: {station["PM_10"]}
          </Popup>
        )}
      </CircleMarker>
    </>
  );
}

// Сравниваем по значениям, а не по ссылке на station: сервер отдаёт новый
// объект каждую секунду, даже если станция не двигалась и не изменилась.
export default memo(StationMarker, (prev, next) => {
  const a = prev.station;
  const b = next.station;
  return (
    prev.highlighted === next.highlighted &&
    a.id === b.id &&
    a.latitude === b.latitude &&
    a.longitude === b.longitude &&
    a.type_st === b.type_st &&
    a["PM_2_5"] === b["PM_2_5"] &&
    a["PM_10"] === b["PM_10"]
  );
});
