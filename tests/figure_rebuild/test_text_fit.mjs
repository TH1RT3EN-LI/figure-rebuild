import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutText, fittedTextBox, fontRoleForText, fontFaceForText} from '../../tools/figure_rebuild/text_fit.mjs';

const measure = text => ({width: [...text].length * 10, actualBoundingBoxLeft: 0,
  actualBoundingBoxRight: [...text].length * 10, actualBoundingBoxAscent: 8,
  actualBoundingBoxDescent: 2});
const canvas = {width: 300, height: 200};

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
