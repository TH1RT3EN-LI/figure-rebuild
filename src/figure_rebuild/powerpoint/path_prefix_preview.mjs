/** Replay a finite opaque grid of an actual native filled-path prefix. */
import {createHash} from 'node:crypto';
export function applyPathPrefixPreview(slide, definition, scale) {
  if (definition?.schema_version !== 1 || definition.policy !== 'delivered-native-filled-path-prefix-grid-v1' ||
      definition.preview_only !== true || definition.native_delivery_modified !== false || definition.reference_pixels_used !== false ||
      ![1,2,4].includes(scale) || !Array.isArray(definition.paint_order) || !Array.isArray(definition.objects) ||
      !Array.isArray(definition.request?.object_ids) || !definition.request.object_ids.length) throw Error('Invalid native path-prefix preview');
  const original = [...slide.elements.items], ids = definition.request.object_ids;
  if (original.length !== definition.paint_order.length || original.some((e,i) =>
      e.name !== definition.paint_order[i].id || e.type !== definition.paint_order[i].type) ||
      ids.length !== definition.objects.length || ids.some((id,i) => original[i]?.name !== id ||
        original[i]?.type !== 'shape' || definition.objects[i]?.id !== id) || new Set(ids).size !== ids.length) {
    throw Error('Native path-prefix changed complete mixed paint identity/order');
  }
  const p = definition.previews?.find(p => p.scale === scale);
  if (!p || Object.keys(p.position ?? {}).sort().join(',') !== 'height,left,top,width' ||
      !Object.values(p.position).every(Number.isFinite) || !Number.isSafeInteger(p.width) || !Number.isSafeInteger(p.height) ||
      p.width <= 0 || p.height <= 0 || !Number.isSafeInteger(p.position.left*scale) || !Number.isSafeInteger(p.position.top*scale) ||
      p.position.width*scale !== p.width || p.position.height*scale !== p.height || typeof p.png_base64 !== 'string') {
    throw Error('Native path-prefix target grid changed');
  }
  const bytes = Buffer.from(p.png_base64, 'base64');
  if (bytes.toString('base64') !== p.png_base64 || createHash('sha256').update(bytes).digest('hex') !== p.png_sha256 ||
      bytes.length < 24 || bytes.subarray(0,8).toString('hex') !== '89504e470d0a1a0a' ||
      bytes.readUInt32BE(16) !== p.width || bytes.readUInt32BE(20) !== p.height) throw Error('Native path-prefix PNG changed');
  const image = slide.images.add({blob:new Uint8Array(bytes), contentType:'image/png', alt:'native-path-prefix-preview',
    position:{...p.position}, geometry:'rect'});
  image.lockAspectRatio = false;
  for (const e of original.slice(0, ids.length)) slide.elements.deleteById(e.id);
  const desired = [image.id, ...original.slice(ids.length).map(e => e.id)];
  for (const id of desired) slide.elements.bringToFront(id);
  if (JSON.stringify(slide.elements.items.map(e => e.id)) !== JSON.stringify(desired)) throw Error('Native path-prefix changed mixed paint order');
  return {scale, applied_object_ids:[...ids], complete_mixed_paint_order_preserved:true};
}
