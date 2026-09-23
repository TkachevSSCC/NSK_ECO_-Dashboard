import React, { useState } from "react";
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

/** Цвет маркера по худшему из PM2.5 / PM10 */
const levelColor = (pm25, pm10) => {
  const lvl = Math.max(
    pollutionLevel(pm25, PM25_CUTOFFS),
    pollutionLevel(pm10, PM10_CUTOFFS)
  );
  return lvl < 0 ? "#64748b" : LEVEL_COLORS[lvl];
};

export default function StationMarker({ station, highlighted = false }) {
  const [isOpen, setIsOpen] = useState(false);

  const pm25 = Number(station["PM_2_5"]);
  const pm10 = Number(station["PM_10"]);
  const isStationary = station.type_st === 0;
  const isMoving = station.type_st === 1;
  const isClusterHead = station.type_st === 2;

  return (
    <>
      {highlighted && (
        <CircleMarker
          center={[station.latitude, station.longitude]}
          radius={15}
          pathOptions={{
            color: "#e0f2fe",
            weight: 2,
            dashArray: "4 4",
            fillColor: "#38bdf8",
            fillOpacity: 0.18,
          }}
        />
      )}
      <CircleMarker
        center={[station.latitude, station.longitude]}
        radius={isStationary ? 7 : isMoving ? 5 : 9}
        pathOptions={{
          // рамка кластер-хэда — чёрная
          color: isClusterHead ? "#000000" : highlighted ? "#f8fafc" : "#e2e8f0",
          weight: highlighted ? 2.5 : isClusterHead ? 2 : 1,
          fillColor: levelColor(pm25, pm10),
          fillOpacity: isMoving ? 0.55 : 0.9,
        }}
        eventHandlers={{ click: () => setIsOpen(true) }}
      >
        {isOpen && (
          <Popup onClose={() => setIsOpen(false)}>
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