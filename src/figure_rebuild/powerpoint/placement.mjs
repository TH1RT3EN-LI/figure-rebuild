/** Map a source figure into a page region without distorting its proportions. */
export function fitPlacement(sourceCanvas, pageCanvas, requested) {
  for (const [label, canvas] of [['Source', sourceCanvas], ['Page', pageCanvas]]) {
    if (!canvas || !['width', 'height'].every(key => Number.isFinite(canvas[key]) && canvas[key] > 0)) {
      throw Error(`${label} canvas must have positive finite width and height`);
    }
  }
  if (!Array.isArray(requested) || requested.length !== 4 || !requested.every(Number.isFinite)) {
    throw Error('Placement must contain four finite numbers: x y width height');
  }
  const [x, y, width, height] = requested;
  if (width <= 0 || height <= 0) throw Error('Placement width and height must be positive');
  if (x < 0 || y < 0 || x + width > pageCanvas.width + .001 || y + height > pageCanvas.height + .001) {
    throw Error('Requested placement is outside the page; clipping is unsupported');
  }
  const scale = Math.min(width / sourceCanvas.width, height / sourceCanvas.height);
  if (!Number.isFinite(scale) || scale <= 0) throw Error('Placement scale must be positive and finite');
  const fittedWidth = sourceCanvas.width * scale, fittedHeight = sourceCanvas.height * scale;
  return {scale, requested: [...requested], placement: [x + (width - fittedWidth) / 2,
    y + (height - fittedHeight) / 2, fittedWidth, fittedHeight]};
}
