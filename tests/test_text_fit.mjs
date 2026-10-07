import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutText, fittedTextBox, fontRoleForText, fontFaceForText, measurePresentationBaseline, characterSpacedMeasurement} from '../src/figure_rebuild/powerpoint/text_fit.mjs';

const measure = text => ({width: [...text].length * 10, actualBoundingBoxLeft: 0,
  actualBoundingBoxRight: [...text].length * 10, actualBoundingBoxAscent: 8,
  actualBoundingBoxDescent: 2});
const canvas = {width: 300, height: 200};

test('explicit character advances are measured without word kerning and preserve the baseline', () => {
  const kerned = text => ({...measure(text), width: text === 'AV' ? 12 : measure(text).width,
    actualBoundingBoxRight: text === 'AV' ? 12 : measure(text).width});
  const o = {id: 'source-advances', text: 'AV', font_size: 10, character_spacing: [.5],
    anchor: {x: 50, y: 80}};
  const result = fittedTextBox(o, kerned, canvas);
  assert.equal(result.layout.required_width, 20.5);
  assert.equal(result.box.y + result.layout.native_baseline_ascent, 80);
  assert.deepEqual(result.layout.lines, ['AV']);
  assert.throws(() => fittedTextBox({...o, anchor: undefined, box: {x: 10, y: 10, width: 20, height: 20}}, kerned, canvas), /overflow/);
});

test('character overhangs and signed spacing participate in the measured frame', () => {
  const overhang = text => ({...measure(text), actualBoundingBoxLeft: text === 'A' ? 2 : 0,
    actualBoundingBoxRight: text === 'B' ? 14 : measure(text).width});
  const result = characterSpacedMeasurement({id: 'ink', text: 'AB', character_spacing: [-2]}, overhang);
  assert.equal(result.width, 18);
  assert.equal(result.actualBoundingBoxLeft, 2);
  assert.equal(result.actualBoundingBoxRight, 22);
  assert.throws(() => characterSpacedMeasurement({id: 'reversed', text: 'AB', character_spacing: [-10]}, measure), /reverses/);
});

test('explicit spacing rejects unsupported script, wrapping and invalid boundaries', () => {
  const o = {id: 'bad-spacing', text: 'AB', character_spacing: [0]};
  for (const patch of [{text: 'e\u0301'}, {text: 'A\nB'}, {text: '中文'}, {text: '  '},
    {wrap: 'square'}, {alignment: 'center'}, {character_spacing: []},
    {character_spacing: [NaN]}, {character_spacing: [true]}]) {
    assert.throws(() => characterSpacedMeasurement({...o, ...patch}, measure));
  }
  assert.equal(characterSpacedMeasurement({text: 'Existing'}, measure), undefined);
});

test('nowrap measures all explicit paragraphs and preserves the input text', () => {
  const input = 'AB\nCDE';
  const layout = layoutText(input, {fontSize: 10}, measure);
  assert.deepEqual(layout.lines, ['AB', 'CDE']);
  assert.equal(layout.required_width, 30);
  assert.equal(layout.required_height, 24);
  assert.equal(input, 'AB\nCDE');
});

test('square wrapping uses word boundaries and breaks an oversized token', () => {
  assert.deepEqual(layoutText('AB CD EF', {fontSize: 10, width: 50, wrap: 'square'}, measure).lines,
    ['AB CD', 'EF']);
  assert.deepEqual(layoutText('中文字符', {fontSize: 10, width: 20, wrap: 'square'}, measure).lines,
    ['中文', '字符']);
  assert.deepEqual(layoutText('ABCDE', {fontSize: 10, width: 20, wrap: 'square'}, measure).lines,
    ['AB', 'CD', 'E']);
});

test('overflow identifies the offending live-text object', () => {
  const object = {id: 'important-label', text: 'ABCDE', font_size: 10,
    box: {x: 10, y: 10, width: 40, height: 20}};
  assert.throws(() => fittedTextBox(object, measure, canvas), /Text overflow: important-label/);
  assert.throws(() => fittedTextBox({...object, wrap: 'square',
    box: {...object.box, width: 20, height: 30}}, measure, canvas), /3 lines/);
  assert.equal(fittedTextBox({...object, wrap: 'square',
    box: {...object.box, width: 20, height: 40}}, measure, canvas).layout.line_count, 3);
});

