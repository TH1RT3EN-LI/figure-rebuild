/** Registered-font measurement before PPT authoring; no backend dependency. */

/** Select a registered real face, never a synthetic italic/bold substitute. */
export function fontRoleForText(object) {
  for (const flag of ['bold', 'italic']) {
    if (flag in object && typeof object[flag] !== 'boolean') throw Error(`Text ${flag} must be boolean: ${object.id}`);
  }
  return object.italic ? (object.bold ? 'boldItalic' : 'italic') : (object.bold ? 'bold' : 'regular');
}

export function fontFaceForText(object, fontAudit, defaultFamily) {
  const family = object.font_family ?? defaultFamily;
  const role = fontRoleForText(object);
  const matches = fontAudit.filter(face => face.family === family && face.role === role);
  if (matches.length !== 1) throw Error(`Missing or ambiguous real font face ${family} ${role} for ${object.id}; no synthetic style or fallback permitted`);
  return matches[0];
}

function finiteMetric(value, name, allowZero = false) {
  if (typeof value !== 'number' || !Number.isFinite(value) || (allowZero ? value < 0 : value <= 0)) {
    throw Error(`Text ${name} must be finite and ${allowZero ? 'nonnegative' : 'positive'}`);
  }
  return value;
}

function textInsets(value = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(key => !['left', 'right', 'top', 'bottom'].includes(key))) {
    throw Error('Text insets needs only left/right/top/bottom pixel values');
  }
  return Object.fromEntries(['left', 'right', 'top', 'bottom'].map(key =>
    [key, finiteMetric(value[key] ?? 0, 'insets.' + key, true)]));
}

function metricWidth(metrics) {
  const advance = metrics.width;
  if (!Number.isFinite(advance) || advance < 0) throw Error('Text measurer returned an invalid width');
  const left = Math.max(0, metrics.actualBoundingBoxLeft ?? 0);
  const right = Math.max(0, metrics.actualBoundingBoxRight ?? advance);
  return Math.max(advance, right) + left;
}

/**
 * Read the presentation renderer's metrics, separately from the source-font
 * measurer. The two Canvas implementations do not include font line gaps in
 * the same way. Values are returned in source pixels after PPT point rounding.
 *
 * Artifact presentation layout uses a 1.2-em natural line box. Its exact-point
 * and default-line-spacing branches have different first baselines. The paint
 * adjustment is also part of that renderer's exported metrics API;
 * do not substitute a fitted constant or the visible ink height for it.
 */
export function measurePresentationBaseline(object, {context, fontMetricsProvider,
  paintBaselineCompensation, scale = 1, defaultFamily}) {
  if (typeof context?.measureText !== 'function'
      || typeof fontMetricsProvider?.getMetricsForSize !== 'function'
      || typeof paintBaselineCompensation !== 'function') {
    throw Error('Configured Artifact Tool lacks the presentation font metrics/baseline API required for faithful text placement');
  }
  finiteMetric(scale, 'render scale');
  const px = Math.round(object.font_size * scale * 75) / 75;
  finiteMetric(px, 'rendered font size');
  const family = object.font_family ?? defaultFamily;
  if (typeof family !== 'string' || !family) throw Error('Renderer text requires a registered font family');
  const font = {family: JSON.stringify(family), style: object.italic ? 'italic' : 'normal',
    weight: object.bold ? '700' : '400'};
  context.font = `${font.style} ${font.weight} ${px}px ${font.family}`;
  context.textBaseline = 'alphabetic';
  const metrics = fontMetricsProvider.getMetricsForSize(font, px);
  const ascent = finiteMetric(metrics.ascentPx, 'renderer font ascent');
  const naturalHeight = px * 1.2;
  const lineHeight = object.line_height === undefined ? naturalHeight
    : Math.round(finiteMetric(object.line_height, 'line_height') * scale * 75) / 75;
  finiteMetric(lineHeight, 'rendered line height');
  let baseline = px;
  if (object.line_height !== undefined) baseline = ascent + (lineHeight - naturalHeight) / 2;
  else if (Number.isFinite(metrics.officeAscentPx) && metrics.officeAscentPx > 0
      && Number.isFinite(metrics.officeDescentPx) && metrics.officeDescentPx > 0) {
    baseline = naturalHeight * metrics.officeAscentPx / (metrics.officeAscentPx + metrics.officeDescentPx);
  }
  const ink = context.measureText(object.text.split(/\r\n?|\n/u)[0] || 'Mg');
  // Artifact applies this correction only for a single resolved font face.
  const runs = (ink.lines ?? []).flatMap(line => line.runs ?? []);
  const families = new Set(runs.map(run => run.family?.trim().toLocaleLowerCase()).filter(Boolean));
  const correction = families.size === 1 && Number.isFinite(ink.fontBoundingBoxAscent)
    ? paintBaselineCompensation(ink.fontBoundingBoxAscent) : 0;
  if (!Number.isFinite(correction)) throw Error('Renderer returned an invalid paint baseline correction');
  return {model: 'artifact_presentation_v1', first_baseline_px: (baseline + correction) / scale,
    line_height_px: lineHeight / scale, natural_line_height_px: naturalHeight / scale,
    font_ascent_px: ascent / scale, paint_baseline_correction_px: correction / scale,
    rendered_font_size_px: px / scale, scale,
    spacing: object.line_height === undefined ? 'default' : 'exact_points'};
}

