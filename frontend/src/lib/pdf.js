// Минимальная сборка PDF из картинок.
//
// Страница PDF — это готовая картинка (canvas), вшитая как XObject. Свои
// шрифты для этого не нужны, поэтому кириллица получается ровно такая же,
// как на экране: её рисует сам canvas. Библиотеки для этого не требуются —
// файл собирается вручную, байт за байтом.

const encoder = new TextEncoder();

function concatBytes(chunks) {
  let length = 0;
  for (const chunk of chunks) length += chunk.length;
  const out = new Uint8Array(length);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.length;
  }
  return out;
}

// сжатие потока: без него картинка страницы весит десятки мегабайт.
// CompressionStream есть во всех актуальных браузерах; если нет — пишем
// данные как есть, файл будет просто крупнее.
async function deflate(bytes) {
  if (typeof CompressionStream === "undefined") return null;
  const stream = new Blob([bytes])
    .stream()
    .pipeThrough(new CompressionStream("deflate"));
  const buffer = await new Response(stream).arrayBuffer();
  return new Uint8Array(buffer);
}

// число в PDF пишем без экспоненты: в длинах объектов она недопустима
function num(value) {
  return String(Number(value.toFixed(2)));
}

// строка свойств файла. PDFDocEncoding кириллицу не содержит, поэтому
// отдаём UTF-16BE с BOM — тогда заголовок читается в любом просмотрщике.
function textString(value) {
  let hex = "FEFF";
  for (const char of value) {
    hex += char.charCodeAt(0).toString(16).padStart(4, "0").toUpperCase();
  }
  return `<${hex}>`;
}

/**
 * Собирает PDF из готовых canvas-страниц.
 *
 * @param {HTMLCanvasElement[]} canvases страницы отчёта, по одной на лист
 * @param {{widthPt: number, heightPt: number, title?: string, author?: string}} options
 * @returns {Promise<Blob>}
 */
export async function canvasesToPdf(canvases, options = {}) {
  const {
    widthPt = 595.28,
    heightPt = 841.89,
    title = "Отчёт",
    author = "NSK ECO",
  } = options;

  if (!Array.isArray(canvases) || canvases.length === 0) {
    throw new Error("Нечего выгружать: не удалось построить ни одной страницы");
  }

  // 1 — каталог, 2 — дерево страниц, дальше на каждую страницу три объекта:
  // сама страница, её содержимое и картинка
  const objects = [];
  const pageRefs = [];

  for (let index = 0; index < canvases.length; index += 1) {
    const canvas = canvases[index];
    const { width, height } = canvas;
    const rgba = canvas.getContext("2d").getImageData(0, 0, width, height).data;

    // PDF-картинка хранит RGB без альфы, прозрачных пикселей у нас нет
    const rgb = new Uint8Array(width * height * 3);
    for (let src = 0, dst = 0; src < rgba.length; src += 4, dst += 3) {
      rgb[dst] = rgba[src];
      rgb[dst + 1] = rgba[src + 1];
      rgb[dst + 2] = rgba[src + 2];
    }

    const packed = await deflate(rgb);
    const payload = packed || rgb;
    const filter = packed ? "/Filter /FlateDecode " : "";

    const content = encoder.encode(
      `q\n${num(widthPt)} 0 0 ${num(heightPt)} 0 0 cm\n/Im0 Do\nQ\n`
    );

    const pageNum = 3 + index * 3;
    const contentNum = pageNum + 1;
    const imageNum = pageNum + 2;
    pageRefs.push(pageNum);

    objects[pageNum] = encoder.encode(
      `<< /Type /Page /Parent 2 0 R` +
        ` /MediaBox [0 0 ${num(widthPt)} ${num(heightPt)}]` +
        ` /Resources << /XObject << /Im0 ${imageNum} 0 R >>` +
        ` /ProcSet [/PDF /ImageC] >>` +
        ` /Contents ${contentNum} 0 R >>`
    );

    objects[contentNum] = concatBytes([
      encoder.encode(`<< /Length ${content.length} >>\nstream\n`),
      content,
      encoder.encode("\nendstream"),
    ]);

    objects[imageNum] = concatBytes([
      encoder.encode(
        `<< /Type /XObject /Subtype /Image` +
          ` /Width ${width} /Height ${height}` +
          ` /ColorSpace /DeviceRGB /BitsPerComponent 8` +
          `${filter}/Length ${payload.length} >>\nstream\n`
      ),
      payload,
      encoder.encode("\nendstream"),
    ]);
  }

  const infoNum = 3 + canvases.length * 3;
  objects[1] = encoder.encode("<< /Type /Catalog /Pages 2 0 R >>");
  objects[2] = encoder.encode(
    `<< /Type /Pages /Kids [${pageRefs
      .map((ref) => `${ref} 0 R`)
      .join(" ")}] /Count ${pageRefs.length} >>`
  );
  objects[infoNum] = encoder.encode(
    `<< /Title ${textString(title)} /Producer ${textString(author)}` +
      ` /Creator ${textString(author)} >>`
  );

  const total = infoNum;
  const chunks = [
    // %PDF-1.7 и маркер бинарного содержимого: без него часть просмотрщиков
    // считает файл текстовым и портит поток картинки
    new Uint8Array([
      0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37, 0x0a, 0x25, 0xe2, 0xe3,
      0xcf, 0xd3, 0x0a,
    ]),
  ];

  let offset = chunks[0].length;
  const offsets = new Array(total + 1).fill(0);
  for (let objNum = 1; objNum <= total; objNum += 1) {
    const body = objects[objNum];
    if (!body) continue;
    const entry = concatBytes([
      encoder.encode(`${objNum} 0 obj\n`),
      body,
      encoder.encode("\nendobj\n"),
    ]);
    offsets[objNum] = offset;
    chunks.push(entry);
    offset += entry.length;
  }

  let xref = `xref\n0 ${total + 1}\n0000000000 65535 f \n`;
  for (let objNum = 1; objNum <= total; objNum += 1) {
    xref += `${String(offsets[objNum]).padStart(10, "0")} 00000 n \n`;
  }
  xref +=
    `trailer\n<< /Size ${total + 1} /Root 1 0 R /Info ${infoNum} 0 R >>\n` +
    `startxref\n${offset}\n%%EOF\n`;
  chunks.push(encoder.encode(xref));

  return new Blob(chunks, { type: "application/pdf" });
}
