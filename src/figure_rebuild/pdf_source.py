"""Read-only extraction of path/glyph paints from MuPDF outlined SVG.

This is a bounded source preprocessor, not an SVG renderer or text recognizer.
It preserves Bezier control points, local resource identity, painter order and
unlowered compositing context. ``outline_paths`` is deliberately opt-in and
fails on unsupported effects instead of silently producing a partial figure.
Images remain separate occurrence records for the image extraction pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from .pdf_visibility import ID as EXACT_ID, exact_chain, prove_source_paint_invisible, simple_points
import hashlib
import math
import re
from typing import Sequence
import xml.etree.ElementTree as ET

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path

from .pdf_dash import PdfDashError, lower_dashes
from .pdf_fill import prove_evenodd_nonzero_equivalent
from .pdf_fill_clip import clip_nonzero_annular_fill
from .pdf_rect_clip import clip_convex_fill_to_rect
from .pdf_line_clip import clip_axis_butt_stroke_to_rect
from .pdf_clip import prove_clip_box_relation
from .pdf_stroke_bounds import (PdfStrokeBoundsError, PdfTangentStrokeProofError,
                                prove_tangent_stroke_support, stroke_envelope)


class PdfSourceError(ValueError):
    """Invalid or unsupported source input; no assets were written."""


class UnsupportedPdfPaintError(PdfSourceError):
    """A source paint cannot be represented faithfully by this converter."""


class _PdfSourceBudgetError(PdfSourceError):
    """A resource limit aborts extraction, never becomes a partial paint."""


def _outward_float(value, upper):
    """Round an exact rational toward the outside of a geometric envelope."""
    result = float(value)
    if not math.isfinite(result):
        raise UnsupportedPdfPaintError("Stroke bounds exceed finite geometry")
    if (Fraction(result) < value if upper else Fraction(result) > value):
        result = math.nextafter(result, math.inf if upper else -math.inf)
    return result


def _stroke_envelope(matrix, width, miter_limit, rectangle, *, linecap="butt", linejoin="miter"):
    """Keep source-error semantics around the strict style-aware support API."""
    try:
        return stroke_envelope(matrix, width, miter_limit, rectangle,
                               linecap=linecap, linejoin=linejoin)
    except PdfStrokeBoundsError as error:
        raise UnsupportedPdfPaintError(str(error)) from error


def _expand_bounds(bounds, envelope):
    ex, ey = envelope
    return (_outward_float(Fraction(bounds[0])-ex, False),
            _outward_float(Fraction(bounds[1])-ey, False),
            _outward_float(Fraction(bounds[2])+ex, True),
            _outward_float(Fraction(bounds[3])+ey, True))


_IDENTITY = (1., 0., 0., 1., 0., 0.)
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(r"[MLHVCSQTZmlhvcsqtz]|" + _NUMBER)
_NS = "http://www.w3.org/2000/svg"
_XLINK = "{http://www.w3.org/1999/xlink}href"
_MAX_BYTES = 16 * 1024 * 1024
_MAX_NODES = 20000
_MAX_COMMANDS = 200000
_DEFAULT_STYLE = {
    "fill": "#000000", "stroke": "none", "stroke-width": "1",
    "fill-opacity": "1", "stroke-opacity": "1", "fill-rule": "nonzero",
    "stroke-linecap": "butt", "stroke-linejoin": "miter", "stroke-miterlimit": "4",
    "stroke-dasharray": "none", "stroke-dashoffset": "0", "visibility": "visible",
    "color": "#000000", "paint-order": "normal", "vector-effect": "none",
}
_EFFECTS = {"opacity", "clip-path", "mask", "filter", "mix-blend-mode", "isolation", "display"}
_NONINHERITED = _EFFECTS | {"vector-effect"}
_ATTRS = {"id", "d", "transform", "style", "data-text", "href", "x", "y", "width", "height", "version", "viewBox", "preserveAspectRatio"} | set(_DEFAULT_STYLE) | _EFFECTS


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _number(value):
    if not re.fullmatch(_NUMBER, str(value).strip()):
        raise PdfSourceError(f"Expected finite unitless number, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or abs(value) > 1e9:
        raise PdfSourceError("Nonfinite or excessively large source coordinate")
    return value


def _matrix(value):
    if not value:
        return _IDENTITY
    match = re.fullmatch(r"\s*matrix\(\s*([^)]*)\)\s*", value)
    if not match:
        raise PdfSourceError(f"Only MuPDF matrix(a,b,c,d,e,f) transforms are supported: {value!r}")
    values = tuple(_number(v) for v in re.split(r"[\s,]+", match[1].strip()))
    if len(values) != 6:
        raise PdfSourceError("Affine matrix requires six values")
    return values


def _mul(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def _point(matrix, point):
    a, b, c, d, e, f = matrix
    return (_number(a*point[0]+c*point[1]+e), _number(b*point[0]+d*point[1]+f))


class _ExactPen(BasePen):
    def __init__(self, consume_command=None):
        super().__init__(None)
        self.commands = []
        self.consume_command = consume_command

    def _append(self, command):
        if len(self.commands) >= _MAX_COMMANDS:
            raise _PdfSourceBudgetError("Source path exceeds command budget")
        if self.consume_command is not None:
            self.consume_command()
        for point in command[1:]:
            for value in point:
                _number(value)
        self.commands.append(command)

    def _moveTo(self, p): self._append(("M", tuple(p)))
    def _lineTo(self, p): self._append(("L", tuple(p)))
    def _curveToOne(self, p1, p2, p3): self._append(("C", tuple(p1), tuple(p2), tuple(p3)))
    def _qCurveToOne(self, p1, p2):
        p0 = self._getCurrentPoint()
        self._curveToOne(tuple(p0[i]+2*(p1[i]-p0[i])/3 for i in (0, 1)),
                         tuple(p2[i]+2*(p1[i]-p2[i])/3 for i in (0, 1)), p2)
    def _closePath(self): self._append(("Z",))
    def _endPath(self): pass


def _commands(data, matrix, *, consume_command=None):
    # fontTools approximates arcs. Reject them before parsing: this API promises
    # exact Bezier conversion, including exact quadratic-to-cubic elevation.
    if data.strip() and not re.match(r"\s*[Mm]", data):
        raise PdfSourceError("Source path must begin with moveto")
    rest = _TOKEN.sub("", data)
    if rest.strip(" ,\t\r\n"):
        raise PdfSourceError("Unsupported path syntax (including elliptical arcs); no flattening performed")
    pen = _ExactPen(consume_command)
    try:
        parse_path(data, TransformPen(pen, matrix))
    except _PdfSourceBudgetError:
        raise
    except (ValueError, IndexError, TypeError, AssertionError) as exc:
        raise PdfSourceError(f"Invalid source path: {exc}") from exc
    return tuple(pen.commands)


def _bounds(commands):
    pen = BoundsPen(None)
    for command in commands:
        op, *points = command
        if op == "M": pen.moveTo(points[0])
        elif op == "L": pen.lineTo(points[0])
        elif op == "C": pen.curveTo(*points)
        elif op == "Z": pen.closePath()
    return pen.bounds


def _exact_zero_length_source_line(paint):
    """A float-collapsed nonzero source line is never a zero-area certificate."""
    if paint.kind != "path" or paint.reference_chain:
        return None
    node = ET.fromstring(paint.source_xml)
    data = node.get("d", "")
    if node.tag.rsplit("}", 1)[-1] != "path" or len(data) > 4096:
        return None
    try:
        points, closed = simple_points(data)
    except (ValueError, TypeError, OverflowError):
        return None
    if closed or len(points) != 2 or points[0] != points[1]:
        return None
    return {"exact_local_point": list(map(str, points[0])),
            "source_path_data_sha256": hashlib.sha256(data.encode()).hexdigest(),
            "predicate_arithmetic": "exact_rationals_of_original_svg_tokens"}


def _attributes(node, inherited):
    # Inkscape layer labels are editor metadata, never presentation properties.
    metadata = {"{http://www.inkscape.org/namespaces/inkscape}groupmode", "{http://www.inkscape.org/namespaces/inkscape}label"}
    own, unsupported = {}, []
    for key, value in node.attrib.items():
        if key in metadata:
            continue
        if key == _XLINK:
            own.setdefault("href", value)
        elif "}" in key:
            unsupported.append(f"unsupported attribute namespace {key}")
        else:
            own[key] = value
    unsupported.extend(f"unsupported attribute {key}" for key in own if key not in _ATTRS)
    properties = {key: value for key, value in own.items() if key in _DEFAULT_STYLE or key in _EFFECTS}
    for declaration in own.get("style", "").split(";"):
        if not declaration.strip(): continue
        if ":" not in declaration:
            unsupported.append(f"malformed CSS declaration {declaration!r}")
            continue
        key, value = (part.strip() for part in declaration.split(":", 1))
        if key not in _DEFAULT_STYLE and key not in _EFFECTS:
            unsupported.append(f"unsupported CSS property {key}")
        properties[key] = value
    style = {key: value for key, value in inherited.items() if key not in _NONINHERITED}
    style.update(properties)
    return style, unsupported


@dataclass(frozen=True)
class SourcePaint:
    source_id: str
    paint_index: int
    xml_path: tuple[int, ...]
    source_element_id: str | None
    resource_id: str | None
    reference_chain: tuple[str, ...]
    kind: str
    commands: tuple
    transform: tuple[float, ...]
    style: dict
    clips: tuple[dict, ...]
    groups: tuple[dict, ...]
    source_text: str | None
    source_xml: str
    unsupported: tuple[str, ...]
    exact_transform: tuple[str, ...] = ()


@dataclass(frozen=True)
class PdfSourceDocument:
    source_sha256: str
    view_box: tuple[float, ...]
    paints: tuple[SourcePaint, ...]
    resource_xml: dict[str, str]
    parser_limits: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PdfOutlineResult:
    objects: list[dict]
    provenance: list[dict]
    skipped: list[dict]
    selected_paint_ids: tuple[str, ...]
    source_paint_count: int


def extract_outlined_svg(data: str | bytes, *, max_total_commands: int = _MAX_COMMANDS) -> PdfSourceDocument:
    """Extract source records without guessing text or writing files.

    Coordinates remain in the root viewBox's user space (PDF points for MuPDF).
    The viewport is metadata, not a second transform. Unsupported paints remain
    explicit records, with their XML and context; definitions are not paints.
    Local ``use`` chains may reference path/image/group resources, never external
    content. XML paths address the expanded use-instance tree, so repeated group
    resources produce distinct identities for every child paint.
    The default expanded command budget is 200,000. Large known inputs may
    explicitly request up to 2,000,000; all other guards remain active and the
    selected budget and actual counts are recorded. No automatic retry raises
    a budget after a failure.
    """
    if (isinstance(max_total_commands, bool) or not isinstance(max_total_commands, int)
            or not 1 <= max_total_commands <= 2_000_000):
        raise PdfSourceError("Total command budget must be an integer in [1, 2000000]")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
        raise PdfSourceError("Expected SVG bytes/text within the 16 MiB limit")
    if re.search(br"<!\s*(DOCTYPE|ENTITY)\b|<\?(?!xml\s)", raw, re.I):
        raise PdfSourceError("DTD, entities and processing instructions are forbidden")
    try: root = ET.fromstring(raw)
    except ET.ParseError as exc: raise PdfSourceError(f"Malformed source SVG: {exc}") from exc
    if _tag(root) != "svg": raise PdfSourceError("Expected SVG root")
    nodes = list(root.iter())
    if len(nodes) > _MAX_NODES: raise PdfSourceError("Source SVG exceeds node budget")
    # Unused definitions and metadata are still serialized into source
    # evidence. Bound their hierarchy too, before any recursive serializer.
    pending = [(root, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > 256:
            raise _PdfSourceBudgetError("Source SVG exceeds input hierarchy depth budget")
        pending.extend((child, depth + 1) for child in node)
    definitions = {}
    parents = {child: parent for parent in root.iter() for child in parent}
    if any(_tag(node) == "style" for node in nodes):
        raise PdfSourceError("Stylesheets are unsupported; computed styles must be explicit")
    for node in nodes:
        if node.get("id"):
            if node.get("id") in definitions: raise PdfSourceError(f"Duplicate source ID {node.get('id')!r}")
            definitions[node.get("id")] = node
    view = root.get("viewBox")
    view_box = tuple(_number(v) for v in re.split(r"[\s,]+", view.strip())) if view else (0., 0., _number(root.get("width", "0")), _number(root.get("height", "0")))
    if len(view_box) != 4 or view_box[2] <= 0 or view_box[3] <= 0:
        raise PdfSourceError("Positive source viewBox dimensions are required")
    paints = []
    command_count = 0
    parsed_commands = 0
    expanded_nodes = 0

    def consume_command():
        nonlocal parsed_commands
        if parsed_commands >= max_total_commands:
            raise _PdfSourceBudgetError("Source SVG exceeds total command budget")
        parsed_commands += 1

    def walk(node, matrix, inherited, clips, groups, errors, location, references=(), source_text=None, exact=EXACT_ID):
        nonlocal command_count, expanded_nodes
        if len(location) + len(references) > 256:
            raise _PdfSourceBudgetError("Source SVG exceeds expanded hierarchy depth budget")
        expanded_nodes += 1
        if expanded_nodes > 200_000:
            raise PdfSourceError("Source SVG exceeds expanded node budget")
        tag = _tag(node)
        if tag in ("defs", "clipPath", "mask", "linearGradient", "radialGradient", "pattern", "marker", "filter", "symbol", "title", "desc", "metadata"): return
        style, own_errors = _attributes(node, inherited)
        errors = (*errors, *own_errors)
        if tag in ("path", "use", "image") and any(_tag(child) not in ("title", "desc", "metadata") for child in node):
            errors += ("unsupported child element on leaf paint/reference",)
        if "}" in node.tag and not node.tag.startswith("{"+_NS+"}"):
            errors += ("foreign drawable namespace",)
        matrix = _mul(matrix, _matrix(node.get("transform")))
        exact = exact_chain(exact, node.get("transform"))
        if source_text is None: source_text = node.get("data-text")
        if tag == "use":
            matrix = _mul(matrix, (1., 0., 0., 1., _number(node.get("x", "0")), _number(node.get("y", "0"))))
            exact = exact_chain(exact, None, (node.get("x", "0"),node.get("y", "0")))
        clip = style.get("clip-path", "none")
        if clip != "none":
            match = re.fullmatch(r"url\(#([^()]+)\)", clip)
            resource = definitions.get(match[1]) if match else None
            clips = (*clips, {"id": match[1] if match else None, "transform": list(matrix), "exact_transform": list(map(str, exact)) if exact is not None else None,
                              "element": ET.tostring(resource, encoding="unicode") if resource is not None else None,
                              "reference": clip,
                              "unsupported_resource_ancestors": _clip_ancestor_context(resource, parents, root)})
        identity = location
        if tag in ("svg", "g", "use"):
            context = {key: style.get(key, default) for key, default in (("opacity", "1"), ("mix-blend-mode", "normal"), ("isolation", "auto"), ("mask", "none"), ("filter", "none"), ("display", "inline"))}
            groups = (*groups, {"id": node.get("id"), "xml_path": list(location), **context})
        if tag == "svg" and node is not root:
            errors += ("nested SVG viewport",)
        elif tag in ("svg", "g"):
            count_before = len(paints)
            for index, child in enumerate(node):
                walk(child, matrix, style, clips, groups, errors, (*location, index), references, source_text, exact)
            if references and len(paints) == count_before:
                # MuPDF can emit empty Type3 glyph resources. Keep the use
                # occurrence auditable; never invent its outline from Unicode.
                paints.append(SourcePaint("svg-paint-" + "-".join(str(v) for v in identity), len(paints), identity,
                                          node.get("id"), references[-1], references,
                                          "glyph" if source_text is not None else "path", (), matrix, style,
                                          clips, groups, source_text, ET.tostring(node, encoding="unicode"), tuple(errors), tuple(map(str, exact)) if exact is not None else ()))
            return
        if tag == "use":
            href = node.get("href", node.get(_XLINK, ""))
            if node.get("href") is not None and node.get(_XLINK) is not None and node.get("href") != node.get(_XLINK):
                errors += ("conflicting href and xlink:href references",)
            resource = href[1:] if href.startswith("#") else None
            target = definitions.get(resource)
            if resource and resource in references:
                errors += (f"cyclic local reference {href}",)
            elif target is None:
                errors += (f"missing or external local reference {href!r}",)
            elif _tag(target) not in ("path", "image", "use", "g"):
                errors += (f"unsupported local reference target {_tag(target)!r}",)
            elif len(references) >= 32:
                errors += ("local reference depth limit",)
            else:
                walk(target, matrix, style, clips, groups, errors, location, (*references, resource), source_text, exact)
                return
        kind = "glyph" if tag == "path" and source_text is not None else tag if tag in ("path", "image") else "unsupported"
        commands = ()
        if tag == "path":
            try: commands = _commands(node.get("d", ""), matrix, consume_command=consume_command)
            except _PdfSourceBudgetError: raise
            except PdfSourceError as exc: errors += (str(exc),)
        elif tag != "image":
            errors += (f"unsupported paint element {tag!r}",)
        command_count += len(commands)
        source_id = "svg-paint-" + "-".join(str(v) for v in identity)
        paints.append(SourcePaint(source_id, len(paints), identity, node.get("id"), references[-1] if references else None,
                                  references, kind, commands, matrix, style, clips, groups, source_text,
                                  ET.tostring(node, encoding="unicode"), tuple(errors), tuple(map(str, exact)) if exact is not None else ()))
    walk(root, _IDENTITY, _DEFAULT_STYLE, (), (), (), (0,))
    return PdfSourceDocument(hashlib.sha256(raw).hexdigest(), view_box, tuple(paints),
                             {key: ET.tostring(value, encoding="unicode") for key, value in definitions.items()},
                             {"max_total_commands": max_total_commands,
                              "expanded_commands": command_count,
                              "parsed_commands_including_failed_paths": parsed_commands,
                              "max_expanded_hierarchy_depth": 256,
                              "max_input_hierarchy_depth": 256,
                              "max_expanded_nodes": 200_000, "expanded_nodes": expanded_nodes,
                              "max_commands_per_path": _MAX_COMMANDS,
                              "max_input_bytes": _MAX_BYTES, "max_input_nodes": _MAX_NODES})


def _alpha(value):
    value = _number(value)
    if not 0 <= value <= 1: raise UnsupportedPdfPaintError("Opacity must be within [0, 1]")
    return value


def _color(value, current):
    value = current if value == "currentColor" else value
    if value in ("none", "black", "white"):
        return {"black": "#000000", "white": "#ffffff"}.get(value, value)
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value): return value.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value): return "#" + "".join(c*2 for c in value[1:]).lower()
    raise UnsupportedPdfPaintError(f"Unsupported solid paint {value!r}; gradients/patterns are not approximated")


def _contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _disjoint(a, b):
    return a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]


def _axis_rectangle(commands):
    """Prove a single closed, nondegenerate axis-aligned rectangle."""
    if not commands or commands[-1][0] != "Z" or sum(c[0] == "M" for c in commands) != 1 or any(c[0] not in ("M", "L", "Z") for c in commands):
        return False
    if sum(c[0] == "Z" for c in commands) != 1:
        return False
    points = [c[1] for c in commands if c[0] != "Z"]
    if len(points) > 1 and points[-1] == points[0]: points.pop()
    return (len(points) == 4 and len(set(points)) == 4
            and len({p[0] for p in points}) == len({p[1] for p in points}) == 2
            and all(a[0] == b[0] or a[1] == b[1] for a,b in zip(points, points[1:]+points[:1])))


def _rect_clip(context):
    if context.get("unsupported_resource_ancestors"):
        raise UnsupportedPdfPaintError("Clip resource ancestor presentation/inheritance is unsupported")
    if not context["element"]: raise UnsupportedPdfPaintError("Missing or external clip resource")
    root = ET.fromstring(context["element"])
    if "}" in root.tag and not root.tag.startswith("{"+_NS+"}"):
        raise UnsupportedPdfPaintError("Unsupported clip resource namespace")
    if _tag(root) != "clipPath" or root.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse":
        raise UnsupportedPdfPaintError("Only userSpaceOnUse rectangular clips can be proved no-op")
    if set(root.attrib) - {"id", "clipPathUnits", "transform"}:
        raise UnsupportedPdfPaintError("Unsupported clipPath attributes")
    children = list(root)
    if len(children) != 1: raise UnsupportedPdfPaintError("Complex clip has multiple children")
    child = children[0]
    if list(child) or ("}" in child.tag and not child.tag.startswith("{"+_NS+"}")):
        raise UnsupportedPdfPaintError("Unsupported clip child content/namespace")
    matrix = _mul(_mul(context["transform"], _matrix(root.get("transform"))), _matrix(child.get("transform")))
    if _tag(child) == "rect" and not (set(child.attrib)-{"x", "y", "width", "height", "id", "transform"}):
        x, y = _number(child.get("x", "0")), _number(child.get("y", "0"))
        w, h = _number(child.get("width", "0")), _number(child.get("height", "0"))
        if w <= 0 or h <= 0: raise UnsupportedPdfPaintError("Degenerate clip rectangle")
        points = [_point(matrix, p) for p in ((x,y), (x+w,y), (x+w,y+h), (x,y+h))]
    elif _tag(child) == "path" and not (set(child.attrib)-{"d", "id", "transform", "clip-rule"}):
        if child.get("clip-rule", "nonzero") not in ("nonzero", "evenodd"):
            raise UnsupportedPdfPaintError("Unsupported clip winding rule")
        commands = _commands(child.get("d", ""), matrix)
        if not _axis_rectangle(commands):
            raise UnsupportedPdfPaintError("Complex clip curve/contour requires explicit geometry support")
        points = [c[1] for c in commands if c[0] != "Z"]
        if len(points) > 1 and points[-1] == points[0]: points.pop()
    else:
        raise UnsupportedPdfPaintError("Complex clip shape requires explicit geometry support")
    xs, ys = {p[0] for p in points}, {p[1] for p in points}
    if len(points) != 4 or len(xs) != 2 or len(ys) != 2 or len(set(points)) != 4 or any(a[0] != b[0] and a[1] != b[1] for a,b in zip(points, points[1:]+points[:1])):
        raise UnsupportedPdfPaintError("Clip is not an axis-aligned rectangle in source space")
    return min(xs), min(ys), max(xs), max(ys)


def _complex_clip_shapes(context):
    """Read clip geometry only after checking its complete supported context."""
    if context.get("unsupported_resource_ancestors"):
        raise UnsupportedPdfPaintError("Clip resource ancestor presentation/inheritance is unsupported")
    if not context["element"]:
        raise UnsupportedPdfPaintError("Missing or external clip resource")
    root = ET.fromstring(context["element"])
    if (_tag(root) != "clipPath" or root.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse"
            or set(root.attrib) - {"id", "clipPathUnits", "transform", "clip-rule"}
            or ("}" in root.tag and not root.tag.startswith("{"+_NS+"}"))):
        raise UnsupportedPdfPaintError("Unsupported complex clip resource/attributes")
    children = list(root)
    if not children or len(children) > 64:
        raise UnsupportedPdfPaintError("Complex clip child budget/empty geometry")
    matrix = _mul(context["transform"], _matrix(root.get("transform")))
    shapes = []
    for child in children:
        if list(child) or ("}" in child.tag and not child.tag.startswith("{"+_NS+"}")):
            raise UnsupportedPdfPaintError("Unsupported complex clip child content/namespace")
        child_matrix = _mul(matrix, _matrix(child.get("transform")))
        if _tag(child) == "path" and not (set(child.attrib)-{"d", "id", "transform", "clip-rule"}):
            commands = _commands(child.get("d", ""), child_matrix)
        elif _tag(child) == "rect" and not (set(child.attrib)-{"x", "y", "width", "height", "id", "transform", "clip-rule"}):
            x, y = _number(child.get("x", "0")), _number(child.get("y", "0"))
            w, h = _number(child.get("width", "0")), _number(child.get("height", "0"))
            if w <= 0 or h <= 0:
                raise UnsupportedPdfPaintError("Degenerate complex clip rectangle")
            points = [_point(child_matrix, p) for p in ((x,y), (x+w,y), (x+w,y+h), (x,y+h))]
            commands = (("M", points[0]), *(("L", p) for p in points[1:]), ("Z",))
        else:
            raise UnsupportedPdfPaintError("Unsupported complex clip child shape/attributes")
        rule = child.get("clip-rule", root.get("clip-rule", "nonzero"))
        if rule not in ("nonzero", "evenodd"):
            raise UnsupportedPdfPaintError("Unsupported complex clip winding rule")
        shapes.append((commands, rule))
    return shapes


def _complex_clip_relation(context, bounds):
    """Prove a clip's union of explicit path/rect children contains a box."""
    proofs = [prove_clip_box_relation(commands, bounds, fill_rule=rule)
              for commands, rule in _complex_clip_shapes(context)]
    if any(p and p["relation"] == "inside" for p in proofs):
        relation = "inside"
    elif all(p and p["relation"] == "outside" for p in proofs):
        relation = "outside"
    else:
        raise UnsupportedPdfPaintError("Complex clip boundary overlaps paint bounds or proof budget exhausted")
    return {"relation": relation, "proof": "union_of_clip_child_fill_regions",
            "source_clip_id": context["id"], "paint_bounds": list(bounds),
            "children": proofs, "source_commands_changed": False}


