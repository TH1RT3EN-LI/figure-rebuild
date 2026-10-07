import test from 'node:test';
import assert from 'node:assert/strict';
import {fitImagePlacement} from '../src/figure_rebuild/powerpoint/image_placement.mjs';

const source = {width: 200, height: 100};
const object = {id: 'photo', box: {x: 10, y: 20, width: 100, height: 100}};

test('default contain preserves aspect and centers with the existing frame policy', () => {
  const result = fitImagePlacement(object, source);
  assert.equal(result.fit, 'contain');
  assert.deepEqual(result.box, {x: 10, y: 45, width: 100, height: 50});
  assert.deepEqual(result.requested_box, object.box);
  assert.deepEqual(result.visible_source_box, {x: 0, y: 0, width: 200, height: 100});
});

test('crop is relative to full image pixels and contain uses the remaining aspect', () => {
  const result = fitImagePlacement({...object, crop: {left: .25, top: .1, right: .5, bottom: .4}}, source);
  assert.deepEqual(result.visible_source_box, {x: 50, y: 10, width: 50, height: 50});
  assert.deepEqual(result.box, object.box);
});

test('explicit stretch preserves the exact frame even for a narrow nonuniform source projection', () => {
  const o = {id: 'source-strip', fit: 'stretch', box: {x: 2, y: 3, width: .7, height: 164},
    crop: {left: .2, top: .1, right: .3, bottom: .2}};
  const result = fitImagePlacement(o, {width: 34, height: 236});
  assert.deepEqual(result.box, o.box);
  assert.deepEqual(result.crop, o.crop);
  assert.equal(result.visible_source_box.width, 17);
  // Fit is a placement request, never a guess at missing crop information.
  assert.deepEqual(fitImagePlacement({...o, crop: undefined}, source).crop,
    {left: 0, top: 0, right: 0, bottom: 0});
});

test('repeated source assets retain independent crops and frames without mutating input', () => {
  const first = {...object, fit: 'stretch', crop: {left: .1, top: .2, right: .3, bottom: .4}};
  const original = structuredClone(first);
  const a = fitImagePlacement(first, source);
  const b = fitImagePlacement({...object, box: {x: 200, y: 20, width: 50, height: 100}}, source);
  a.box.x = 1000; a.crop.left = .5; a.requested_box.y = 1000;
  assert.deepEqual(first, original);
  assert.deepEqual(b.box, {x: 200, y: 57.5, width: 50, height: 25});
  assert.equal(b.crop.left, 0);
});

test('unsupported fits, malformed crop, and empty native crop fail before export', () => {
  for (const fit of ['cover', 'fill', '', null, [], {}, true, 0]) {
    assert.throws(() => fitImagePlacement({...object, fit}, source), /Invalid image fit/);
  }
  for (const crop of [null, [], {}, {left: 0, top: 0, right: 0},
    {left: true, top: 0, right: 0, bottom: 0}, {left: -.1, top: 0, right: 0, bottom: 0},
    {left: NaN, top: 0, right: 0, bottom: 0}, {left: .8, top: 0, right: .2, bottom: 0},
    {left: .499996, top: 0, right: .499996, bottom: 0}]) {
    assert.throws(() => fitImagePlacement({...object, crop}, source), /crop/i);
  }
});

test('invalid image dimensions and nonfinite frames cannot reach authoring', () => {
  for (const size of [null, {width: 0, height: 1}, {width: true, height: 1}, {width: 1, height: Infinity}]) {
    assert.throws(() => fitImagePlacement(object, size), /source dimensions/);
  }
  for (const box of [null, {x: 0, y: 0, width: 0, height: 1}, {x: NaN, y: 0, width: 1, height: 1}]) {
    assert.throws(() => fitImagePlacement({...object, box}, source), /frame/);
  }
});
