/** Resolve an image's cropped source pixels into its requested source-space box.
 *
 * `contain` preserves the cropped image's aspect ratio and centers it (default).
 * `stretch` maps the cropped source to the exact box, including nonuniform scale.
 * Crop fractions always refer to the complete, unchanged image bytes. Selecting
 * stretch does not repair an incorrect source crop or infer PDF clipping.
 */
export function fitImagePlacement(object, sourceSize) {
  const label = object?.id ?? '(unnamed image)';
  const fail = message => { throw Error(`${message}: ${label}`); };
  const fit = object?.fit === undefined ? 'contain' : object.fit;
  if (fit !== 'contain' && fit !== 'stretch') fail('Invalid image fit');
  const requested = object?.box;
  if (!requested || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(requested[key])) ||
      requested.width <= 0 || requested.height <= 0) fail('Invalid image frame');
  if (!sourceSize || !['width', 'height'].every(key => Number.isFinite(sourceSize[key]) && sourceSize[key] > 0)) {
    fail('Invalid image source dimensions');
  }
  const crop = object.crop === undefined ? {left: 0, top: 0, right: 0, bottom: 0} : object.crop;
  const edges = ['left', 'top', 'right', 'bottom'];
  if (!crop || Array.isArray(crop) || Object.keys(crop).length !== 4 ||
      !edges.every(key => Number.isFinite(crop[key]) && crop[key] >= 0 && crop[key] < 1) ||
      crop.left + crop.right >= 1 || crop.top + crop.bottom >= 1) fail('Invalid image crop');
  if (Math.round(crop.left * 100000) + Math.round(crop.right * 100000) >= 100000 ||
      Math.round(crop.top * 100000) + Math.round(crop.bottom * 100000) >= 100000) {
    fail('Image crop quantization leaves no source area');
  }
  const visible = {x: sourceSize.width * crop.left, y: sourceSize.height * crop.top,
    width: sourceSize.width * (1 - crop.left - crop.right),
    height: sourceSize.height * (1 - crop.top - crop.bottom)};
  const box = {...requested};
  if (fit === 'contain') {
    const scale = Math.min(requested.width / visible.width, requested.height / visible.height);
    box.width = visible.width * scale;
    box.height = visible.height * scale;
    box.x += (requested.width - box.width) / 2;
    box.y += (requested.height - box.height) / 2;
  }
  if (!Object.values(visible).every(Number.isFinite) || visible.width <= 0 || visible.height <= 0 ||
      !['x', 'y', 'width', 'height'].every(key => Number.isFinite(box[key])) || box.width <= 0 || box.height <= 0) {
    fail('Image placement exceeds numeric range');
  }
  return {fit, box, requested_box: {...requested}, crop: {...crop},
    source_size: {width: sourceSize.width, height: sourceSize.height}, visible_source_box: visible};
}