def _clip_ancestor_context(resource, parents, root):
    """Do not lose inherited definition styles when serializing a clip alone.

    MuPDF emits self-contained definitions. Other SVGs may inherit clip-rule
    through defs/groups; until those computed styles are supported, retain and
    reject that context instead of silently substituting nonzero. Even a
    recognized fill/color ancestor is conservatively rejected here.
    """
    result = []
    parent = parents.get(resource)
    while parent is not None:
        allowed = {"id"}
        if parent is root:
            allowed |= {"width", "height", "viewBox", "version", "preserveAspectRatio"}
        attributes = {k: v for k, v in parent.attrib.items() if k not in allowed}
        if attributes or ("}" in parent.tag and not parent.tag.startswith("{"+_NS+"}")):
            result.append({"tag": parent.tag, "attributes": attributes})
        parent = parents.get(parent)
    return result


def outline_paths(document: PdfSourceDocument, *, glyph_mode: str,
                  paint_ids: Sequence[str] | None = None, region: Sequence[float] | None = None,
                  transform: Sequence[float] = _IDENTITY, dash_tolerance: float = 1e-4,
                  max_clip_overhang: float = 0.) -> PdfOutlineResult:
    """Lower an explicit selection to native path objects, or raise.

    ``glyph_mode='outline'`` is required, including for ordinary labels. It
    produces editable geometry, never editable text or recovered semantics.
    Clips/ROI require disjoint/no-op proofs or an explicit supported geometric
    intersection. Convex filled polygon contours and a single nonzero annular fill clipped
    by a strictly nested contour have bounded exact intersection support.
    Cubic boundaries use exact rational hull separation and constant winding;
    no output path is flattened. Other crossing clips, images and group
    compositing remain unsupported.
    ``paint_ids`` is an explicit subset, reported as such in the result.
    ``transform`` maps source PDF coordinates into the target canvas. Positive
    dash arrays are lowered before any clipping, with ``dash_tolerance`` in
    source coordinate units and numerical arc-placement bounds in provenance.
    ``max_clip_overhang`` defaults to zero. An explicit positive source-unit
    bound permits only tiny rectangular clip overhang on a proved axis-aligned
    rectangle stroke, recorded as bounded_source_rounding (never exact/no-op).
    """
    if glyph_mode != "outline": raise PdfSourceError("Explicit glyph_mode='outline' is required; live text policy is unchanged")
    max_clip_overhang = _number(max_clip_overhang)
    if not 0 <= max_clip_overhang <= .01: raise PdfSourceError("Clip overhang allowance must be within [0, .01] source units")
    dash_tolerance = _number(dash_tolerance)
    if not 1e-9 <= dash_tolerance <= 1: raise PdfSourceError("Invalid source-unit dash tolerance")
    transform = tuple(_number(v) for v in transform)
    if len(transform) != 6: raise PdfSourceError("Target transform requires six values")
    if region is not None:
        region = tuple(_number(v) for v in region)
        if len(region) != 4 or region[2] <= region[0] or region[3] <= region[1]: raise PdfSourceError("Region must be a positive x0,y0,x1,y1 box")
    all_ids = {p.source_id for p in document.paints}
    selected = all_ids if paint_ids is None else set(paint_ids)
    if not selected <= all_ids: raise PdfSourceError(f"Unknown paint identities: {sorted(selected-all_ids)}")
    objects, provenance, skipped = [], [], []
    for paint in document.paints:
        if paint.source_id not in selected: continue
        try:
            if paint.unsupported: raise UnsupportedPdfPaintError("; ".join(paint.unsupported))
            if paint.kind not in ("path", "glyph"): raise UnsupportedPdfPaintError(f"Unsupported paint kind {paint.kind!r}; images require separate occurrence processing")
            if not paint.commands:
                skipped.append({"source_id": paint.source_id, "reason": "empty source outline"})
                continue
            style = paint.style
            for group in paint.groups:
                if _alpha(group["opacity"]) != 1: raise UnsupportedPdfPaintError("Group opacity cannot be distributed to child paints")
                if group["mix-blend-mode"] != "normal" or group["isolation"] != "auto": raise UnsupportedPdfPaintError("Group blend/isolation is unsupported")
                if group["mask"] != "none" or group["filter"] != "none": raise UnsupportedPdfPaintError("Group mask/filter is unsupported")
                if group["display"] != "inline": raise UnsupportedPdfPaintError("Group display override is unsupported")
            for key, default in (("mask", "none"), ("filter", "none"), ("mix-blend-mode", "normal"), ("isolation", "auto"), ("display", "inline"), ("visibility", "visible"), ("vector-effect", "none"), ("paint-order", "normal")):
                if style.get(key, default) != default: raise UnsupportedPdfPaintError(f"Unsupported {key}={style[key]!r}")
            fill, stroke = (_color(style[k], style["color"]) for k in ("fill", "stroke"))
            opacity, fa, sa = (_alpha(style.get(k, "1")) for k in ("opacity", "fill-opacity", "stroke-opacity"))
            if opacity == 0 or ((fill == "none" or fa == 0) and (stroke == "none" or sa == 0)):
                skipped.append({"source_id": paint.source_id, "reason": "no visible source paint",
                                "proof": "zero_alpha_or_absent_fill_and_stroke_with_supported_effect_context",
                                "source_fill": fill, "source_stroke": stroke,
                                "source_opacity": opacity, "source_fill_opacity": fa,
                                "source_stroke_opacity": sa})
                continue
            points = [p for command in paint.commands for p in command[1:]]
            if stroke == "none" and points and all(p == points[0] for p in points):
                skipped.append({"source_id": paint.source_id, "reason": "point-only fill has zero area",
                                "proof": "all_path_controls_equal_and_no_stroke",
                                "source_point": list(points[0]),
                                "source_text_unverified": paint.source_text})
                continue
            invisible = prove_source_paint_invisible(paint, region)
            if (fill == "none" and stroke != "none" and len(paint.commands) == 2
                    and paint.commands[0][0] == "M" and paint.commands[1][0] == "L"
                    and paint.commands[0][1] == paint.commands[1][1]
                    and style["stroke-linecap"] == "butt"
                    and style["stroke-linejoin"] in ("miter", "round", "bevel")
                    and style["stroke-dasharray"] == "none"
                    and (zero_line := _exact_zero_length_source_line(paint)) is not None):
                if _number(style["stroke-width"]) < 0:
                    raise UnsupportedPdfPaintError("Negative stroke width")
                if (style["stroke-linejoin"] == "miter"
                        and not 1 <= _number(style["stroke-miterlimit"]) <= 21474.83647):
                    raise UnsupportedPdfPaintError("Source miter limit is outside SVG/native supported range")
                skipped.append({"source_id": paint.source_id,
                                "reason": "zero-length independent butt-capped line has no visible area",
                                "proof": "one_moveto_one_equal_lineto_no_fill_no_dash_butt_cap",
                                "source_point": list(paint.commands[0][1]),
                                "source_linecap": "butt", "source_commands_changed": False,
                                "source_lexical_geometry": zero_line})
                continue
            if invisible is not None:
                skipped.append({"source_id": paint.source_id, "reason": "proved source clip/support outside selected region", "visibility_certificate": invisible})
                continue
            fill_rule_proof = None
            if style["fill-rule"] == "evenodd":
                fill_rule_proof = prove_evenodd_nonzero_equivalent(paint.commands)
                if fill_rule_proof is None: raise UnsupportedPdfPaintError("Evenodd fill requires explicit winding normalization")
            elif style["fill-rule"] != "nonzero":
                raise UnsupportedPdfPaintError("Unsupported source fill-rule")
            width = _number(style["stroke-width"])
            if width < 0: raise UnsupportedPdfPaintError("Negative stroke width")
            source_scale = math.hypot(paint.transform[0], paint.transform[1])
            total = _mul(transform, paint.transform)
            target_scale = math.hypot(total[0], total[1])
            stroke_fields = {}
            dash_pattern = None
            miter_limit = _number(style["stroke-miterlimit"])
            if stroke != "none":
                cap, join = style["stroke-linecap"], style["stroke-linejoin"]
                if cap not in ("butt", "round", "square") or join not in ("miter", "round", "bevel"):
                    raise UnsupportedPdfPaintError("Unsupported stroke cap/join")
                stroke_fields = {"stroke_linecap": cap, "stroke_linejoin": join}
                if join == "miter":
                    if not 1 <= miter_limit <= 21474.83647:
                        raise UnsupportedPdfPaintError("Source miter limit is outside SVG/native supported range")
                    stroke_fields["stroke_miterlimit"] = miter_limit
                for m in (paint.transform, total):
                    a,b,c,d,_,_ = m
                    if not math.isclose(a*a+b*b, c*c+d*d, rel_tol=1e-10, abs_tol=1e-15) or not math.isclose(a*c+b*d, 0, abs_tol=1e-10):
                        raise UnsupportedPdfPaintError("Nonuniform/skew stroke transform requires exact stroke expansion")
                if style["stroke-dasharray"] != "none":
                    raw_pattern = style["stroke-dasharray"].strip()
                    if not re.fullmatch(_NUMBER + r"(?:(?:\s*,\s*|\s+)" + _NUMBER + r")*", raw_pattern):
                        raise UnsupportedPdfPaintError("Only explicit unitless positive dash arrays are supported")
                    dash_pattern = tuple(_number(v) for v in re.split(r"[\s,]+", raw_pattern))
            if fill != "none" and stroke != "none" and (fa != 1 or sa != 1 or opacity != 1): raise UnsupportedPdfPaintError("Combined fill/stroke alpha requires separate verified compositing")
            bounds = _bounds(paint.commands)
            if bounds is None:
                skipped.append({"source_id": paint.source_id, "reason": "source outline has no drawable segment"})
                continue
            if stroke != "none":
                # A control hull encloses every Bezier centerline point.
                # Floating cubic-extremum roots cannot certify an outward
                # bound at a clip boundary, especially after a tight support
                # replaces the old overly generous miter envelope.
                controls = [p for command in paint.commands for p in command[1:]]
                bounds = (min(p[0] for p in controls), min(p[1] for p in controls),
                          max(p[0] for p in controls), max(p[1] for p in controls))
            rectangle_stroke = stroke != "none" and _axis_rectangle(paint.commands)
            envelope = (_stroke_envelope(paint.transform, width, miter_limit, rectangle_stroke,
                                         linecap=cap, linejoin=join)
                        if stroke != "none" else (Fraction(0), Fraction(0)))
            tangent_support = None
            if (stroke != "none" and not rectangle_stroke and join == "miter"
                    and fill == "none" and style["stroke-dasharray"] == "none"):
                try:
                    tangent_support = prove_tangent_stroke_support(
                        paint.commands, paint.transform, width, miter_limit,
                        linecap=cap, linejoin=join, dasharray=style["stroke-dasharray"], fill=fill)
                except PdfTangentStrokeProofError as error:
                    tangent_support = {"status": "not_proven", "reason": str(error),
                                       "fallback": "unchanged_conservative_stroke_envelope"}
                else:
                    envelope = tuple(Fraction(v) for v in tangent_support["axis_support_exact_rationals"])
                # This receipt concerns the original source stroke only. A
                # later supported intersection can still create a fill.
                tangent_support = {**tangent_support, "source_paint_id": paint.source_id,
                                   "applies_to": "original_source_stroke_before_clip_intersection"}
            tangent_record = ({"tangent_stroke_support": tangent_support}
                              if tangent_support is not None else {})
            bounds = _expand_bounds(bounds, envelope)
            rectangles, complex_contexts, clip_proofs = [], [], []
            for context in paint.clips:
                try:
                    rectangles.append(_rect_clip(context))
                except UnsupportedPdfPaintError:
                    complex_contexts.append(context)
            if region is not None: rectangles.append(region)
            geometry_commands = paint.commands
            rectangle_intersection = None
            polygon_intersection = None
            axis_stroke_intersection = None
            annular_intersection = None
            if rectangles:
                effective = (max(r[0] for r in rectangles), max(r[1] for r in rectangles),
                             min(r[2] for r in rectangles), min(r[3] for r in rectangles))
                if effective[2] <= effective[0] or effective[3] <= effective[1] or _disjoint(effective, bounds):
                    skipped.append({"source_id": paint.source_id, "reason": "outside source clip/region",
                                    "proof": "empty_rectangular_clip_intersection_or_disjoint_paint_bounds",
                                    "rectangular_clip_intersection": list(effective), **tangent_record})
                    continue
                if rectangle_stroke and fill == "none":
                    points = [c[1] for c in paint.commands if c[0] != "Z"]
                    if points[-1] == points[0]: points.pop()
                    side_bounds = [_expand_bounds((min(a[0], b[0]), min(a[1], b[1]),
                                                   max(a[0], b[0]), max(a[1], b[1])), envelope)
                                   for a, b in zip(points, points[1:]+points[:1])]
                    if all(_disjoint(side, effective) for side in side_bounds):
                        skipped.append({"source_id": paint.source_id,
                                        "reason": "rectangle stroke lies entirely outside source clip/region",
                                        "proof": "all_four_conservative_side_envelopes_disjoint_from_rectangular_clip_intersection",
                                        "source_side_envelopes": [list(side) for side in side_bounds],
                                        "rectangular_clip_intersection": list(effective)})
                        continue
                if (fill == "none" and stroke != "none" and
                        (bounds[0] < effective[0] or bounds[1] < effective[1] or
                         bounds[2] > effective[2] or bounds[3] > effective[3])):
                    intersection = clip_axis_butt_stroke_to_rect(
                        geometry_commands, effective, source_transform=paint.transform,
                        stroke_width=width, fill=fill, linecap=cap,
                        dasharray=style["stroke-dasharray"], linejoin=join)
                    if intersection is not None:
                        relation = intersection["relation"]
                        axis_stroke_intersection = {**intersection["proof"], "relation": relation}
                        if relation == "outside":
                            skipped.append({"source_id": paint.source_id,
                                            "reason": "zero-area finite butt stroke rectangle intersection",
                                            "axis_butt_stroke_intersection": axis_stroke_intersection,
                                            **tangent_record})
                            continue
                        if relation == "inside":
                            boxes = [tuple(Fraction(v) for v in row["complete_butt_stroke_bounds_exact"])
                                     for row in intersection["proof"]["subpaths"]]
                            bounds = tuple(_outward_float(
                                (max if index >= 2 else min)(box[index] for box in boxes), index >= 2)
                                for index in range(4))
                        else:
                            geometry_commands = intersection["commands"]
                            # Keep all derived contours in one paint. Original
                            # stroke alpha applies once, including compound
                            # subpaths; fill-opacity was irrelevant upstream.
                            fill, stroke, fa = stroke, "none", sa
                            controls = [p for command in geometry_commands for p in command[1:]]
                            bounds = (min(p[0] for p in controls), min(p[1] for p in controls),
                                      max(p[0] for p in controls), max(p[1] for p in controls))
                if fill != "none" and stroke == "none" and _axis_rectangle(paint.commands):
                    clipped = (max(bounds[0], effective[0]), max(bounds[1], effective[1]),
                               min(bounds[2], effective[2]), min(bounds[3], effective[3]))
                    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                        skipped.append({"source_id": paint.source_id, "reason": "zero-area filled rectangle clip intersection"})
                        continue
                    if clipped != bounds:
                        rectangle_intersection = {"method": "exact_axis_aligned_rectangle_intersection",
                                                  "source_rectangle_bounds": list(bounds),
                                                  "rectangular_clip_intersection": list(effective),
                                                  "output_rectangle_bounds": list(clipped),
                                                  "source_commands_changed": True,
                                                  "curve_or_stroke_clipping": False}
                        x0, y0, x1, y1 = clipped
                        geometry_commands = (("M", (x0,y0)), ("L", (x1,y0)), ("L", (x1,y1)), ("L", (x0,y1)), ("Z",))
                        bounds = clipped
                if (fill != "none" and stroke == "none" and
                        (bounds[0] < effective[0] or bounds[1] < effective[1] or
                         bounds[2] > effective[2] or bounds[3] > effective[3])):
                    intersection = clip_convex_fill_to_rect(geometry_commands, effective)
                    if intersection is not None:
                        polygon_intersection = {**intersection["proof"],
                                                "original_source_fill_rule": style["fill-rule"]}
                        geometry_commands = intersection["commands"]
                        if not geometry_commands:
                            skipped.append({"source_id": paint.source_id,
                                            "reason": "zero-area convex fill rectangle intersection",
                                            "polygon_fill_intersection": polygon_intersection})
                            continue
                        controls = [p for command in geometry_commands for p in command[1:]]
                        bounds = (min(p[0] for p in controls), min(p[1] for p in controls),
                                  max(p[0] for p in controls), max(p[1] for p in controls))
            for context in complex_contexts:
                try:
                    clip_proofs.append(_complex_clip_relation(context, bounds))
                except UnsupportedPdfPaintError:
                    # Keep this fallback narrowly scoped to one filled paint
                    # and one complex clip. The shared parser rechecks every
                    # resource attribute and inherited context, so an unknown
                    # effect cannot be laundered into a geometry-only proof.
                    if (len(complex_contexts) != 1 or fill == "none" or stroke != "none"
                            or style["fill-rule"] != "nonzero"):
                        raise
                    shapes = _complex_clip_shapes(context)
                    if len(shapes) != 1:
                        raise
                    clip_commands, clip_rule = shapes[0]
                    intersection = clip_nonzero_annular_fill(
                        geometry_commands, clip_commands,
                        source_fill_rule=style["fill-rule"], clip_fill_rule=clip_rule)
                    if intersection is None:
                        raise
                    geometry_commands = intersection["commands"]
                    # Every remaining rectangle and ROI must contain the
                    # complete derived control hull, without a tolerance.
                    # This is conservative even when cubic extrema would
                    # have tighter bounds, and needs no floating root solve.
                    controls = [p for command in geometry_commands for p in command[1:]]
                    bounds = (min(p[0] for p in controls), min(p[1] for p in controls),
                              max(p[0] for p in controls), max(p[1] for p in controls))
                    annular_intersection = {**intersection["proof"],
                                            "source_clip_id": context["id"],
                                            "output_control_hull_bounds": list(bounds)}
                    clip_proofs.append({"relation": "exact_intersection",
                                        "proof": "nonzero_annular_fill_with_strictly_nested_clip",
                                        "source_clip_id": context["id"],
                                        "source_commands_changed": True})
            if any(p["relation"] == "outside" for p in clip_proofs):
                skipped.append({"source_id": paint.source_id, "reason": "outside source clip/region",
                                "clip_geometry_proofs": clip_proofs, **tangent_record})
                continue
            clip_rounding = []
            for clip_index, rectangle in enumerate(rectangles):
                overhang = [max(0., rectangle[0]-bounds[0]), max(0., rectangle[1]-bounds[1]),
                            max(0., bounds[2]-rectangle[2]), max(0., bounds[3]-rectangle[3])]
                if not any(overhang): continue
                if not rectangle_stroke or max(overhang) > max_clip_overhang:
                    raise UnsupportedPdfPaintError("Path crosses clip/region; exact clipping is not implemented")
                clip_rounding.append({"classification": "bounded_source_rounding", "clip_index": clip_index,
                                      "source_clip": list(rectangle), "source_stroke_bounds": list(bounds),
                                      "overhang_left_top_right_bottom_source_units": overhang,
                                      "max_clip_overhang_source_units": max_clip_overhang,
                                      "maximum_overhang_target_units": max(overhang)*math.hypot(transform[0], transform[1]),
                                      "proof": "axis_aligned_closed_rectangle_stroke; outward conservative transformed axis support",
                                      "exact_noop_clip": False})
            dash_result = None
            if dash_pattern is not None:
                try:
                    if source_scale == 0: raise UnsupportedPdfPaintError("Degenerate stroke transform")
                    # Dash in the path's original user space before applying
                    # its affine. In particular, a scaled integer-coordinate
                    # rectangle must not acquire spurious seam gaps from PDF-
                    # space subtraction roundoff.
                    local_commands = _commands(ET.fromstring(paint.source_xml).get("d", ""), _IDENTITY)
                    dash_result = lower_dashes(local_commands, dash_pattern,
                                              _number(style["stroke-dashoffset"]),
                                              tolerance=dash_tolerance/source_scale)
                except PdfDashError as exc:
                    raise UnsupportedPdfPaintError(str(exc)) from exc
            variants = [(paint.source_id, geometry_commands, fill, stroke, 0,
                         "clipped-stroke-fill" if axis_stroke_intersection and
                         axis_stroke_intersection["relation"] == "intersection" else "original")]
            if dash_result is not None:
                variants = []
                if fill != "none":
                    variants.append((paint.source_id+"-fill", geometry_commands, fill, "none", 0, "fill"))
                if dash_result.commands:
                    variants.append((paint.source_id if fill == "none" else paint.source_id+"-dash-stroke",
                                     tuple((op, *(_point(paint.transform, point) for point in points)) for op, *points in dash_result.commands),
                                     "none", stroke, 1 if fill != "none" else 0, "dash-stroke"))
                else:
                    skipped.append({"source_id": paint.source_id, "reason": "dash pattern has no visible stroke run", "dash_lowering": dash_result.provenance})
            for object_id, source_commands, part_fill, part_stroke, suborder, part in variants:
                commands = []
                for op, *points in source_commands:
                    points = [_point(transform, point) for point in points]
                    if op == "Z": commands.append({"close": {}})
                    elif op in ("M", "L"): commands.append({"moveTo" if op == "M" else "lineTo": {"x": points[0][0], "y": points[0][1]}})
                    else: commands.append({"cubicTo": {"x1": points[0][0], "y1": points[0][1], "x2": points[1][0], "y2": points[1][1], "x": points[2][0], "y": points[2][1]}})
                part_stroke_fields = stroke_fields if part_stroke != "none" else {}
                objects.append({"id": object_id, "kind": "path", "z_index": paint.paint_index, "commands": commands,
                                "style": {"fill": part_fill, "stroke": part_stroke, "stroke_width": width*target_scale if part_stroke != "none" else 0,
                                          "opacity": opacity*(fa if part_fill != "none" else sa), **part_stroke_fields}})
                record = {"object_id": object_id, "source_paint_id": paint.source_id, "source_paint_part": part,
                          "source_paint_suborder": suborder, "source_svg_sha256": document.source_sha256,
                          "source_paint_index": paint.paint_index, "source_xml_path": list(paint.xml_path),
                          "source_instance_path": list(paint.xml_path),
                          "source_element_id": paint.source_element_id, "resource_id": paint.resource_id,
                          "reference_chain": list(paint.reference_chain), "source_kind": paint.kind,
                          "source_text_unverified": paint.source_text, "text_editable": False,
                          "geometry_method": ("finite source butt stroke lowered to one rectangular compound fill"
                                              if part == "clipped-stroke-fill" else
                                              "source Bezier controls; exact affine and quadratic degree elevation"),
                          "source_transform": list(paint.transform), "target_transform": list(transform),
                          "clip_context": list(paint.clips), "group_context": list(paint.groups),
                          "fill_rule_equivalence": fill_rule_proof, "clip_boundary_rounding": clip_rounding,
                          "clip_geometry_proofs": clip_proofs,
                          "rectangle_fill_intersection": rectangle_intersection,
                          "polygon_fill_intersection": polygon_intersection,
                          "axis_butt_stroke_intersection": axis_stroke_intersection,
                          "annular_fill_intersection": annular_intersection,
                          "stroke_native_fields": part_stroke_fields,
                          "stroke_bounds_proof": ({
                              "centerline": "independent_source_axis_segments",
                              "support": "union_of_exact_finite_butt_stroke_rectangles",
                              "source_bounds_outward": list(bounds),
                          } if part_stroke_fields and axis_stroke_intersection else {
                              "centerline": "source_Bezier_control_hull",
                              "support": "style_aware_affine_support_rounded_outward",
                              "axis_support_exact_rationals": [str(v) for v in envelope],
                              "source_bounds_outward": list(bounds),
                          } if part_stroke_fields else None),
                          "stroke_visual_verification_required": bool(part_stroke_fields),
                          "stroke_preview_renderer_support": "not_verified_or_unsupported" if part_stroke_fields else "not_applicable",
                          "stroke_miterlimit_native_quantization": 1e-5 if "stroke_miterlimit" in part_stroke_fields else None}
                record.update(tangent_record)
                if part == "dash-stroke":
                    record["dash_lowering"] = dash_result.provenance
                    record["dash_coordinate_system"] = "original SVG path user space, before source affine"
                    record["dash_maximum_arc_error_source_units"] = dash_result.provenance["maximum_arc_position_error_upper"] * source_scale
                    record["dash_maximum_arc_error_target_units"] = dash_result.provenance["maximum_arc_position_error_upper"] * target_scale
                provenance.append(record)
        except PdfSourceError as exc:
            raise UnsupportedPdfPaintError(f"{paint.source_id} (paint {paint.paint_index}): {exc}") from exc
    return PdfOutlineResult(objects, provenance, skipped, tuple(p.source_id for p in document.paints if p.source_id in selected), len(document.paints))