test('a glyph wider than a whole wrapping box is rejected', () => {
  assert.throws(() => fittedTextBox({id: 'wide-glyph', text: 'A', font_size: 10,
    wrap: 'square', box: {x: 0, y: 0, width: 5, height: 30}}, measure, canvas), /wide-glyph/);
});

test('baseline anchors produce measured boxes with correct alignment', () => {
  const center = fittedTextBox({id: 'anchor', text: 'AB', font_size: 10,
    anchor: {x: 150, y: 80}, alignment: 'center'}, measure, canvas);
  assert.equal(center.box.x + center.box.width / 2, 150);
  assert.equal(center.layout.required_width, 20);
  assert.throws(() => fittedTextBox({id: 'outside', text: 'AB', font_size: 10,
    anchor: {x: 0, y: 80}, alignment: 'center'}, measure, canvas), /outside the source canvas: outside/);
});

test('native baseline uses font ascent and leading rather than string ink', () => {
  const nativeMeasure = text => ({...measure(text), fontBoundingBoxAscent: 11,
    fontBoundingBoxDescent: 3});
  const result = fittedTextBox({id:'source-baseline',text:'AB',font_size:10,
    anchor:{x:50,y:80}},nativeMeasure,canvas);
  assert.equal(result.layout.native_baseline_ascent,12);
  assert.equal(result.box.y,68);
  assert.equal(result.box.y+result.layout.native_baseline_ascent,80);
});

test('rotation is checked after measuring the full box', () => {
  assert.throws(() => fittedTextBox({id: 'rotated', text: 'A', font_size: 10,
    box: {x: 0, y: 0, width: 40, height: 20}, rotation: 45}, measure, canvas), /rotated/);
});

test('rotated anchored text keeps the source baseline in slide coordinates', () => {
  const baselineOnSlide = (result, alignment, angle) => {
    const box = result.box, content = result.layout.content_box;
    const x = content.x + (alignment === 'center' ? content.width / 2 : alignment === 'right' ? content.width : 0);
    const y = content.y + result.layout.native_baseline_ascent;
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    const a = angle * Math.PI / 180;
    return [cx + Math.cos(a) * (x - cx) - Math.sin(a) * (y - cy),
      cy + Math.sin(a) * (x - cx) + Math.cos(a) * (y - cy)];
  };
  for (const rotation of [-90, 90, 37, 180]) {
    for (const alignment of ['left', 'center', 'right']) {
      const result = fittedTextBox({id: 'vertical-source-label', text: 'ABCDE', font_size: 10,
        anchor: {x: 150, y: 80}, baseline_offset: 11, rotation, alignment,
        insets: {left: 5, right: 9, top: 4, bottom: 3}}, measure, canvas);
      const point = baselineOnSlide(result, alignment, rotation);
      assert.ok(Math.abs(point[0] - 150) < 1e-10);
      assert.ok(Math.abs(point[1] - 80) < 1e-10);
    }
  }
});

test('source baseline anchoring still rejects a rotated frame outside the canvas', () => {
  assert.throws(() => fittedTextBox({id: 'outside-vertical', text: 'ABCDE', font_size: 10,
    anchor: {x: 150, y: 5}, rotation: -90, baseline_offset: 11}, measure, canvas),
    /outside the source canvas: outside-vertical/);
});

test('combining graphemes are kept together during oversized-token wrapping', () => {
  const graphemeMeasure = text => ({...measure(text), width: [...new Intl.Segmenter(undefined,
    {granularity: 'grapheme'}).segment(text)].length * 10,
    actualBoundingBoxRight: [...new Intl.Segmenter(undefined,
      {granularity: 'grapheme'}).segment(text)].length * 10});
  const layout = layoutText('e\u0301e\u0301', {fontSize: 10, width: 10, wrap: 'square'}, graphemeMeasure);
  assert.deepEqual(layout.lines, ['e\u0301', 'e\u0301']);
});

