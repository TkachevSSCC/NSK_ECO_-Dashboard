import React, { useState } from "react";
import { CircleMarker, Popup } from "react-leaflet";

/** Цвет маркера по концентрации PM2.5 */
const levelColor = (pm) => {
  if (pm == null || Number.isNaN(pm)) return "#64748b";
  if (pm < 15) return "#22c55e"; // норма
  if (pm < 35) return "#eab308"; // умеренно
  if (pm < 75) return "#f97316"; // высокое
  return "#ef4444"; // опасное
};

export default function StationMarker({ station, highlighted = false }) {
  const [isOpen, setIsOpen] = useState(false);

  const pm25 = Number(station["PM_2_5"]);
  const isStationary = station.type_st === 0;
  const isMoving = station.type_st === 1;

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
          color: highlighted ? "#f8fafc" : "#e2e8f0",
          weight: highlighted ? 2.5 : 1,
          fillColor: levelColor(pm25),
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