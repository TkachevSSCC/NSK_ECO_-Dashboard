import React from "react";
import { MapContainer, TileLayer, Polygon, Rectangle, Circle, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import StationMarker from "./StationMarker";

const clusterColors = [
  "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
  "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
  "#bcbd22", "#17becf",
];

export default function MapView({
  stations,
  zones,
  showZones,
  clusters,
  showClusters,
  showClusterHeads,
  showBatteryHeads,
  highlightIds = [],
  dangerZones = [],
}) {
  return (
    <MapContainer
      center={[54.8676586, 83.082019]}
      zoom={10}
      style={{ height: "100%", width: "100%" }}
    >
      <TileLayer
        attribution="&copy; OpenStreetMap contributors"
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {stations.map((station) => (
        <StationMarker
          key={station.id}
          station={station}
          highlighted={highlightIds.includes(station.id)}
        />
      ))}

      {/* таймзоны — скрыты, когда активен любой режим кластеров */}
      {showZones &&
        !showClusters &&
        !showClusterHeads &&
        !showBatteryHeads &&
        zones.map((zone, i) => (
          <Rectangle
            key={i}
            bounds={[
              [zone.x_min, zone.y_min],
              [zone.x_max, zone.y_max],
            ]}
            pathOptions={{
              color: "white",
              weight: 1,
              fillColor: clusterColors[i % clusterColors.length],
              fillOpacity: 0.35,
            }}
          />
        ))}

      {/* кластеры (обычные / с хедами / с питанием) */}
      {(showClusters || showBatteryHeads || showClusterHeads) &&
        clusters?.polygons?.length > 0 &&
        clusters.polygons.map((cluster, i) => (
          <Polygon
            key={cluster.cluster_id}
            positions={cluster.points.map(([lat, lon]) => [lat, lon])}
            pathOptions={{
              color: "white",
              weight: 1,
              fillColor: clusterColors[i % clusterColors.length],
              fillOpacity: 0.35,
            }}
          />
        ))}
    {/* «тревожные кластеры» вокруг узлов с критическим превышением PM2.5+PM10 */}
      {dangerZones.map((dz) => (
        <Circle
          key={dz.id}
          center={[dz.latitude, dz.longitude]}
          radius={dz.radius}
          pathOptions={{
            color: "#ef4444",
            weight: 2,
            fillColor: "#ef4444",
            fillOpacity: 0.25,
            dashArray: "8 6",
          }}
        >
          <Tooltip sticky>
            Узел №{dz.id} · координаты: {dz.latitude.toFixed(5)},{" "}
            {dz.longitude.toFixed(5)}
            <br />
            PM2.5 = {dz.pm25}, PM10 = {dz.pm10} (сумма = {dz.sum})
            <br />
            В кластере: {dz.members.map((m) => `№${m}`).join(", ")}
          </Tooltip>
        </Circle>
      ))}
    </MapContainer>
  );
}