test('style selection requires the exact registered face for family and flags', () => {
  const audit = ['regular', 'bold', 'italic', 'boldItalic'].map(role => ({family: 'Real Family', role}));
  assert.equal(fontRoleForText({}), 'regular');
  assert.equal(fontRoleForText({bold: true}), 'bold');
  assert.equal(fontRoleForText({italic: true}), 'italic');
  assert.equal(fontRoleForText({bold: true, italic: true}), 'boldItalic');
  assert.equal(fontFaceForText({id: 'formula-label', italic: true, bold: true}, audit, 'Real Family'), audit[3]);
  assert.throws(() => fontFaceForText({id: 'needs-italic', italic: true}, audit.slice(0, 2), 'Real Family'), /real font face Real Family italic for needs-italic/);
  assert.throws(() => fontFaceForText({id: 'unknown', font_family: 'Other'}, audit, 'Real Family'), /Other regular/);
  assert.throws(() => fontFaceForText({id: 'duplicate'}, [...audit, audit[0]], 'Real Family'), /ambiguous/);
  assert.throws(() => fontRoleForText({id: 'bad-flags', italic: 'true'}), /boolean: bad-flags/);
});

test('explicit line height and baseline retain the calibrated source baseline', () => {
  const result = fittedTextBox({id: 'calibrated', text: 'AB\nCD', font_size: 10,
    anchor: {x: 50, y: 80}, line_height: 14, baseline_offset: 11}, measure, canvas);
  assert.equal(result.box.y, 69);
  assert.equal(result.layout.native_baseline_ascent, 11);
  assert.equal(result.layout.line_height, 14);
  assert.equal(result.layout.required_height, 28);
  assert.equal(result.layout.baseline_basis, 'explicit_calibrated_offset');
  assert.equal(result.layout.line_height_basis, 'explicit_pixel_value');
  const shifted = layoutText('AB\nCD', {fontSize: 10, lineHeight: 12, baselineOffset: 20}, measure);
  assert.equal(shifted.required_height, 34);
});

test('insets reduce wrapping area and are included in overflow checks', () => {
  const object = {id: 'inset-label', text: 'AB CD', font_size: 10, wrap: 'square',
    box: {x: 20, y: 20, width: 60, height: 40}, insets: {left: 15, right: 15, top: 4, bottom: 5}};
  const result = fittedTextBox(object, measure, canvas);
  assert.deepEqual(result.layout.lines, ['AB', 'CD']);
  assert.deepEqual(result.layout.content_box, {x: 35, y: 24, width: 30, height: 31});
  assert.throws(() => fittedTextBox({...object, box: {...object.box, height: 30}}, measure, canvas), /content box 30×21/);
  assert.throws(() => fittedTextBox({...object, insets: {left: 30, right: 30}}, measure, canvas), /no content width: inset-label/);
});

test('baseline anchored boxes align the content even with asymmetric insets', () => {
  for (const alignment of ['left', 'center', 'right']) {
    const result = fittedTextBox({id: 'inset-anchor', text: 'AB', font_size: 10,
      anchor: {x: 150, y: 80}, alignment, baseline_offset: 11,
      insets: {left: 5, right: 9, top: 4, bottom: 3}}, measure, canvas);
    const content = result.layout.content_box;
    const alignmentOffset = alignment === 'center' ? content.width / 2 : alignment === 'right' ? content.width : 0;
    assert.equal(content.x + alignmentOffset, 150);
    assert.equal(content.y + result.layout.native_baseline_ascent, 80);
    assert.equal(result.box.y, 65);
  }
});

test('invalid calibrated metrics and insets fail without silently clamping', () => {
  const object = {id: 'invalid-metrics', text: 'AB', font_size: 10, anchor: {x: 50, y: 80}};
  for (const values of [{line_height: 0}, {line_height: Infinity}, {line_height: '12'},
    {baseline_offset: -1}, {baseline_offset: NaN}, {insets: {left: -1}},
    {insets: {top: '4'}}, {insets: {unrecognized: 2}}, {insets: []}, {insets: null}]) {
    assert.throws(() => fittedTextBox({...object, ...values}, measure, canvas));
  }
});

