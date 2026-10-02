"""Strict, offline SVG-to-editable-scene import.

Only geometry and editable baseline text are imported. Curves are flattened in
canvas space, so ``tolerance`` is a CSS-pixel error bound after transforms. This
is deliberately not a general SVG renderer: constructs the scene cannot express
raise SVGImportError rather than being dropped or rasterized.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


class SVGImportError(ValueError):
    """The input cannot be faithfully represented by the editable scene."""


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_MAX_BYTES = 16 * 1024 * 1024
_MAX_NODES = 20000
_MAX_POINTS = 200000
_MAX_COORD = 1e9
_SVG_NS = "http://www.w3.org/2000/svg"
_SUPPORTED = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title", "desc", "metadata"}
_DEFAULT_STYLE = {
    "fill": "#000000", "stroke": "none", "stroke-width": "1", "opacity": "1",
    "fill-opacity": "1", "stroke-opacity": "1", "fill-rule": "nonzero",
    "font-size": "16", "font-weight": "normal", "font-style": "normal",
    "text-anchor": "start", "visibility": "visible", "display": "inline",
    "stroke-linecap": "butt", "stroke-linejoin": "miter", "stroke-dasharray": "none",
    "color": "#000000", "font-family": "sans-serif",
}
_STYLE_KEYS = set(_DEFAULT_STYLE) | {"stroke-miterlimit", "stroke-dashoffset", "letter-spacing", "word-spacing", "dominant-baseline", "alignment-baseline", "vector-effect", "paint-order", "filter", "clip-path", "mask", "marker-start", "marker-mid", "marker-end"}
_SAFE_EMPTY_STYLE = {"filter", "clip-path", "mask", "marker-start", "marker-mid", "marker-end"}
_UNSUPPORTED_PRESENTATION = {"text-decoration", "direction", "writing-mode", "unicode-bidi", "font-variant", "font-stretch", "baseline-shift", "white-space", "kerning", "font-size-adjust", "mix-blend-mode", "isolation"}
_COLORS = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff", "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "navy": "#000080", "teal": "#008080", "maroon": "#800000", "lime": "#00ff00", "aqua": "#00ffff", "fuchsia": "#ff00ff", "olive": "#808000"}


def _error(message, node=None):
    suffix = "" if node is None else f" on <{_tag(node)}>" + (f" id={node.get('id')!r}" if node.get("id") else "")
    raise SVGImportError(message + suffix)


def _tag(node):
    return node.tag.rsplit("}", 1)[-1]


def _finite(value):
    if not math.isfinite(value) or abs(value) > _MAX_COORD:
        _error("Nonfinite or excessively large coordinates are not supported")
    return value


def _length(value, default=None):
    if value is None:
        if default is None:
            _error("Missing required length")
        return default
    match = re.fullmatch(r"\s*(" + _NUMBER.pattern + r")\s*(px|pt|pc|in|cm|mm|q)?\s*", str(value), re.I)
    if not match:
        _error(f"Unsupported length {value!r}; use absolute lengths (px, pt, cm, mm, in), not percentages or font-relative units")
    factors = {None: 1, "px": 1, "pt": 96 / 72, "pc": 16, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "q": 96 / 101.6}
    return _finite(float(match[1]) * factors[(match[2] or "").lower() or None])


def _numbers(value):
    scanner = _Scanner(str(value))
    result = []
    while scanner.has_more():
        result.append(scanner.number())
    return result


def _mul(first, second):
    a, b, c, d, e, f = first
    A, B, C, D, E, F = second
    return (a*A + c*B, b*A + d*B, a*C + c*D, b*C + d*D, a*E + c*F + e, b*E + d*F + f)


def _point(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (_finite(a*x + c*y + e), _finite(b*x + d*y + f))


def _transform(value):
    matrix = _IDENTITY
    rest = (value or "").strip()
    while rest:
        match = re.match(r"([A-Za-z]+)\s*\(([^()]*)\)\s*,?\s*", rest)
        if not match:
            _error(f"Malformed transform near {rest[:60]!r}")
        kind, arg = match[1], _numbers(match[2])
        if kind == "matrix" and len(arg) == 6:
            item = tuple(arg)
        elif kind == "translate" and len(arg) in (1, 2):
            item = (1, 0, 0, 1, arg[0], arg[1] if len(arg) == 2 else 0)
        elif kind == "scale" and len(arg) in (1, 2):
            item = (arg[0], 0, 0, arg[-1], 0, 0)
        elif kind == "rotate" and len(arg) in (1, 3):
            radians = math.radians(arg[0]); co, si = math.cos(radians), math.sin(radians)
            item = (co, si, -si, co, 0, 0)
            if len(arg) == 3:
                item = _mul(_mul((1, 0, 0, 1, arg[1], arg[2]), item), (1, 0, 0, 1, -arg[1], -arg[2]))
        elif kind in ("skewX", "skewY") and len(arg) == 1:
            tangent = math.tan(math.radians(arg[0]))
            item = (1, 0, tangent, 1, 0, 0) if kind == "skewX" else (1, tangent, 0, 1, 0, 0)
        else:
            _error(f"Unsupported transform or wrong argument count: {kind}({match[2]})")
        matrix = _mul(matrix, item)
        rest = rest[match.end():].strip()
    return tuple(_finite(v) for v in matrix)


class _Scanner:
    def __init__(self, text):
        self.text, self.index = text, 0

    def skip(self):
        while self.index < len(self.text) and (self.text[self.index].isspace() or self.text[self.index] == ","):
            self.index += 1

    def has_more(self):
        self.skip()
        return self.index < len(self.text)

    def command(self):
        self.skip()
        if self.index < len(self.text) and self.text[self.index].isalpha():
            letter = self.text[self.index]; self.index += 1
            return letter
        return None

    def number(self):
        self.skip()
        match = _NUMBER.match(self.text, self.index)
        if not match:
            _error(f"Expected a number near {self.text[self.index:self.index+40]!r}")
        self.index = match.end()
        return _finite(float(match[0]))

    def flag(self):
        self.skip()
        if self.index >= len(self.text) or self.text[self.index] not in "01":
            _error("Elliptical arc flags must be 0 or 1")
        value = int(self.text[self.index]); self.index += 1
        return value


def _distance_segment(point, first, last):
    dx, dy = last[0] - first[0], last[1] - first[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0]-first[0], point[1]-first[1])
    t = max(0, min(1, ((point[0]-first[0])*dx + (point[1]-first[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(point[0] - first[0] - t*dx, point[1] - first[1] - t*dy)


def _mid(first, last):
    return ((first[0]+last[0])/2, (first[1]+last[1])/2)


def _flatten_cubic(p0, p1, p2, p3, tolerance, emit, depth=0):
    if max(_distance_segment(p1, p0, p3), _distance_segment(p2, p0, p3)) <= tolerance:
        emit(p3); return
    if depth >= 24:
        _error("Curve requires excessive subdivision; increase tolerance or simplify SVG")
    a, b, c = _mid(p0, p1), _mid(p1, p2), _mid(p2, p3)
    d, e = _mid(a, b), _mid(b, c)
    m = _mid(d, e)
    _flatten_cubic(p0, a, d, m, tolerance, emit, depth+1)
    _flatten_cubic(m, e, c, p3, tolerance, emit, depth+1)


def _arc(start, end, rx, ry, rotation, large, sweep, matrix, tolerance, emit):
    """SVG endpoint-to-center conversion, then conservatively bounded chords."""
    rx, ry = abs(rx), abs(ry)
    if start == end:
        return
    if not rx or not ry:
        emit(_point(matrix, end)); return
    phi = math.radians(rotation % 360); co, si = math.cos(phi), math.sin(phi)
    dx, dy = (start[0]-end[0])/2, (start[1]-end[1])/2
    xp, yp = co*dx + si*dy, -si*dx + co*dy
    scale = (xp/rx)**2 + (yp/ry)**2
    if scale > 1:
        scale = math.sqrt(scale); rx *= scale; ry *= scale
    denominator = (rx*yp)**2 + (ry*xp)**2
    numerator = max(0, (rx*ry)**2 - denominator)
    factor = (-1 if large == sweep else 1) * math.sqrt(numerator / denominator) if denominator else 0
    cxp, cyp = factor*rx*yp/ry, -factor*ry*xp/rx
    cx, cy = co*cxp - si*cyp + (start[0]+end[0])/2, si*cxp + co*cyp + (start[1]+end[1])/2
    ux, uy = (xp-cxp)/rx, (yp-cyp)/ry
    vx, vy = (-xp-cxp)/rx, (-yp-cyp)/ry
    theta = math.atan2(uy, ux)
    delta = math.atan2(ux*vy-uy*vx, ux*vx+uy*vy)
    if not sweep and delta > 0: delta -= 2*math.pi
    if sweep and delta < 0: delta += 2*math.pi
    # Frobenius norm of the transformed ellipse basis bounds its operator norm.
    a, b, c, d, _, _ = matrix
    ex = (a*rx*co + c*rx*si, b*rx*co + d*rx*si)
    ey = (-a*ry*si + c*ry*co, -b*ry*si + d*ry*co)
    radius_bound = math.sqrt(sum(v*v for v in (*ex, *ey)))
    step = 2*math.acos(max(-1, min(1, 1-tolerance/radius_bound))) if radius_bound > tolerance else math.pi/2
    step = max(1e-7, min(math.pi/2, step))
    count = max(1, math.ceil(abs(delta)/step))
    if count > _MAX_POINTS:
        _error("Arc requires too many vertices; increase tolerance or simplify SVG")
    for i in range(1, count+1):
        angle = theta + delta*i/count
        q = (cx + rx*co*math.cos(angle)-ry*si*math.sin(angle), cy + rx*si*math.cos(angle)+ry*co*math.sin(angle))
        emit(_point(matrix, end if i == count else q))


def _path(data, matrix, tolerance, budget):
    scanner = _Scanner(data)
    result = []
    point = start = (0.0, 0.0)
    command = previous = None
    cubic_control = quadratic_control = None

    def append(kind, position=None):
        budget[0] += 1
        if budget[0] > _MAX_POINTS:
            _error("SVG exceeds the vertex limit; simplify the input or increase tolerance")
        result.append({kind: {} if position is None else {"x": position[0], "y": position[1]}})

    def line(position): append("lineTo", position)

    while scanner.has_more():
        explicit = scanner.command()
        if explicit is not None:
            command = explicit
        if command is None or command.upper() not in "MLHVCSQTAZ":
            _error(f"Unsupported or missing path command {command!r}")
        kind, relative = command.upper(), command.islower()
        if not result and kind != "M":
            _error("Path must begin with moveto (M or m)")
        if kind == "Z":
            if explicit is None: _error("A closepath cannot have implicit numeric arguments")
            append("close"); point = start
            cubic_control = quadratic_control = None
            previous = "Z"; command = None
            continue
        origin = point

        def pair():
            x, y = scanner.number(), scanner.number()
            return (x+origin[0], y+origin[1]) if relative else (x, y)

        if kind in ("M", "L", "T"):
            end = pair()
            if kind == "M":
                append("moveTo", _point(matrix, end)); start = end
                command = "l" if relative else "L"
            elif kind == "T":
                control = (2*origin[0]-quadratic_control[0], 2*origin[1]-quadratic_control[1]) if previous in ("Q", "T") else origin
                c1 = (origin[0]+2*(control[0]-origin[0])/3, origin[1]+2*(control[1]-origin[1])/3)
                c2 = (end[0]+2*(control[0]-end[0])/3, end[1]+2*(control[1]-end[1])/3)
                _flatten_cubic(*[_point(matrix, p) for p in (origin, c1, c2, end)], tolerance, line)
                quadratic_control = control
            else: line(_point(matrix, end))
        elif kind == "H":
            x = scanner.number(); end = (x+origin[0] if relative else x, origin[1]); line(_point(matrix, end))
        elif kind == "V":
            y = scanner.number(); end = (origin[0], y+origin[1] if relative else y); line(_point(matrix, end))
        elif kind in ("C", "S"):
            c1 = pair() if kind == "C" else ((2*origin[0]-cubic_control[0], 2*origin[1]-cubic_control[1]) if previous in ("C", "S") else origin)
            c2, end = pair(), pair()
            _flatten_cubic(*[_point(matrix, p) for p in (origin, c1, c2, end)], tolerance, line)
            cubic_control = c2
        elif kind == "Q":
            control, end = pair(), pair()
            c1 = (origin[0]+2*(control[0]-origin[0])/3, origin[1]+2*(control[1]-origin[1])/3)
            c2 = (end[0]+2*(control[0]-end[0])/3, end[1]+2*(control[1]-end[1])/3)
            _flatten_cubic(*[_point(matrix, p) for p in (origin, c1, c2, end)], tolerance, line)
            quadratic_control = control
        elif kind == "A":
            rx, ry, rotation = scanner.number(), scanner.number(), scanner.number()
            large, sweep = scanner.flag(), scanner.flag()
            end = pair()
            _arc(origin, end, rx, ry, rotation, large, sweep, matrix, tolerance, line)
        point = end
        if kind not in ("C", "S"): cubic_control = None
        if kind not in ("Q", "T"): quadratic_control = None
        previous = kind
    return result


def _color(value, current):
    value = value.strip().lower()
    if value == "currentcolor": value = current.strip().lower()
    if value == "none": return "none"
    if value in _COLORS: return _COLORS[value]
    if re.fullmatch(r"#[\da-f]{3}", value): return "#" + "".join(c*2 for c in value[1:])
    if re.fullmatch(r"#[\da-f]{6}", value): return value
    match = re.fullmatch(r"rgb\(\s*([^)]*)\)", value)
    if match:
        pieces = re.split(r"[\s,]+", match[1].strip())
        if len(pieces) == 3:
            channels = []
            try:
                for piece in pieces:
                    val = float(piece[:-1])*255/100 if piece.endswith("%") else float(piece)
                    channels.append(round(max(0, min(255, val))))
                return "#" + "".join(f"{c:02x}" for c in channels)
            except (ValueError, OverflowError): pass
    _error(f"Unsupported paint {value!r}; use solid #RRGGBB or rgb() colors and explicit opacity, without paint servers")


def _style(node, inherited):
    style = inherited.copy()
    for key in _UNSUPPORTED_PRESENTATION:
        if key in node.attrib:
            _error(f"Unsupported presentation attribute {key!r}; flatten its effect into paths or explicit text placement", node)
    for key in _STYLE_KEYS:
        if key in node.attrib: style[key] = node.attrib[key]
    inline = node.get("style", "")
    for part in inline.split(";"):
        if not part.strip(): continue
        if ":" not in part: _error("Malformed inline CSS declaration", node)
        key, value = (part.strip() for part in part.split(":", 1))
        if key not in _STYLE_KEYS:
            _error(f"Unsupported CSS property {key!r}; flatten it into supported geometry/styles", node)
        if "!important" in value: _error("CSS !important is unsupported", node)
        style[key] = value
    for key, value in list(style.items()):
        if value == "inherit": style[key] = inherited.get(key, _DEFAULT_STYLE.get(key))
    if node.get("class"): _error("CSS classes are unsupported; inline computed styles first", node)
    if style["fill-rule"] != "nonzero":
        _error("fill-rule=evenodd is unsupported; normalize compound contour winding to nonzero before importing", node)
    for key in _SAFE_EMPTY_STYLE:
        if style.get(key, "none") != "none": _error(f"{key} is unsupported; flatten/remove the effect first", node)
    constraints = {"stroke-linecap": "butt", "stroke-linejoin": "miter", "stroke-dasharray": "none", "stroke-dashoffset": "0", "stroke-miterlimit": "4", "letter-spacing": "normal", "word-spacing": "normal", "dominant-baseline": "auto", "alignment-baseline": "auto", "vector-effect": "none", "paint-order": "normal"}
    for key, expected in constraints.items():
        if key in style and style[key] != expected:
            # Zero tracking is equivalent to normal; automatic baseline is default.
            if key in ("letter-spacing", "word-spacing") and style[key] in ("0", "0px"): continue
            if key == "dominant-baseline" and style[key] == "alphabetic": continue
            _error(f"Unsupported {key}={style[key]!r}; outline this effect before importing", node)
    if style["display"] not in ("inline", "none"): _error("Unsupported display mode", node)
    if style["visibility"] not in ("visible", "hidden", "collapse"): _error("Unsupported visibility", node)
    return style


def _unit_opacity(value, name):
    try: number = float(value)
    except ValueError: _error(f"Invalid {name} {value!r}")
    if not math.isfinite(number) or not 0 <= number <= 1: _error(f"{name} must be between 0 and 1")
    return number


def _paint(style, matrix, node, is_text=False):
    fill, stroke = _color(style["fill"], style["color"]), _color(style["stroke"], style["color"])
    width = _length(style["stroke-width"])
    if width < 0: _error("Stroke width cannot be negative", node)
    if is_text and stroke != "none" and width:
        _error("Editable text cannot preserve SVG text strokes; convert stroked text to outlines", node)
    if stroke != "none" and width:
        a, b, c, d, _, _ = matrix
        sx, sy = math.hypot(a, b), math.hypot(c, d)
        if not math.isclose(sx, sy, rel_tol=1e-8, abs_tol=1e-8) or abs(a*c+b*d) > 1e-8*max(1, sx*sy):
            _error("A nonuniform scale/skew on a stroked path cannot preserve its stroke width; expand the stroke to a filled outline", node)
        width *= sx
    alpha = _unit_opacity(style["opacity"], "opacity")
    fill_alpha = _unit_opacity(style["fill-opacity"], "fill-opacity")
    stroke_alpha = _unit_opacity(style["stroke-opacity"], "stroke-opacity")
    if fill != "none" and stroke != "none" and width and not math.isclose(fill_alpha, stroke_alpha):
        _error("Different simultaneous fill/stroke opacities require separate paths", node)
    alpha *= fill_alpha if fill != "none" else stroke_alpha
    return {"fill": fill, "stroke": stroke, "stroke_width": width, "opacity": alpha}


def _shape_data(node):
    tag = _tag(node)
    if tag == "path": return node.get("d", "")
    if tag == "line":
        x1, y1, x2, y2 = [_length(node.get(k), 0) for k in ("x1", "y1", "x2", "y2")]
        return f"M{x1},{y1} L{x2},{y2}"
    if tag in ("polyline", "polygon"):
        points = _numbers(node.get("points", ""))
        if len(points) % 2: _error("Points must contain complete x,y pairs", node)
        if len(points) < 4: _error("Polyline/polygon requires at least two points", node)
        return "M" + " L".join(f"{x},{y}" for x, y in zip(points[::2], points[1::2])) + (" Z" if tag == "polygon" else "")
    if tag == "rect":
        x, y, w, h = [_length(node.get(k), 0) for k in ("x", "y", "width", "height")]
        if w < 0 or h < 0: _error("Rectangle dimensions cannot be negative", node)
        if not w or not h: return ""
        rx = _length(node.get("rx"), _length(node.get("ry"), 0))
        ry = _length(node.get("ry"), rx)
        if rx < 0 or ry < 0: _error("Corner radii cannot be negative", node)
        rx, ry = min(rx, w/2), min(ry, h/2)
        if not rx or not ry: return f"M{x},{y} H{x+w} V{y+h} H{x} Z"
        return f"M{x+rx},{y} H{x+w-rx} A{rx},{ry} 0 0 1 {x+w},{y+ry} V{y+h-ry} A{rx},{ry} 0 0 1 {x+w-rx},{y+h} H{x+rx} A{rx},{ry} 0 0 1 {x},{y+h-ry} V{y+ry} A{rx},{ry} 0 0 1 {x+rx},{y} Z"
    cx, cy = _length(node.get("cx"), 0), _length(node.get("cy"), 0)
    rx = _length(node.get("r" if tag == "circle" else "rx"), 0)
    ry = rx if tag == "circle" else _length(node.get("ry"), 0)
    if rx < 0 or ry < 0: _error("Ellipse radii cannot be negative", node)
    if not rx or not ry: return ""
    return f"M{cx+rx},{cy} A{rx},{ry} 0 1 1 {cx-rx},{cy} A{rx},{ry} 0 1 1 {cx+rx},{cy} Z"


def _viewbox(node, width, height):
    raw = node.get("viewBox")
    if raw is None: return _IDENTITY
    values = _numbers(raw)
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0: _error("viewBox needs x y width height with positive dimensions", node)
    x, y, vw, vh = values
    aspect = node.get("preserveAspectRatio", "xMidYMid meet").split()
    if aspect == ["none"]:
        sx, sy = width/vw, height/vh
        return (sx, 0, 0, sy, -x*sx, -y*sy)
    if len(aspect) not in (1, 2) or not re.fullmatch(r"x(Min|Mid|Max)Y(Min|Mid|Max)", aspect[0]) or (len(aspect) == 2 and aspect[1] not in ("meet", "slice")):
        _error("Unsupported preserveAspectRatio", node)
    if len(aspect) == 2 and aspect[1] == "slice":
        _error("preserveAspectRatio=slice requires viewport clipping; use meet or normalize the geometry", node)
    scale = min(width/vw, height/vh)
    align_x = {"Min": 0, "Mid": .5, "Max": 1}[aspect[0][1:4]]
    align_y = {"Min": 0, "Mid": .5, "Max": 1}[aspect[0][5:8]]
    return (scale, 0, 0, scale, (width-vw*scale)*align_x-x*scale, (height-vh*scale)*align_y-y*scale)


def import_svg(svg_path, tolerance=0.35):
    """Read SVG and return a scene of global polygonal paths and baseline text.

    Raises SVGImportError for unsupported/lossy conversions. Text retains its
    source baseline and size; downstream renderers must use registered font
    metrics to position editable boxes. Source IDs are retained verbatim;
    anonymous IDs derive only from XML topology, never displayed text.
    """
    if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0.001 <= tolerance <= 100:
        _error("tolerance must be a finite CSS-pixel value between 0.001 and 100")
    source = Path(svg_path)
    if source.stat().st_size > _MAX_BYTES: _error("SVG is larger than the 16 MiB import limit")
    data = source.read_bytes()
    if re.search(br"<!\s*(DOCTYPE|ENTITY)\b", data, re.I): _error("DTD/entity declarations are forbidden; provide a self-contained SVG")
    if re.search(br"<\?(?!xml\s)[A-Za-z]", data, re.I):
        _error("XML processing instructions, including external stylesheets, are unsupported; inline computed styles first")
    try: root = ET.fromstring(data)
    except ET.ParseError as exc: raise SVGImportError(f"Malformed SVG XML: {exc}") from exc
    if _tag(root) != "svg": _error("Root element must be <svg>")
    nodes = list(root.iter())
    if len(nodes) > _MAX_NODES: _error("SVG exceeds the 20,000-node import limit")
    ids = set()
    parent_map = {child: parent for parent in nodes for child in parent}
    for node in nodes:
        # Metadata can carry foreign namespaces but is not drawable content.
        ancestor = node
        in_metadata = False
        while ancestor is not None:
            if _tag(ancestor) == "metadata": in_metadata = True; break
            ancestor = parent_map.get(ancestor)
        if in_metadata: continue
        tag = _tag(node)
        if tag not in _SUPPORTED: _error(f"Unsupported SVG element {tag!r}; replace resources/effects with self-contained filled paths or editable text", node)
        if "}" in node.tag and not node.tag.startswith("{"+_SVG_NS+"}"): _error("Foreign drawable XML namespaces are unsupported", node)
        for key in node.attrib:
            local = key.rsplit("}", 1)[-1]
            if local in ("href", "src"):
                _error("Referenced resources are forbidden; no network, files, or embedded rasters are imported", node)
            if local.startswith("on"): _error("Event handlers are forbidden", node)
        if node.get("id"):
            if node.get("id") in ids: _error(f"Duplicate source ID {node.get('id')!r}", node)
            ids.add(node.get("id"))
    view = _numbers(root.get("viewBox", ""))
    width = _length(root.get("width"), view[2] if len(view) == 4 else 300)
    height = _length(root.get("height"), view[3] if len(view) == 4 else 150)
    if width <= 0 or height <= 0: _error("Canvas width and height must be positive")
    objects, budget, generated_ids = [], [0], set(ids)

    def identity(node, location):
        if node.get("id"): return node.get("id")
        candidate = "svg-" + "-".join(str(i) for i in location) + "-" + _tag(node)
        while candidate in generated_ids: candidate += "-anonymous"
        generated_ids.add(candidate)
        return candidate

    def walk(node, parent_matrix, parent_style, group_id, location, hidden=False):
        tag = _tag(node)
        if tag in ("metadata", "title", "desc"): return
        style = _style(node, parent_style)
        # opacity is not inherited in SVG; per-container compositing is separate.
        if "opacity" not in node.attrib and not re.search(r"(?:^|;)\s*opacity\s*:", node.get("style", "")):
            style["opacity"] = "1"
        hidden = hidden or style["display"] == "none"
        matrix = _mul(parent_matrix, _transform(node.get("transform")))
        node_id = identity(node, location)
        if tag in ("svg", "g"):
            if _unit_opacity(style["opacity"], "opacity") != 1:
                _error("Group opacity changes overlap compositing and cannot be flattened; put opacity on individual shapes or flatten the group to outlined geometry", node)
            if tag == "svg":
                if node is root:
                    matrix = _mul(matrix, _viewbox(node, width, height))
                else:
                    _error("Nested SVG viewports require clipping; flatten the viewport to a transformed group first", node)
            next_group = node_id if tag == "g" else group_id
            for i, child in enumerate(node): walk(child, matrix, style, next_group, (*location, i), hidden)
            return
        if tag == "tspan": _error("tspan must be a direct child of text", node)
        if tag == "text":
            a, b, c, d, _, _ = matrix
            if a <= 0 or d <= 0 or not math.isclose(a, d, rel_tol=1e-8, abs_tol=1e-8) or abs(b)+abs(c) > 1e-8:
                _error("Rotated/skewed/reflected/nonuniformly scaled text is unsupported; remove the transform or outline the text", node)
            if node.get("rotate") or node.get("textLength") or node.get("lengthAdjust"):
                _error("Rotated or length-adjusted text must be outlined first", node)

            def emit_text(text_node, text_style, content, text_id, x, y):
                if not content or hidden or text_style["visibility"] != "visible": return
                anchor = _point(matrix, (x, y))
                size = _length(text_style["font-size"])*a
                if size <= 0: _error("Font size must be positive", text_node)
                weight = text_style["font-weight"]
                if weight in ("normal", "bold"): bold = weight == "bold"
                else:
                    try: bold = float(weight) >= 600
                    except ValueError: _error("Unsupported relative font weight; use normal/bold or numeric weight", text_node)
                if text_style["font-style"] not in ("normal", "italic"): _error("Unsupported font-style; use normal or italic", text_node)
                align = {"start": "left", "middle": "center", "end": "right"}.get(text_style["text-anchor"])
                if align is None: _error("Unsupported text-anchor", text_node)
                item = {"id": text_id, "kind": "text", "text": content, "anchor": {"x": anchor[0], "y": anchor[1]}, "font_size": size, "bold": bold, "italic": text_style["font-style"] == "italic", "alignment": align, "style": _paint(text_style, matrix, text_node, True)}
                if group_id: item["group_id"] = group_id
                objects.append(item)

            def single_position(text_node, key, default):
                raw = text_node.get(key)
                if raw is None: return default
                # Positioning lists need character-level layout unavailable here.
                try: return _length(raw)
                except SVGImportError: _error(f"Text {key} must be a single absolute coordinate; outline character-positioned text", text_node)

            x = single_position(node, "x", 0) + single_position(node, "dx", 0)
            y = single_position(node, "y", 0) + single_position(node, "dy", 0)
            children = list(node)
            if not children:
                preserve = node.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
                content = node.text or ""
                content = content if preserve else " ".join(content.split())
                emit_text(node, style, content, node_id, x, y)
            else:
                if (node.text or "").strip(): _error("Mixed text/tspan layout must use explicitly positioned tspans or be outlined", node)
                for i, child in enumerate(children):
                    if _tag(child) != "tspan" or list(child): _error("Only direct, explicitly positioned tspans are supported", child)
                    if child.get("x") is None or child.get("y") is None or (child.tail or "").strip():
                        _error("Each tspan needs explicit x and y, with no unpositioned trailing text", child)
                    if child.get("transform"): _error("Transformed tspans must be outlined", child)
                    child_style = _style(child, style)
                    child_alpha = child_style["opacity"] if "opacity" in child.attrib or re.search(r"(?:^|;)\s*opacity\s*:", child.get("style", "")) else "1"
                    child_style["opacity"] = str(_unit_opacity(style["opacity"], "opacity")*_unit_opacity(child_alpha, "opacity"))
                    if child.get("rotate") or child.get("textLength") or child.get("lengthAdjust"):
                        _error("Rotated or length-adjusted tspans must be outlined first", child)
                    cx = single_position(child, "x", 0)+single_position(child, "dx", 0)
                    cy = single_position(child, "y", 0)+single_position(child, "dy", 0)
                    content = " ".join((child.text or "").split())
                    emit_text(child, child_style, content, identity(child, (*location, i)), cx, cy)
            return
        commands = _path(_shape_data(node), matrix, tolerance, budget)
        if tag == "line":
            # SVG lines have no interior fill, even though fill is inherited.
            style["fill"] = "none"
        paint = _paint(style, matrix, node)
        if not hidden and style["visibility"] == "visible" and commands:
            item = {"id": node_id, "kind": "path", "commands": commands, "style": paint}
            if group_id: item["group_id"] = group_id
            objects.append(item)

    walk(root, _IDENTITY, _DEFAULT_STYLE, None, (0,))
    return {"canvas": {"width": width, "height": height}, "objects": objects}
