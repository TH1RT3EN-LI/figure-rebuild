// Manifest validation happens before authoring; retain a local guard so this
// converter never silently turns an unsupported gradient into a solid fill.
export function linearGradientFill(style) {
  const g=style.fill_gradient;
  if(!g)return null;
  if(g.type!=='linear'||!Number.isFinite(g.angle)||g.angle<0||g.angle>=360||!Array.isArray(g.stops)||g.stops.length<2)throw Error('Invalid native linear gradient');
  let previous=-1;
  const stops=g.stops.map(stop=>{
    const offset=Math.round(stop.offset*100000),opacity=(stop.opacity??1)*(style.opacity??1);
    if(!Number.isFinite(offset)||offset<0||offset>100000||offset<=previous||!/^#[0-9a-f]{6}$/i.test(stop.color)||!Number.isFinite(opacity)||opacity<0||opacity>1)throw Error('Invalid native gradient stop');
    previous=offset;
    return {offset,color:`${stop.color}/${opacity*100}`};
  });
  if(stops[0].offset!==0||stops.at(-1).offset!==100000)throw Error('Native gradient requires complete endpoint coverage');
  return {type:'gradient',gradientKind:'linear',angleDeg:(Math.round(g.angle*60000)%21600000)/60000,stops};
}
