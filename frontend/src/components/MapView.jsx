import React from "react";
import L from "leaflet";
import {
  MapContainer,
  TileLayer,
  Polygon,
  Rectangle,
  Circle,
  CircleMarker,
  ImageOverlay,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import StationMarker from "./StationMarker";

const clusterColors = [
  "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
  "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
  "#bcbd22", "#17becf",
];

function MapMetricsControl({ weightPerSecond }) {
  const map = useMap();
  const valueRef = React.useRef(null);

  // Панель создаётся один раз: пересоздавать control на каждом тике нельзя,
  // иначе он дёргается и пересоздаёт DOM. Значение обновляем точечно.
  React.useEffect(() => {
    const control = L.control({ position: "topright" });
    const element = L.DomUtil.create("div", "map-metrics-control");
    element.innerHTML = `
      <div class="map-metrics-title">Глобальная сеть</div>
      <div class="map-metrics-value">0,00 КБ/с</div>
      <div class="map-metrics-caption">вес сообщений в секунду</div>
    `;
    valueRef.current = element.querySelector(".map-metrics-value");
    control.onAdd = () => element;
    control.addTo(map);
    return () => {
      valueRef.current = null;
      control.remove();
    };
  }, [map]);

  React.useEffect(() => {
    const value = Number(weightPerSecond || 0).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    if (valueRef.current) valueRef.current.textContent = `${value} КБ/с`;
  }, [weightPerSecond]);

  return null;
}

// Ловец кликов в режиме «добавить устройство»: монтируется только когда
// включено добавление, поэтому обычные клики по карте ничего не делают.
function MapClickCatcher({ onAddDevice }) {
  const map = useMap();

  // курсор-прицел, пока идёт режим размещения
  React.useEffect(() => {
    const host = map.getContainer();
    host.classList.add("placing-device");
    return () => host.classList.remove("placing-device");
  }, [map]);

  useMapEvents({
    click(e) {
      if (!onAddDevice) return;
      // клик по маркеру/кругу/зоне не должен ставить устройство поверх него
      const target = e.originalEvent?.target;
      if (
        target &&
        typeof target.closest === "function" &&
        target.closest(".leaflet-interactive")
      ) {
        return;
      }
      onAddDevice(e.latlng.lat, e.latlng.lng);
    },
  });

  return null;
}

export default function MapView({
  stations,
  zones,
  showZones,
  clusters,
  showClusterHeads,
  showBatteryHeads,
  highlightIds = [],
  dangerZones = [],
  heatmapImage = null,
  heatmapBounds = null,
  installSites = [],
  weightPerSecond = 0,
  onAddDevice = null,
}) {
  // станции с нулевым зарядом не передают и не показываются на карте
  const liveStations = stations.filter((s) => (s.battery_life ?? 0) > 0);

  // Set вместо includes: список подсвеченных узлов проверяется для каждой
  // станции на каждом тике, а includes на массиве даёт O(n) на узел
  const highlighted = React.useMemo(
    () => new Set(highlightIds),
    [highlightIds]
  );

  return (
    <MapContainer
      center={[54.8676586, 83.082019]}
      zoom={10}
      attributionControl={false}
      style={{ height: "100%", width: "100%" }}
    >
      <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <MapMetricsControl weightPerSecond={weightPerSecond} />
      {onAddDevice && <MapClickCatcher onAddDevice={onAddDevice} />}

      {heatmapImage && heatmapBounds && (
        <ImageOverlay
          url={heatmapImage}
          bounds={heatmapBounds}
          opacity={0.65}
          zIndex={1}
        />
      )}

      {liveStations.map((station) => (
        <StationMarker
          key={station.id}
          station={station}
          highlighted={highlighted.has(station.id)}
        />
      ))}

      {installSites.map((site) => (
        <CircleMarker
          key={`install-${site.number}`}
          center={[site.latitude, site.longitude]}
          radius={9}
          pathOptions={{
            color: "#f8fafc",
            weight: 2,
            fillColor: "#06b6d4",
            fillOpacity: 0.95,
          }}
        >
          <Tooltip permanent direction="top" offset={[0, -8]}>
            <strong>Точка №{site.number}</strong>
            <br />
            Загрязнение: {site.mass.toFixed(1)} мкг/м³
            <br />
            До станции: {site.gapKm.toFixed(1)} км
          </Tooltip>
        </CircleMarker>
      ))}

      {/* таймзоны — скрыты, когда активен любой режим кластеров */}
      {showZones &&
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

      {/* кластеры (с хедами / с питанием) */}
      {(showBatteryHeads || showClusterHeads) &&
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
