import test from 'node:test';
import assert from 'node:assert/strict';
import {linearGradientFill} from '../src/figure_rebuild/powerpoint/linear_gradient.mjs';

test('native gradient directions, stop precision and composed opacity',()=>{
  for(const angle of [0,90,180,270]){
    const fill=linearGradientFill({opacity:.5,fill_gradient:{type:'linear',angle,stops:[
      {offset:0,color:'#000000'}, {offset:.123456,color:'#abcdef',opacity:.4}, {offset:1,color:'#ffffff'}]}});
    assert.equal(fill.angleDeg,angle);
    assert.deepEqual(fill.stops,[{offset:0,color:'#000000/50'},{offset:12346,color:'#abcdef/20'},{offset:100000,color:'#ffffff/50'}]);
  }
});
test('existing solid fill is untouched and invalid gradient fails closed',()=>{
  assert.equal(linearGradientFill({fill:'#123456'}),null);
  assert.throws(()=>linearGradientFill({fill_gradient:{type:'linear',angle:360,stops:[]}}));
  assert.throws(()=>linearGradientFill({fill_gradient:{type:'linear',angle:0,stops:[
    {offset:0,color:'#000000'},{offset:.000001,color:'#ffffff'},{offset:1,color:'#000000'}]}}));
});
test('oblique angle preserves native 1/60000 degree quantization',()=>{
  const fill=linearGradientFill({fill_gradient:{type:'linear',angle:30.123456,stops:[{offset:0,color:'#000000'},{offset:1,color:'#ffffff'}]}});
  assert.equal(fill.angleDeg,1807407/60000);
});
