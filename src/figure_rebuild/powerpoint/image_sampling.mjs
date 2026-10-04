/** Bound CPU image minification without changing source bytes or placement.
 * Skia Canvas 3.0.8 uses the same local interpolation for low/medium/high.
 * Repeated at-most-2x reductions retain thin ink that a single large reduction
 * can miss. This is a finite sampling policy, not a pixel-equivalence proof.
 */
const installed = new WeakMap();
export const IMAGE_SAMPLING_POLICY = 'full-source-staged-minification-v1';
const MAX_STAGE_PIXELS = 16_000_000;
const MAX_DRAW_PIXELS = 24_000_000;
const MAX_STAGES = 16;

// Largest singular value of the source-pixel -> device-pixel linear map.
// Rotation and shear must not turn an apparently small column into magnification.
function largestScale(a, b, c, d) {
  const scale = Math.max(Math.abs(a), Math.abs(b), Math.abs(c), Math.abs(d));
  if (!Number.isFinite(scale) || scale === 0) return scale;
  a /= scale; b /= scale; c /= scale; d /= scale;
  const aa = a*a + b*b, cc = c*c + d*d, ac = a*c + b*d;
  return scale * Math.sqrt((aa + cc + Math.hypot(aa - cc, 2*ac)) / 2);
}

export function planImageMinification(width, height, args, transform) {
  if (![width, height].every(value => Number.isSafeInteger(value) && value > 0) ||
      !Array.isArray(args) || ![2, 4, 8].includes(args.length) ||
      !args.every(value => typeof value === 'number' && Number.isFinite(value))) return null;
  let sx = 0, sy = 0, sw = width, sh = height, dx, dy, dw, dh;
  if (args.length === 2) { [dx, dy] = args; dw = width; dh = height; }
  else if (args.length === 4) [dx, dy, dw, dh] = args;
  else [sx, sy, sw, sh, dx, dy, dw, dh] = args;
  if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0 ||
      !transform || !['a', 'b', 'c', 'd'].every(key =>
        typeof transform[key] === 'number' && Number.isFinite(transform[key]))) return null;
  const {a, b, c, d} = transform;
  const scaleAt = (w, h) => largestScale(a*dw/sw*width/w, b*dw/sw*width/w,
    c*dh/sh*height/h, d*dh/sh*height/h);
  const initialScale = scaleAt(width, height);
  if (!(initialScale > 0 && initialScale <= .5)) return null;
  // Filtering the complete source before cropping can mix ink outside the
  // requested source rectangle into its edge. Keep cropped calls untouched.
  if (sx !== 0 || sy !== 0 || sw !== width || sh !== height) return {cropped: true};
  let w = width, h = height, pixels = 0;
  const stages = [];
  while (scaleAt(w, h) <= .5 && (w > 1 || h > 1)) {
    const nw = Math.ceil(w/2), nh = Math.ceil(h/2), cost = nw*nh;
    if (stages.length >= MAX_STAGES || nw > 32768 || nh > 32768 ||
        cost > MAX_STAGE_PIXELS || pixels + cost > MAX_DRAW_PIXELS) {
      throw Error('Image minification exceeds the bounded staging budget');
    }
    stages.push({width: nw, height: nh});
    pixels += cost; w = nw; h = nh;
  }
  return stages.length ? {stages, pixels, destination: [dx, dy, dw, dh]} : null;
}

export function installImageMinification(Canvas) {
  if (typeof Canvas !== 'function' || typeof Canvas.prototype.getContext !== 'function') {
    throw Error('Image minification requires the CPU Canvas getContext API');
  }
  if (installed.has(Canvas)) {
    const result = installed.get(Canvas);
    if (Canvas.prototype.getContext !== result.getContext) {
      throw Error('Image minification context adapter was replaced after installation');
    }
    return result;
  }
  const nativeGetContext = Canvas.prototype.getContext;
  const contexts = new WeakSet();
  let draws = 0, filtered = 0, cropped = 0, stages = 0, pixels = 0;
  let maxStages = 0, maxDrawPixels = 0;
  function getContext(...args) {
    const context = nativeGetContext.apply(this, args);
    if (!context || contexts.has(context)) return context;
    if (typeof context.drawImage !== 'function' || typeof context.getTransform !== 'function') {
      throw Error('Image minification requires native drawImage and getTransform');
    }
    const nativeDrawImage = context.drawImage;
    context.drawImage = function(source, ...coordinates) {
      draws += 1;
      const plan = this.imageSmoothingEnabled === true && source ?
        planImageMinification(source.width, source.height, coordinates, this.getTransform()) : null;
      if (!plan || plan.cropped) {
        if (plan?.cropped) cropped += 1;
        return nativeDrawImage.call(this, source, ...coordinates);
      }
      let image = source, width = source.width, height = source.height;
      // No persistent image cache: both Canvas pixels and Image.src can change.
      // Each draw consumes the current decoded source, including its alpha.
      for (const stage of plan.stages) {
        const canvas = new Canvas(stage.width, stage.height);
        const intermediate = nativeGetContext.call(canvas, '2d');
        if (!intermediate) throw Error('Image minification could not acquire a staging context');
        intermediate.imageSmoothingEnabled = true;
        nativeDrawImage.call(intermediate, image, 0, 0, width, height,
          0, 0, stage.width, stage.height);
        image = canvas; width = stage.width; height = stage.height;
      }
      const result = nativeDrawImage.call(this, image, 0, 0, width, height, ...plan.destination);
      filtered += 1; stages += plan.stages.length; pixels += plan.pixels;
      maxStages = Math.max(maxStages, plan.stages.length);
      maxDrawPixels = Math.max(maxDrawPixels, plan.pixels);
      return result;
    };
    contexts.add(context);
    return context;
  }
  Canvas.prototype.getContext = getContext;
  const result = {getContext, audit: () => ({
    policy: IMAGE_SAMPLING_POLICY, scope: 'complete-source-window-only',
    method: 'successive_at_most_twofold_native_cpu_canvas_reductions',
    draw_calls: draws, filtered_draw_calls: filtered, cropped_minification_calls_unfiltered: cropped,
    staging_canvas_count: stages, staging_pixels_processed: pixels,
    max_stages_per_draw: maxStages, max_staging_pixels_per_draw: maxDrawPixels,
    limits: {max_stage_pixels: MAX_STAGE_PIXELS, max_draw_pixels: MAX_DRAW_PIXELS, max_stages: MAX_STAGES},
    persistent_image_cache: false, source_media_bytes_modified: false,
    source_window_extent_preserved: true, destination_coordinates_modified: false,
    explicit_smoothing_disabled_preserved: true,
    rgb_alpha_error_bound_proved: false, visual_verification_required: true,
  })};
  installed.set(Canvas, result);
  return result;
}
