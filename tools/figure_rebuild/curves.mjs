/** Editable cubic paths use global source coordinates in the scene manifest.
 * Artifact Tool authoring uses a bounded flattening only as an intermediate;
 * postprocess.py restores the exact cubic segments in the delivered OOXML.
 */
const POINT_KEYS = ['x', 'y'];
const CUBIC_KEYS = ['x1', 'y1', 'x2', 'y2', 'x', 'y'];

function finiteRecord(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length
    && keys.every(key => Number.isFinite(value[key]));
}

function checkedCommands(commands) {
  if (!Array.isArray(commands) || commands.length < 2 || commands.length > 200000)
    throw Error('Invalid path command count');
  let active = false;
  for (const command of commands) {
    if (!command || typeof command !== 'object' || Object.keys(command).length !== 1)
      throw Error('Invalid path command');
    const [op, point] = Object.entries(command)[0];
    if (op === 'moveTo' || op === 'lineTo') {
      if (!finiteRecord(point, POINT_KEYS)) throw Error('Invalid path point');
      if (op === 'moveTo') active = true;
      else if (!active) throw Error('lineTo before moveTo');
    } else if (op === 'cubicTo') {
      if (!active || !finiteRecord(point, CUBIC_KEYS)) throw Error('Invalid cubicTo');
    } else if (op === 'close') {
      if (!active || !finiteRecord(point, [])) throw Error('Invalid close');
    } else throw Error('Unsupported path command: ' + op);
  }
  return commands;
}

/** Conservative control hull, shared with the final native-path coordinate map. */
export function pathBounds(commands) {
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  const include = (x, y) => {
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x); bottom = Math.max(bottom, y);
  };
  for (const command of checkedCommands(commands)) {
    if (command.moveTo || command.lineTo) {
      const point = command.moveTo ?? command.lineTo; include(point.x, point.y);
    } else if (command.cubicTo) {
      const point = command.cubicTo;
      include(point.x1, point.y1); include(point.x2, point.y2); include(point.x, point.y);
    }
  }
  return {x: left, y: top, width: Math.max(.01, right - left), height: Math.max(.01, bottom - top)};
}

const midpoint = (a, b) => ({x: (a.x + b.x) / 2, y: (a.y + b.y) / 2});
function distanceToSegment(point, start, end) {
  const dx = end.x - start.x, dy = end.y - start.y, length2 = dx * dx + dy * dy;
  const t = length2 ? Math.max(0, Math.min(1,
    ((point.x - start.x) * dx + (point.y - start.y) * dy) / length2)) : 0;
  return Math.hypot(point.x - start.x - t * dx, point.y - start.y - t * dy);
}

/** Adaptive De Casteljau subdivision; tolerance is in source pixels. */
export function flattenPath(commands, tolerance = .08) {
  if (!Number.isFinite(tolerance) || tolerance <= 0) throw Error('Curve tolerance must be positive');
  const flattened = [];
  function append(command) {
    if (flattened.length >= 200000) throw Error('Flattened path exceeds 200000 commands');
    flattened.push(command);
  }
  function subdivide(p0, p1, p2, p3, depth) {
    const error = Math.max(distanceToSegment(p1, p0, p3), distanceToSegment(p2, p0, p3));
    if (error <= tolerance) { append({lineTo: {...p3}}); return; }
    if (depth >= 24) throw Error('Cubic flattening could not satisfy tolerance');
    const a = midpoint(p0, p1), b = midpoint(p1, p2), c = midpoint(p2, p3);
    const d = midpoint(a, b), e = midpoint(b, c), f = midpoint(d, e);
    subdivide(p0, a, d, f, depth + 1); subdivide(f, e, c, p3, depth + 1);
  }
  let previous = null, start = null;
  for (const command of checkedCommands(commands)) {
    if (command.moveTo) { start = previous = {...command.moveTo}; append({moveTo: {...previous}}); }
    else if (command.lineTo) { previous = {...command.lineTo}; append({lineTo: {...previous}}); }
    else if (command.cubicTo) {
      const c = command.cubicTo;
      subdivide(previous, {x: c.x1, y: c.y1}, {x: c.x2, y: c.y2}, {x: c.x, y: c.y}, 0);
      previous = {x: c.x, y: c.y};
    } else { append({close: {}}); previous = start; }
  }
  return flattened;
}
