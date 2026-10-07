/** Correct explicit native stroke fields in the transient preview presentation.
 * Never export this presentation as the user's editable PPTX.
 */
import {createHash} from 'node:crypto';
const sha = value => createHash('sha256').update(value).digest('hex');
export function applyStrokePreview(slide, definition) {
  if (definition?.schema_version !== 1 || definition.policy !== 'delivered-native-solid-unfilled-stroke-svg-v1' ||
      definition.preview_only !== true || definition.native_delivery_modified !== false ||
      !Array.isArray(definition.objects) || !Array.isArray(definition.unsupported)) throw Error('Invalid native stroke preview definition');
  const original = [...slide.elements.items], byName = new Map();
  for (const element of original) if (element.type === 'shape') {
    if (byName.has(element.name)) throw Error('Duplicate imported native shape name');
    byName.set(element.name, element);
  }
  const selected = new Map();
  // Validate everything before changing the preview's in-memory objects.
  for (const object of definition.objects) {
    const shape = byName.get(object.id);
    if (!shape || selected.has(shape.id) || typeof object.svg !== 'string' || sha(object.svg) !== object.svg_sha256 ||
        !Object.values(object.position ?? {}).every(Number.isFinite) ||
        Object.keys(object.position ?? {}).sort().join(',') !== 'height,left,top,width' || object.position.width <= 0 || object.position.height <= 0) throw Error('Native stroke preview identity/geometry changed: ' + object.id);
    selected.set(shape.id, object);
  }
  const desired = [];
  for (const element of original) {
    const object = selected.get(element.id);
    if (!object) { desired.push(element.id); continue; }
    const image = slide.images.add({blob: new Uint8Array(Buffer.from(object.svg)), contentType: 'image/svg+xml',
      alt: object.id, position: {...object.position}, geometry: 'rect'});
    image.lockAspectRatio = false;
    slide.elements.deleteById(element.id);
    desired.push(image.id);
  }
  // Preserve mixed image/path/text occlusion rather than appending all strokes.
  if (selected.size) for (const id of desired) slide.elements.bringToFront(id);
  if (JSON.stringify(slide.elements.items.map(e => e.id)) !== JSON.stringify(desired)) throw Error('Stroke preview changed native paint order');
  return {schema_version: 1, policy: definition.policy, preview_only: true, native_delivery_modified: false,
    applied_object_ids: definition.objects.map(o => o.id), unsupported: definition.unsupported,
    complete_mixed_paint_order_preserved: true, source_pixel_equivalence: false, application_playback_verified: false};
}
