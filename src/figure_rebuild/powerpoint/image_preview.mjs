/** Apply a finite sampler to actual native pictures in a transient preview.
 * The editable PPTX is never re-exported from this presentation.
 */
import {createHash} from 'node:crypto';
const digest = data => createHash('sha256').update(data).digest('hex');
export function applyImagePreview(slide, definition, scale) {
  if (![1,2].includes(definition?.schema_version) || definition.policy !== `delivered-native-picture-mupdf-device-grid-v${definition.schema_version}` ||
      definition.preview_only !== true || definition.native_delivery_modified !== false || definition.reference_pixels_used !== false ||
      ![1,2,4].includes(scale) || !Array.isArray(definition.objects) || !Array.isArray(definition.unsupported) ||
      !Array.isArray(definition.paint_order)) throw Error('Invalid native picture preview definition');
  const original = [...slide.elements.items];
  if (original.length !== definition.paint_order.length || original.some((element, i) =>
    element.type !== definition.paint_order[i].type || element.name !== definition.paint_order[i].id)) {
    throw Error('Native picture preview changed imported paint identity/order');
  }
  const byName = new Map(original.map(e => [e.name, e])), selected = new Map();
  for (const object of definition.objects) {
    const image = byName.get(object.id), preview = object.previews?.find(p => p.scale === scale);
    const validPosition = p => p && Object.keys(p).sort().join(',') === 'height,left,top,width' &&
      Object.values(p).every(v => typeof v === 'number' && Number.isFinite(v)) && p.width > 0 && p.height > 0;
    if (!image || image.type !== 'image' || selected.has(image.id) || !validPosition(object.native_position) ||
        Object.keys(object.native_position).some(k => Math.abs(image.position[k]-object.native_position[k]) > 1e-8) ||
        !validPosition(preview?.position) || !Number.isSafeInteger(preview.width) || !Number.isSafeInteger(preview.height) ||
        preview.width <= 0 || preview.height <= 0 || !Number.isSafeInteger(preview.position.left*scale) ||
        !Number.isSafeInteger(preview.position.top*scale) || preview.position.width*scale !== preview.width ||
        preview.position.height*scale !== preview.height || typeof preview.png_base64 !== 'string') {
      throw Error('Native picture preview identity/geometry changed: ' + object.id);
    }
    const bytes = Buffer.from(preview.png_base64, 'base64');
    if (bytes.toString('base64') !== preview.png_base64 || digest(bytes) !== preview.png_sha256 ||
        bytes.subarray(0,8).toString('hex') !== '89504e470d0a1a0a' || bytes.length < 24 ||
        bytes.readUInt32BE(16) !== preview.width || bytes.readUInt32BE(20) !== preview.height) {
      throw Error('Native picture preview PNG changed: ' + object.id);
    }
    selected.set(image.id, {object, preview, bytes});
  }
  const desired = [];
  for (const element of original) {
    const selectedImage = selected.get(element.id);
    if (!selectedImage) { desired.push(element.id); continue; }
    const {object, preview, bytes} = selectedImage;
    const image = slide.images.add({blob: new Uint8Array(bytes), contentType: 'image/png', alt: object.id,
      position: {...preview.position}, geometry: 'rect'});
    image.lockAspectRatio = false;
    slide.elements.deleteById(element.id); desired.push(image.id);
  }
  if (selected.size) for (const id of desired) slide.elements.bringToFront(id);
  if (JSON.stringify(slide.elements.items.map(e => e.id)) !== JSON.stringify(desired)) throw Error('Native picture preview changed paint order');
  return {scale, applied_object_ids: definition.objects.map(o => o.id), complete_mixed_paint_order_preserved: true};
}