function rendererOptions(scale = 1) {
  const ascentEm = 0.921630859375; // Audited Arial hhea ascent plus half line gap.
  const context = {font: '', measureText() {
    const size = Number(this.font.match(/([\d.]+)px/u)[1]);
    return {fontBoundingBoxAscent: size * ascentEm, lines: [{runs: [{family: 'Arial'}]}]};
  }};
  return {scale, defaultFamily: 'Arial', context,
    fontMetricsProvider: {getMetricsForSize(_font, size) { return {ascentPx: size * ascentEm}; }},
    paintBaselineCompensation: value => value - Math.round(value)};
}

test('explicit 1.2em uses the same percentage baseline as native default spacing', () => {
  const object = {text: 'Hgx09', font_size: 40};
  const natural = measurePresentationBaseline(object, rendererOptions());
  const exact = measurePresentationBaseline({...object, line_height: 48}, rendererOptions());
  assert.equal(natural.line_height_px, exact.line_height_px);
  assert.equal(natural.first_baseline_px, exact.first_baseline_px);
  assert.equal(exact.spacing, 'percent_of_natural_line');
  assert.equal(exact.spacing_thousandths_percent, 100000);
});

test('source anchor and calibrated box retain first baseline independently of line pitch', () => {
  const sourceMeasure = text => ({...measure(text), fontBoundingBoxAscent: 18.10546875});
  for (const height of [undefined, 20, 24]) {
    const object = {id: 'rendered', text: 'Hgx09\nHgx09', font_size: 20,
      anchor: {x: 50, y: 80}, baseline_offset: 23, insets: {top: 3, bottom: 7},
      ...(height === undefined ? {} : {line_height: height})};
    const rendererBaseline = measurePresentationBaseline(object, rendererOptions());
    const result = fittedTextBox(object, sourceMeasure, canvas, {rendererBaseline});
    assert.equal(result.box.y + 3 + result.layout.baseline_adjustment_px
      + rendererBaseline.first_baseline_px, 80);
    assert.equal(result.layout.native_baseline_ascent, 23);
    assert.equal(result.layout.line_count, 2);
    assert.ok(Math.abs(result.layout.line_height - (height ?? 24)) <= 24 * .000005);
    const box = fittedTextBox({...object, anchor: undefined, box: {x: 50, y: 30, width: 100, height: 90}},
      sourceMeasure, canvas, {rendererBaseline});
    assert.equal(box.box.y, 30);
    assert.equal(box.layout.baseline_adjustment_px + rendererBaseline.first_baseline_px, 23);
  }
});

test('renderer uses physical PPT rounding then returns placement-scaled source coordinates', () => {
  const object = {text: 'Hgx09', font_size: 13.137, line_height: 15.741};
  const scale = 0.637;
  const rendered = measurePresentationBaseline(object, rendererOptions(scale));
  assert.equal(rendered.rendered_font_size_px, Math.round(object.font_size * scale * 75) / 75 / scale);
  assert.equal(rendered.requested_line_height_px, object.line_height);
  assert.ok(Math.abs(rendered.line_height_px
    - rendered.natural_line_height_px * rendered.spacing_thousandths_percent / 100000) < 1e-12);
  assert.ok(Math.abs(rendered.line_height_px - rendered.requested_line_height_px)
    <= rendered.natural_line_height_px * .000005);
  assert.equal(rendered.scale, scale);
});

test('native default baseline respects supplied Office metrics and missing APIs fail clearly', () => {
  const options = rendererOptions();
  options.fontMetricsProvider.getMetricsForSize = () => ({ascentPx: 18, officeAscentPx: 18, officeDescentPx: 6});
  const result = measurePresentationBaseline({text: 'AB', font_size: 20}, options);
  assert.equal(result.first_baseline_px, 24 * 18 / 24 + result.paint_baseline_correction_px);
  assert.throws(() => measurePresentationBaseline({text: 'AB', font_size: 20}, {}), /lacks.*baseline API/u);
});
