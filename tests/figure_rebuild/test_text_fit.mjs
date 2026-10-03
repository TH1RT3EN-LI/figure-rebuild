import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutText, fittedTextBox} from '../../tools/figure_rebuild/text_fit.mjs';

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
