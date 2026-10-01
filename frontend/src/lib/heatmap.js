const EARTH_RADIUS_KM = 6371;

// Прямоугольник, в котором бэкенд генерирует станции:
// app/api/v1/stations.py -> set_stations_count (low/high) и
// backend/seed_data.py. Тепловая карта строится по тем же границам,
// чтобы рамка совпадала с областью, где вообще могут появиться
// устройства, и не уезжала на пустые поля.
export const NOVOSIBIRSK_ANALYSIS_BOUNDS = {
  minLat: 54.81,
  maxLat: 55.16,
  minLon: 82.87,
  maxLon: 83.21,
};

export function distanceKm(a, b) {
  const toRad = (value) => (value * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(h));
}

function normalize(value, min, max) {
  return max > min ? (value - min) / (max - min) : 0;
}

function colorFor(value) {
  const stops = [
    [0, [34, 197, 94]],
    [0.35, [250, 204, 21]],
    [0.65, [249, 115, 22]],
    [1, [239, 68, 68]],
  ];
  const t = Math.max(0, Math.min(1, value));
  for (let i = 1; i < stops.length; i += 1) {
    if (t <= stops[i][0]) {
      const [leftStop, leftColor] = stops[i - 1];
      const [rightStop, rightColor] = stops[i];
      const ratio = (t - leftStop) / (rightStop - leftStop);
      return leftColor.map((channel, index) =>
        Math.round(channel + (rightColor[index] - channel) * ratio)
      );
    }
  }
  return stops[stops.length - 1][1];
}

export function buildHeatmapField(
  stations,
  cells = 36,
  bounds = NOVOSIBIRSK_ANALYSIS_BOUNDS
) {
  if (!stations.length) return null;

  const latitudes = stations.map((station) => station.latitude);
  const longitudes = stations.map((station) => station.longitude);
  const minLat = bounds?.minLat ?? Math.min(...latitudes) - 0.01;
  const maxLat = bounds?.maxLat ?? Math.max(...latitudes) + 0.01;
  const minLon = bounds?.minLon ?? Math.min(...longitudes) - 0.01;
  const maxLon = bounds?.maxLon ?? Math.max(...longitudes) + 0.01;
  const latStep = (maxLat - minLat) / cells;
  const lonStep = (maxLon - minLon) / cells;
  const centerLat = (minLat + maxLat) / 2;
  const kmPerLat = 111.32;
  const kmPerLon = kmPerLat * Math.cos((centerLat * Math.PI) / 180);
  const mass = new Float32Array(cells * cells);
  const density = new Uint16Array(cells * cells);
  const gapKm = new Float32Array(cells * cells);
  const values = stations.map(
    (station) => (Number(station["PM_2_5"]) || 0) + (Number(station["PM_10"]) || 0)
  );

  for (let row = 0; row < cells; row += 1) {
    const latitude = maxLat - (row + 0.5) * latStep;
    for (let column = 0; column < cells; column += 1) {
      const longitude = minLon + (column + 0.5) * lonStep;
      const index = row * cells + column;
      let weightedMass = 0;
      let totalWeight = 0;
      let nearestKm = Infinity;

      stations.forEach((station, stationIndex) => {
        const dx = (station.longitude - longitude) * kmPerLon;
        const dy = (station.latitude - latitude) * kmPerLat;
        const distance = Math.sqrt(dx * dx + dy * dy);
        nearestKm = Math.min(nearestKm, distance);
        if (distance < 0.05) {
          weightedMass = values[stationIndex];
          totalWeight = 1;
          return;
        }
        const weight = 1 / distance ** 2;
        weightedMass += values[stationIndex] * weight;
        totalWeight += weight;
      });

      mass[index] = totalWeight ? weightedMass / totalWeight : 0;
      gapKm[index] = Number.isFinite(nearestKm) ? nearestKm : 0;
    }
  }

  // Count station density separately so each station contributes once to a cell.
  density.fill(0);
  stations.forEach((station) => {
    const row = Math.max(
      0,
      Math.min(cells - 1, Math.floor((maxLat - station.latitude) / latStep))
    );
    const column = Math.max(
      0,
      Math.min(cells - 1, Math.floor((station.longitude - minLon) / lonStep))
    );
    density[row * cells + column] += 1;
  });

  const massMin = Math.min(...mass);
  const massMax = Math.max(...mass);
  const gapMax = Math.max(...gapKm);
  const score = new Float32Array(cells * cells);
  for (let i = 0; i < score.length; i += 1) {
    score[i] = normalize(mass[i], massMin, massMax) * normalize(gapKm[i], 0, gapMax);
  }

  return {
    cells,
    mass,
    density,
    gapKm,
    score,
    massMin,
    massMax,
    minLat,
    maxLat,
    minLon,
    maxLon,
    latStep,
    lonStep,
  };
}

