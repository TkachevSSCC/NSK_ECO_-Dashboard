import React from "react";
import { MapContainer, TileLayer, Polygon, Rectangle } from "react-leaflet";
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

      {/* таймзоны */}
      {showZones &&
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
    </MapContainer>
  );
}