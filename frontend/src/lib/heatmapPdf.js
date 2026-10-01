// Отчёт по тепловой карте в PDF.
//
// Страницы рисуются на canvas в пунктах (A4, портрет) и затем вшиваются в
// PDF как картинки — см. lib/pdf.js. Рисуем сами, а не подключаем библиотеку:
// кириллица остаётся без встраивания шрифтов, а в бандл не добавляется
// ни килобайта.

import { canvasesToPdf } from "./pdf";
import { HEAT_COLOR_STOPS, renderHeatmapCanvas } from "./heatmap";

const PAGE_W = 595.28; // A4, пункты
const PAGE_H = 841.89;
const DPI = 150; // 150 точек на дюйм — текст в PDF остаётся читаемым
const SCALE = DPI / 72;
const MARGIN = 48;
const KM_PER_DEG_LAT = 111.32;

const FONT_STACK =
  '-apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const INK = "#0f172a";
const MUTED = "#64748b";
const FAINT = "#94a3b8";
const HAIRLINE = "#e2e8f0";

function newPage() {
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(PAGE_W * SCALE);
  canvas.height = Math.round(PAGE_H * SCALE);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.scale(SCALE, SCALE);
  ctx.textBaseline = "alphabetic";
  return { canvas, ctx };
}

function heading(ctx, text, x, y, size = 20) {
  ctx.fillStyle = INK;
  ctx.font = `600 ${size}px ${FONT_STACK}`;
  ctx.fillText(text, x, y);
}

function body(ctx, text, x, y, size = 10, color = MUTED) {
  ctx.fillStyle = color;
  ctx.font = `400 ${size}px ${FONT_STACK}`;
  ctx.fillText(text, x, y);
}

// текст в несколько строк по ширине — длинные пояснения не влезают в колонку
function paragraph(ctx, text, x, y, width, size = 9, color = MUTED, leading = 13) {
  ctx.fillStyle = color;
  ctx.font = `400 ${size}px ${FONT_STACK}`;
  const words = text.split(" ");
  let line = "";
  let cursor = y;
  for (const word of words) {
    const probe = line ? `${line} ${word}` : word;
    if (ctx.measureText(probe).width > width && line) {
      ctx.fillText(line, x, cursor);
      cursor += leading;
      line = word;
    } else {
      line = probe;
    }
  }
  if (line) ctx.fillText(line, x, cursor);
  return cursor;
}