export function layoutText(text, {fontSize, width = Infinity, wrap = 'none', lineHeight: explicitLineHeight,
  baselineOffset: explicitBaselineOffset, rendererBaseline}, measure) {
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
  let lineHeight = explicitLineHeight === undefined ? Math.max(fontSize * 1.2, ascent + descent)
    : finiteMetric(explicitLineHeight, 'line_height');
  // This is the source frame's default baseline contract, not the renderer's
  // baseline. Keep it independent of explicit line pitch and preserve it with
  // the measured native inset correction below.
  const defaultNativeBaselineAscent = Number.isFinite(sample.fontBoundingBoxAscent)
    ? sample.fontBoundingBoxAscent + fontSize * .1 : ascent + fontSize * .04;
  const nativeBaselineAscent = explicitBaselineOffset === undefined
    ? defaultNativeBaselineAscent
    : finiteMetric(explicitBaselineOffset, 'baseline_offset', true);
  let rendererLayout = {};
  if (rendererBaseline !== undefined) {
    if (!rendererBaseline || rendererBaseline.model !== 'artifact_presentation_v1') {
      throw Error('Text needs supported renderer baseline metrics');
    }
    const actualBaseline = finiteMetric(rendererBaseline.first_baseline_px, 'renderer first baseline', true);
    lineHeight = finiteMetric(rendererBaseline.line_height_px, 'renderer line height');
    rendererLayout = {renderer_baseline: rendererBaseline,
      baseline_adjustment_px: nativeBaselineAscent - actualBaseline};
  }
  const requiredHeight = explicitBaselineOffset === undefined ? lineHeight * lines.length
    : Math.max(lineHeight * lines.length, nativeBaselineAscent + descent + (lines.length - 1) * lineHeight);
  return {lines, line_count: lines.length, required_width: Math.max(0, ...measurements.map(metricWidth)),
          required_height: requiredHeight, line_height: lineHeight, ascent, descent,
          native_baseline_ascent: nativeBaselineAscent,
          default_native_baseline_ascent: defaultNativeBaselineAscent,
          baseline_basis: explicitBaselineOffset === undefined ? 'font_metrics_and_native_leading' : 'explicit_calibrated_offset',
          line_height_basis: explicitLineHeight === undefined ? 'default_measured_leading' : 'explicit_pixel_value',
          ...rendererLayout,
          measurement_basis: 'registered font; approximate PPT line layout; actual preview still required'};
}

export function fittedTextBox(object, measure, canvas, {rendererBaseline} = {}) {
  const insets = textInsets(object.insets);
  const contentWidth = object.box ? object.box.width - insets.left - insets.right : Infinity;
  if (!(contentWidth > 0)) throw Error('Text insets leave no content width: ' + object.id);
  const layout = layoutText(object.text, {fontSize: object.font_size,
    width: contentWidth, wrap: object.box ? object.wrap ?? 'none' : 'none',
    lineHeight: object.line_height, baselineOffset: object.baseline_offset, rendererBaseline}, measure);
  let box = object.box;
  if (!box) {
    const width = Math.max(1, layout.required_width + object.font_size * 0.12);
    const height = layout.required_height + object.font_size * 0.16;
    const alignment = object.alignment ?? 'left';
    box = {x: object.anchor.x - insets.left - (alignment === 'center' ? width / 2 : alignment === 'right' ? width : 0),
      y: object.anchor.y - insets.top - layout.native_baseline_ascent,
      width: width + insets.left + insets.right, height: height + insets.top + insets.bottom};
  }
  const contentBox = {x: box.x + insets.left, y: box.y + insets.top,
    width: box.width - insets.left - insets.right, height: box.height - insets.top - insets.bottom};
  if (layout.required_width > contentBox.width + 0.001 || layout.required_height > contentBox.height + 0.001) {
    throw Error(`Text overflow: ${object.id}; requires ${layout.required_width.toFixed(2)}×${layout.required_height.toFixed(2)} px, content box ${contentBox.width}×${contentBox.height} px (${layout.line_count} lines)`);
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
  return {box, layout: {...layout, content_box: contentBox, insets}};
}
