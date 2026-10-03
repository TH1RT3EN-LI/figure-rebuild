import test from 'node:test';
import assert from 'node:assert/strict';
import {flattenPath, pathBounds} from '../../tools/figure_rebuild/curves.mjs';

const arc = [
  {moveTo: {x: 90, y: 50}},
  {cubicTo: {x1: 90, y1: 66.5685425, x2: 72.09139, y2: 80, x: 50, y: 80}},
  {lineTo: {x: 90, y: 50}}, {close: {}},
  {moveTo: {x: 65, y: 50}},
  {cubicTo: {x1: 65, y1: 41.715729, x2: 58.28427, y2: 35, x: 50, y: 35}},
  {close: {}}
];

test('bounds include the control hull, including a control beyond visible extrema', () => {
  const path = [{moveTo: {x: 10, y: 10}},
    {cubicTo: {x1: 10, y1: 90, x2: 50, y2: 90, x: 50, y: 10}}];
  assert.deepEqual(pathBounds(path), {x: 10, y: 10, width: 40, height: 80});
});

test('adaptive authoring intermediate preserves subpaths, close and endpoints', () => {
  const flattened = flattenPath(arc);
  assert.equal(flattened.filter(c => c.moveTo).length, 2);
  assert.equal(flattened.filter(c => c.close).length, 2);
  assert.deepEqual(flattened.findLast(c => c.lineTo), {lineTo: {x: 50, y: 35}});
  assert.equal(flattened.some(c => c.cubicTo), false);
  assert.equal(arc.filter(c => c.cubicTo).length, 2);
});

const pointDistance = (p, a, b) => {
  const dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length)) : 0;
  return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
};
test('dense analytic samples stay inside the requested flattening tolerance', () => {
  const input = [{moveTo: {x: 3, y: 4}},
    {cubicTo: {x1: 230, y1: 110, x2: -15, y2: 75, x: 190, y: 12}}];
  const output = flattenPath(input, .05);
  const vertices = output.map(c => c.moveTo ?? c.lineTo);
  let error = 0;
  for (let i = 0; i <= 1000; i++) {
    const t = i / 1000, u = 1 - t;
    const p = {x: u ** 3 * 3 + 3 * u * u * t * 230 + 3 * u * t * t * -15 + t ** 3 * 190,
      y: u ** 3 * 4 + 3 * u * u * t * 110 + 3 * u * t * t * 75 + t ** 3 * 12};
    error = Math.max(error, Math.min(...vertices.slice(1).map((b, j) => pointDistance(p, vertices[j], b))));
  }
  assert.ok(error < .05, 'analytic curve error: ' + error);
});

test('close resets the next cubic origin and a closed-endpoint loop still flattens', () => {
  const input = [{moveTo: {x: 0, y: 0}}, {lineTo: {x: 10, y: 0}}, {close: {}},
    {cubicTo: {x1: 30, y1: 30, x2: -30, y2: 30, x: 0, y: 0}}];
  const output = flattenPath(input);
  assert.ok(output.length > 10);
  assert.deepEqual(output.at(-1), {lineTo: {x: 0, y: 0}});
});

test('malformed, inactive, nonfinite curves and invalid tolerance are rejected', () => {
  for (const path of [[], [{cubicTo: {x1: 1, y1: 1, x2: 2, y2: 2, x: 3, y: 3}}, {close: {}}],
    [{moveTo: {x: 0, y: 0}}, {cubicTo: {x1: 1, y1: 1, x2: 2, y2: NaN, x: 3, y: 3}}]])
    assert.throws(() => flattenPath(path));
  for (const tolerance of [0, -1, Infinity, NaN]) assert.throws(() => flattenPath(arc, tolerance));
});