function stamp(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return {
    text: `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`,
    file: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}_${pad(date.getHours())}-${pad(date.getMinutes())}`,
  };
}

// геометрия области карты: сколько километров влезло в рамку
function geoOf(field) {
  const centerLat = (field.minLat + field.maxLat) / 2;
  const kmPerLon = KM_PER_DEG_LAT * Math.cos((centerLat * Math.PI) / 180);
  return {
    kmPerLon,
    widthKm: (field.maxLon - field.minLon) * kmPerLon,
    heightKm: (field.maxLat - field.minLat) * KM_PER_DEG_LAT,
  };
}

function footer(ctx, meta, page, pages) {
  const y = PAGE_H - 38;
  ctx.strokeStyle = HAIRLINE;
  ctx.lineWidth = 0.6;
  ctx.beginPath();
  ctx.moveTo(MARGIN, y - 16);
  ctx.lineTo(PAGE_W - MARGIN, y - 16);
  ctx.stroke();
  body(ctx, `NSK ECO · тепловая карта · ${meta.stamp}`, MARGIN, y, 8, FAINT);
  ctx.textAlign = "right";
  body(ctx, `${page} из ${pages}`, PAGE_W - MARGIN, y, 8, FAINT);
  ctx.textAlign = "left";
}

function drawOverview(field, sites, meta) {
  const { canvas, ctx } = newPage();
  const geo = geoOf(field);

  heading(ctx, "Тепловая карта загрязнения воздуха", MARGIN, 66);
  body(
    ctx,
    `Новосибирск · ${meta.stationCount} станций в расчёте · сетка ${field.cells}×${field.cells} · ${meta.stamp}`,
    MARGIN,
    86
  );

  // ---- карта ----
  const boxX = MARGIN;
  const boxY = 106;
  const boxW = 332;
  const boxH = 470;
  // пропорции настоящие: иначе город на листе растягивается по ширине
  const aspect = geo.widthKm / geo.heightKm;
  let mapH = boxH;
  let mapW = mapH * aspect;
  if (mapW > boxW) {
    mapW = boxW;
    mapH = mapW / aspect;
  }
  const mapX = boxX + (boxW - mapW) / 2;
  const mapY = boxY + (boxH - mapH) / 2;

  const heatCanvas = renderHeatmapCanvas(field);
  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(boxX, boxY, boxW, boxH);
  if (heatCanvas) {
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(heatCanvas, mapX, mapY, mapW, mapH);
  }
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 0.8;
  ctx.strokeRect(boxX + 0.4, boxY + 0.4, boxW - 0.8, boxH - 0.8);

  // рекомендованные точки наносятся по координатам, а не «как получилось»
  sites.forEach((site) => {
    const px =
      mapX +
      ((site.longitude - field.minLon) / (field.maxLon - field.minLon)) * mapW;
    const py =
      mapY +
      ((field.maxLat - site.latitude) / (field.maxLat - field.minLat)) * mapH;
    ctx.beginPath();
    ctx.arc(px, py, 9, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.font = `600 10px ${FONT_STACK}`;
    ctx.textAlign = "center";
    ctx.fillText(String(site.number), px, py + 3.5);
    ctx.textAlign = "left";
  });

  // ---- колонка справа: шкала и параметры расчёта ----
  const colX = boxX + boxW + 28;
  const colW = PAGE_W - MARGIN - colX;

  body(ctx, "Шкала суммы PM2.5 + PM10", colX, 112, 10, INK);

  const gradX = colX + 2;
  const gradY = 124;
  const gradW = 14;
  const gradH = 190;
  const gradient = ctx.createLinearGradient(0, gradY + gradH, 0, gradY);
  HEAT_COLOR_STOPS.forEach(([stop, rgb]) => {
    gradient.addColorStop(stop, `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`);
  });
  ctx.fillStyle = gradient;
  ctx.fillRect(gradX, gradY, gradW, gradH);
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 0.7;
  ctx.strokeRect(gradX + 0.35, gradY + 0.35, gradW - 0.7, gradH - 0.7);

  ctx.font = `400 8px ${FONT_STACK}`;
  ctx.fillStyle = MUTED;
  [[field.massMax, gradY + 7], [field.massMin, gradY + gradH]].forEach(
    ([value, y]) => {
      ctx.fillText(`${value.toFixed(1)}`, gradX + gradW + 5, y);
    }
  );
  ctx.textAlign = "right";
  ctx.fillText("мкг/м³", gradX - 4, gradY + gradH / 2);
  ctx.textAlign = "left";

  let cursor = gradY + gradH + 34;
  const stats = [
    ["Сетка", `${field.cells}×${field.cells}`],
    [
      "Размер ячейки",
      `${(field.latStep * KM_PER_DEG_LAT).toFixed(2)}×${(field.lonStep * geo.kmPerLon).toFixed(2)} км`,
    ],
    ["Охват", `${geo.widthKm.toFixed(1)}×${geo.heightKm.toFixed(1)} км`],
    ["Станций", String(meta.stationCount)],
    ["Точек", String(sites.length)],
  ];
  stats.forEach(([label, value]) => {
    ctx.strokeStyle = HAIRLINE;
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(colX, cursor - 11);
    ctx.lineTo(colX + colW, cursor - 11);
    ctx.stroke();
    body(ctx, label, colX, cursor, 9, MUTED);
    ctx.textAlign = "right";
    body(ctx, value, colX + colW, cursor, 9, INK);
    ctx.textAlign = "left";
    cursor += 22;
  });

  // ---- пояснения под картой ----
  // paragraph() возвращает базовую линию последней нарисованной строки, поэтому
  // следующий абзац начинаем на полный интерлиньяж ниже, а не «впритык»
  const LEADING = 13;
  const noteY = boxY + boxH + 30;
  body(ctx, "Как читать карту", MARGIN, noteY, 11, INK);
  cursor = paragraph(
    ctx,
    "Цвет ячейки — расчётная сумма PM2.5 и PM10, полученная усреднением по близлежащим станциям с весом 1/d²: чем больше значение, тем «теплее» ячейка.",
    MARGIN,
    noteY + 18,
    332,
    9
  );
  cursor = paragraph(
    ctx,
    "Светлые линии — контуры плотности устройств (уровни 1, 2 и 3 станции на ячейку). Они показывают, где измеряющих устройств больше, не закрывая сам загрязнитель.",
    MARGIN,
    cursor + LEADING,
    332,
    9
  );
  cursor = paragraph(
    ctx,
    "Номера на карте — рекомендованные точки установки: высокая расчётная загрязнённость в сочетании с большим расстоянием до ближайшей станции. Места в воде и на самом краю области не предлагаются.",
    MARGIN,
    cursor + LEADING,
    332,
    9
  );

  // условный знак точек
  const keyY = cursor + 26;
  ctx.beginPath();
  ctx.arc(MARGIN + 7, keyY - 3, 7, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
  ctx.fill();
  body(ctx, "рекомендованная точка установки", MARGIN + 20, keyY, 9, MUTED);

  footer(ctx, meta, 1, meta.pages);
  return canvas;
}

function drawSites(sites, meta) {
  const { canvas, ctx } = newPage();
  heading(ctx, "Рекомендованные точки установки", MARGIN, 66, 17);
  paragraph(
    ctx,
    "Точки выбраны там, где высокая расчётная загрязнённость сочетается с большим расстоянием до ближайшей станции. Места в воде и на самом краю карты не предлагаются.",
    MARGIN,
    88,
    PAGE_W - MARGIN * 2,
    9
  );

  const columns = [
    { title: "№", width: 30, cell: (s) => String(s.number) },
    { title: "Широта", width: 92, cell: (s) => s.latitude.toFixed(5) },
    { title: "Долгота", width: 92, cell: (s) => s.longitude.toFixed(5) },
    {
      title: "Загрязнение, мкг/м³",
      width: 116,
      cell: (s) => s.mass.toFixed(1),
    },
    { title: "До станции, км", width: 92, cell: (s) => s.gapKm.toFixed(1) },
    { title: "Оценка", width: 56, cell: (s) => s.score.toFixed(3) },
  ];

  let x = MARGIN;
  let y = 132;
  const rowH = 22;

  ctx.fillStyle = "#f1f5f9";
  ctx.fillRect(MARGIN, y - rowH + 4, columns.reduce((sum, c) => sum + c.width, 0), rowH);
  columns.forEach((column) => {
    body(ctx, column.title, x + 6, y - 7, 9, INK);
    x += column.width;
  });
  y += rowH;

  sites.forEach((site, index) => {
    if (index % 2 === 1) {
      ctx.fillStyle = "#f8fafc";
      ctx.fillRect(
        MARGIN,
        y - rowH + 4,
        columns.reduce((sum, c) => sum + c.width, 0),
        rowH
      );
    }
    x = MARGIN;
    columns.forEach((column) => {
      body(ctx, column.cell(site), x + 6, y - 7, 9, INK);
      x += column.width;
    });
    y += rowH;
  });

  y += 22;
  body(ctx, "Параметры расчёта", MARGIN, y, 11, INK);
  paragraph(
    ctx,
    `Значение загрязнения — расчётная сумма PM2.5 + PM10 в точке, мкг/м³. Расстояние до станции — до ближайшей по прямой, км. Оценка — произведение нормированной загрязнённости на нормированное расстояние, по ней и выбирались точки.`,
    MARGIN,
    y + 16,
    PAGE_W - MARGIN * 2,
    9
  );
  paragraph(
    ctx,
    `Всего станций в расчёте: ${meta.stationCount}. Время формирования отчёта: ${meta.stamp}.`,
    MARGIN,
    y + 58,
    PAGE_W - MARGIN * 2,
    9
  );

  footer(ctx, meta, 2, meta.pages);
  return canvas;
}

/**
 * Собирает PDF-отчёт по тепловой карте.
 *
 * @param {{field: object, sites: Array, stationCount: number}} params
 * @returns {Promise<{blob: Blob, fileName: string}>}
 */
export async function buildHeatmapPdf({ field, sites = [], stationCount = 0 }) {
  if (!field) throw new Error("Тепловая карта не построена");

  const now = stamp(new Date());
  const meta = { stamp: now.text, stationCount, pages: sites.length ? 2 : 1 };

  const pages = [drawOverview(field, sites, meta)];
  if (sites.length) pages.push(drawSites(sites, meta));

  const blob = await canvasesToPdf(pages, {
    widthPt: PAGE_W,
    heightPt: PAGE_H,
    title: `Тепловая карта ${now.text}`,
  });
  return { blob, fileName: `Тепловая карта ${now.file}.pdf` };
}

// скачивание файла в обычную папку загрузок браузера
export function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // ссылку отпускаем не сразу: браузеру нужно начать скачивание
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