// Отступ от края карты в ячейках. Точка на самой кромке попадает на обрез
// отображаемой области и выглядит «за границей города», поэтому такие
// ячейки не рекомендуем.
export const INSTALL_EDGE_MARGIN = 2;

export function pickInstallSites(field, count = 5, waterMask = null) {
  if (!field) return [];
  const candidates = Array.from(field.score, (score, index) => ({ score, index }))
    .sort((a, b) => b.score - a.score);
  const selected = [];
  const minDistanceKm = Math.max(field.latStep * 111.32 * 2, 1.5);
  const centerLat = (field.minLat + field.maxLat) / 2;
  const kmPerLon = 111.32 * Math.cos((centerLat * Math.PI) / 180);
  const margin = INSTALL_EDGE_MARGIN;

  for (const candidate of candidates) {
    if (candidate.score <= 0) continue;
    const row = Math.floor(candidate.index / field.cells);
    const column = candidate.index % field.cells;

    // не рекомендовать точку на воде: геометрия воды считается на бэкенде
    if (waterMask && waterMask.length === field.cells * field.cells) {
      if (waterMask[candidate.index]) continue;
    }
    // и не на самом краю области
    if (
      row < margin ||
      column < margin ||
      row > field.cells - 1 - margin ||
      column > field.cells - 1 - margin
    ) {
      continue;
    }

    const latitude = field.maxLat - (row + 0.5) * field.latStep;
    const longitude = field.minLon + (column + 0.5) * field.lonStep;
    const tooClose = selected.some((site) => {
      const dx = (site.longitude - longitude) * kmPerLon;
      const dy = (site.latitude - latitude) * 111.32;
      return Math.sqrt(dx * dx + dy * dy) < minDistanceKm;
    });
    if (tooClose) continue;
    selected.push({
      number: selected.length + 1,
      latitude,
      longitude,
      score: candidate.score,
      mass: field.mass[candidate.index],
      gapKm: field.gapKm[candidate.index],
    });
    if (selected.length === count) break;
  }
  return selected;
}

export function renderHeatmap(field) {
  if (!field || typeof document === "undefined") return null;
  const scale = 4;
  const canvas = document.createElement("canvas");
  canvas.width = field.cells * scale;
  canvas.height = field.cells * scale;
  const context = canvas.getContext("2d");
  const image = context.createImageData(field.cells, field.cells);

  for (let i = 0; i < field.mass.length; i += 1) {
    const color = colorFor(normalize(field.mass[i], field.massMin, field.massMax));
    const pixel = i * 4;
    image.data[pixel] = color[0];
    image.data[pixel + 1] = color[1];
    image.data[pixel + 2] = color[2];
    image.data[pixel + 3] = 115;
  }

  const smallCanvas = document.createElement("canvas");
  smallCanvas.width = field.cells;
  smallCanvas.height = field.cells;
  smallCanvas.getContext("2d").putImageData(image, 0, 0);
  context.imageSmoothingEnabled = true;
  context.drawImage(smallCanvas, 0, 0, canvas.width, canvas.height);

  // Light contours show where station density changes without hiding pollution.
  context.strokeStyle = "rgba(226, 232, 240, 0.5)";
  context.lineWidth = 1.5;
  for (const level of [1, 2, 3]) {
    for (let row = 0; row < field.cells; row += 1) {
      for (let column = 0; column < field.cells; column += 1) {
        const index = row * field.cells + column;
        if (field.density[index] < level) continue;
        const x = column * scale;
        const y = row * scale;
        const drawEdge = (nextIndex, x1, y1, x2, y2) => {
          if (nextIndex < 0 || field.density[nextIndex] < level) {
            context.beginPath();
            context.moveTo(x1, y1);
            context.lineTo(x2, y2);
            context.stroke();
          }
        };
        drawEdge(
          row > 0 ? index - field.cells : -1,
          x,
          y,
          x + scale,
          y
        );
        drawEdge(
          row < field.cells - 1 ? index + field.cells : -1,
          x,
          y + scale,
          x + scale,
          y + scale
        );
        drawEdge(column > 0 ? index - 1 : -1, x, y, x, y + scale);
        drawEdge(
          column < field.cells - 1 ? index + 1 : -1,
          x + scale,
          y,
          x + scale,
          y + scale
        );
      }
    }
  }
  return canvas.toDataURL("image/png");
}
