/** Registered-font measurement before PPT authoring; no backend dependency. */

function metricWidth(metrics) {
  const advance = metrics.width;
  if (!Number.isFinite(advance) || advance < 0) throw Error('Text measurer returned an invalid width');
  const left = Math.max(0, metrics.actualBoundingBoxLeft ?? 0);
  const right = Math.max(0, metrics.actualBoundingBoxRight ?? advance);
  return Math.max(advance, right) + left;
}

export function layoutText(text, {fontSize, width = Infinity, wrap = 'none'}, measure) {
  if (typeof text !== 'string' || !Number.isFinite(fontSize) || fontSize <= 0) throw Error('Invalid text measurement input');
  if (!(width > 0) || !['none', 'square'].includes(wrap)) throw Error('Invalid text wrapping bounds');
  const paragraphs = text.replace(/\r\n?/g, '\n').split('\n');
  const lines = [];
  const fits = candidate => metricWidth(measure(candidate)) <= width + 0.001;
  for (const paragraph of paragraphs) {
    if (wrap === 'none' || width === Infinity) {
      lines.push(paragraph);
      continue;
    }
    let current = '';
    for (const token of paragraph.match(/\S+\s*|\s+/gu) ?? []) {
      if (current && !fits(current + token.trimEnd())) {
        lines.push(current.trimEnd());
        current = '';
      }
      if (!fits(token.trimEnd())) {
        // Long Latin tokens and Chinese text may need character-level wrapping.
        // Segment graphemes so a combining sequence/emoji is never split.
        const graphemes = new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(token);
        for (const {segment} of graphemes) {
          if (current && !fits(current + segment)) {
            lines.push(current.trimEnd());
            current = '';
          }
          current += segment;
        }
      } else {
        current += token;
      }
    }
    lines.push(current.trimEnd());
  }
  const measurements = lines.map(line => measure(line));
  const sample = measure('Mg');
  const ascent = Math.max(0, sample.actualBoundingBoxAscent ?? fontSize * 0.8,
                         ...measurements.map(item => item.actualBoundingBoxAscent ?? 0));
  const descent = Math.max(0, sample.actualBoundingBoxDescent ?? fontSize * 0.2,
                          ...measurements.map(item => item.actualBoundingBoxDescent ?? 0));
  const lineHeight = Math.max(fontSize * 1.2, ascent + descent);
  // Native PPT's single-line baseline uses the font ascent plus half of its
  // default 20% leading, rather than the visible ink ascent of this string.
  const nativeBaselineAscent = Number.isFinite(sample.fontBoundingBoxAscent)
    ? sample.fontBoundingBoxAscent + fontSize * .1 : ascent + fontSize * .04;
  return {lines, line_count: lines.length, required_width: Math.max(0, ...measurements.map(metricWidth)),
          required_height: lineHeight * lines.length, line_height: lineHeight, ascent, descent,
          native_baseline_ascent: nativeBaselineAscent,
          measurement_basis: 'registered font; approximate PPT line layout; actual preview still required'};
}

export function fittedTextBox(object, measure, canvas) {
  const layout = layoutText(object.text, {fontSize: object.font_size,
    width: object.box?.width ?? Infinity, wrap: object.box ? object.wrap ?? 'none' : 'none'}, measure);
  let box = object.box;
  if (!box) {
    const width = Math.max(1, layout.required_width + object.font_size * 0.12);
    const height = layout.required_height + object.font_size * 0.16;
    const alignment = object.alignment ?? 'left';
    box = {x: object.anchor.x - (alignment === 'center' ? width / 2 : alignment === 'right' ? width : 0),
      y: object.anchor.y - layout.native_baseline_ascent, width, height};
  }
  if (layout.required_width > box.width + 0.001 || layout.required_height > box.height + 0.001) {
    throw Error(`Text overflow: ${object.id}; requires ${layout.required_width.toFixed(2)}×${layout.required_height.toFixed(2)} px, box ${box.width}×${box.height} px (${layout.line_count} lines)`);
  }
  const radians = (object.rotation ?? 0) * Math.PI / 180;
  const co = Math.cos(radians), si = Math.sin(radians);
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  for (const [dx, dy] of [[-box.width / 2, -box.height / 2], [-box.width / 2, box.height / 2],
                         [box.width / 2, -box.height / 2], [box.width / 2, box.height / 2]]) {
    const x = cx + co * dx - si * dy, y = cy + si * dx + co * dy;
    if (x < -0.001 || y < -0.001 || x > canvas.width + 0.001 || y > canvas.height + 0.001) {
      throw Error('Measured text lies outside the source canvas: ' + object.id);
    }
  }
  return {box, layout};
}
