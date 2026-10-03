import test from 'node:test';
import assert from 'node:assert/strict';
import {fitPlacement} from '../src/figure_rebuild/powerpoint/placement.mjs';

const source = {width: 800, height: 400}, page = {width: 1200, height: 800};

test('a wide figure fits a tall region uniformly with vertical centering', () => {
  const region = [100, 100, 400, 400];
  assert.deepEqual(fitPlacement(source, page, region), {
    scale: .5, requested: region, placement: [100, 200, 400, 200]
  });
  assert.deepEqual(region, [100, 100, 400, 400]);
});

test('a tall figure centers horizontally and a smaller figure can be enlarged', () => {
  assert.deepEqual(fitPlacement({width: 400, height: 800}, page, [100, 100, 600, 400]).placement,
    [300, 100, 200, 400]);
  assert.deepEqual(fitPlacement({width: 200, height: 100}, page, [0, 0, 1000, 600]), {
    scale: 5, requested: [0, 0, 1000, 600], placement: [0, 50, 1000, 500]
  });
});

test('standalone figure placement preserves its original size and origin', () => {
  assert.deepEqual(fitPlacement(source, source, [0, 0, 800, 400]).placement, [0, 0, 800, 400]);
});

test('invalid requests fail even if a fitted figure would fall inside the page', () => {
  for (const region of [[-1, 0, 100, 100], [0, -1, 100, 100], [0, 0, 0, 100],
    [0, 0, 100, -1], [1190, 0, 20, 20], [0, 799, 20, 20], [0, 0, 1400, 100],
    [0, 0, NaN, 100], [0, 0, Infinity, 100], [true, 0, 100, 100], [0, 0, 100], null]) {
    assert.throws(() => fitPlacement(source, page, region));
  }
});

test('source and target canvases require finite positive dimensions', () => {
  for (const canvas of [{width: 0, height: 10}, {width: 10, height: NaN},
    {width: true, height: 10}, {width: 10, height: Infinity}, null]) {
    assert.throws(() => fitPlacement(canvas, page, [0, 0, 100, 100]));
    assert.throws(() => fitPlacement(source, canvas, [0, 0, 100, 100]));
  }
});
