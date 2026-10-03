"""Small authoring helpers that preserve relationships in native path geometry."""
import math
from numbers import Real


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a finite number')
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(result):
        raise ValueError(f'{name} must be a finite number')
    return result


def _point(value, name):
    try:
        x, y = value
    except (TypeError, ValueError):
        raise ValueError(f'{name} must contain exactly two coordinates') from None
    return _number(x, f'{name}.x'), _number(y, f'{name}.y')


def radial_pointer_commands(pivot, tip, half_width, *, tip_half_width=0):
    """Return an editable pointer whose axis passes through ``pivot``.

    ``pivot`` and ``tip`` are (x, y) pairs in the scene's pixel coordinates.
    ``half_width`` is the perpendicular distance from the pivot to either base
    corner. ``tip_half_width`` optionally describes a blunt end perpendicular
    to the same axis; zero gives a triangle. Reuse this same pivot for the
    visible hub; it need not be the center of an enclosing dial. The caller
    supplies the reference tip and widths, so this helper neither infers nor
    changes a depicted value or angle.

    The returned moveTo/lineTo/close commands are directly usable in a manifest
    path. Invalid, coincident, or numerically degenerate geometry is rejected.
    """
    px, py = _point(pivot, 'pivot')
    tx, ty = _point(tip, 'tip')
    width = _number(half_width, 'half_width')
    if width <= 0:
        raise ValueError('half_width must be positive')
    tip_width = _number(tip_half_width, 'tip_half_width')
    if tip_width < 0:
        raise ValueError('tip_half_width must be nonnegative')
    dx, dy = tx - px, ty - py
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length == 0:
        raise ValueError('pivot and tip must define a finite non-zero axis')
    nx, ny = -dy / length, dx / length
    left = {'x': px + width * nx, 'y': py + width * ny}
    right = {'x': px - width * nx, 'y': py - width * ny}
    cap = ([{'x': tx + tip_width * nx, 'y': ty + tip_width * ny},
            {'x': tx - tip_width * nx, 'y': ty - tip_width * ny}]
           if tip_width else [{'x': tx, 'y': ty}])
    area = ((tx - left['x']) * (right['y'] - left['y'])
            - (ty - left['y']) * (right['x'] - left['x']))
    if (not all(math.isfinite(v) for p in [left, *cap, right] for v in p.values())
            or not math.isfinite(area) or area == 0):
        raise ValueError('pointer must have finite nondegenerate coordinates')
    if tip_width and cap[0] == cap[1]:
        raise ValueError('tip cap must have nondegenerate coordinates')
    return [{'moveTo': left}, *[{'lineTo': point} for point in cap],
            {'lineTo': right}, {'close': {}}]
