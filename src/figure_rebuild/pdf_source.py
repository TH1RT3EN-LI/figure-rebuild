"""Read-only extraction of path/glyph paints from MuPDF outlined SVG.

This is a bounded source preprocessor, not an SVG renderer or text recognizer.
It preserves Bezier control points, local resource identity, painter order and
unlowered compositing context. ``outline_paths`` is deliberately opt-in and
fails on unsupported effects instead of silently producing a partial figure.
Images remain separate occurrence records for the image extraction pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Sequence
import xml.etree.ElementTree as ET

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path


class PdfSourceError(ValueError):
    """Invalid or unsupported source input; no assets were written."""


class UnsupportedPdfPaintError(PdfSourceError):
    """A source paint cannot be represented faithfully by this converter."""


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
    def __init__(self):
        super().__init__(None)
        self.commands = []

    def _append(self, command):
        if len(self.commands) >= _MAX_COMMANDS:
            raise PdfSourceError("Source path exceeds command budget")
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


def _commands(data, matrix):
    # fontTools approximates arcs. Reject them before parsing: this API promises
    # exact Bezier conversion, including exact quadratic-to-cubic elevation.
    if data.strip() and not re.match(r"\s*[Mm]", data):
        raise PdfSourceError("Source path must begin with moveto")
    rest = _TOKEN.sub("", data)
    if rest.strip(" ,\t\r\n"):
        raise PdfSourceError("Unsupported path syntax (including elliptical arcs); no flattening performed")
    pen = _ExactPen()
    try:
        parse_path(data, TransformPen(pen, matrix))
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


@dataclass(frozen=True)
class PdfSourceDocument:
    source_sha256: str
    view_box: tuple[float, ...]
    paints: tuple[SourcePaint, ...]
    resource_xml: dict[str, str]


@dataclass(frozen=True)
class PdfOutlineResult:
    objects: list[dict]
    provenance: list[dict]
    skipped: list[dict]
    selected_paint_ids: tuple[str, ...]
    source_paint_count: int


def extract_outlined_svg(data: str | bytes) -> PdfSourceDocument:
    """Extract source records without guessing text or writing files.

    Coordinates remain in the root viewBox's user space (PDF points for MuPDF).
    The viewport is metadata, not a second transform. Unsupported paints remain
    explicit records, with their XML and context; definitions are not paints.
    Local ``use`` chains may reference path/image/group resources, never external
    content. XML paths address the expanded use-instance tree, so repeated group
    resources produce distinct identities for every child paint.
    """
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
    definitions = {}
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

    def walk(node, matrix, inherited, clips, groups, errors, location, references=(), source_text=None):
        nonlocal command_count
        tag = _tag(node)
        if tag in ("defs", "clipPath", "mask", "linearGradient", "radialGradient", "pattern", "marker", "filter", "symbol", "title", "desc", "metadata"): return
        style, own_errors = _attributes(node, inherited)
        errors = (*errors, *own_errors)
        if tag in ("path", "use", "image") and any(_tag(child) not in ("title", "desc", "metadata") for child in node):
            errors += ("unsupported child element on leaf paint/reference",)
        if "}" in node.tag and not node.tag.startswith("{"+_NS+"}"):
            errors += ("foreign drawable namespace",)
        matrix = _mul(matrix, _matrix(node.get("transform")))
        if source_text is None: source_text = node.get("data-text")
        if tag == "use":
            matrix = _mul(matrix, (1., 0., 0., 1., _number(node.get("x", "0")), _number(node.get("y", "0"))))
        clip = style.get("clip-path", "none")
        if clip != "none":
            match = re.fullmatch(r"url\(#([^()]+)\)", clip)
            resource = definitions.get(match[1]) if match else None
            clips = (*clips, {"id": match[1] if match else None, "transform": list(matrix),
                              "element": ET.tostring(resource, encoding="unicode") if resource is not None else None,
                              "reference": clip})
        identity = location
        if tag in ("svg", "g", "use"):
            context = {key: style.get(key, default) for key, default in (("opacity", "1"), ("mix-blend-mode", "normal"), ("isolation", "auto"), ("mask", "none"), ("filter", "none"), ("display", "inline"))}
            groups = (*groups, {"id": node.get("id"), "xml_path": list(location), **context})
        if tag == "svg" and node is not root:
            errors += ("nested SVG viewport",)
        elif tag in ("svg", "g"):
            count_before = len(paints)
            for index, child in enumerate(node):
                walk(child, matrix, style, clips, groups, errors, (*location, index), references, source_text)
            if references and len(paints) == count_before:
                # MuPDF can emit empty Type3 glyph resources. Keep the use
                # occurrence auditable; never invent its outline from Unicode.
                paints.append(SourcePaint("svg-paint-" + "-".join(str(v) for v in identity), len(paints), identity,
                                          node.get("id"), references[-1], references,
                                          "glyph" if source_text is not None else "path", (), matrix, style,
                                          clips, groups, source_text, ET.tostring(node, encoding="unicode"), tuple(errors)))
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
                walk(target, matrix, style, clips, groups, errors, location, (*references, resource), source_text)
                return
        kind = "glyph" if tag == "path" and source_text is not None else tag if tag in ("path", "image") else "unsupported"
        commands = ()
        if tag == "path":
            try: commands = _commands(node.get("d", ""), matrix)
            except PdfSourceError as exc: errors += (str(exc),)
        elif tag != "image":
            errors += (f"unsupported paint element {tag!r}",)
        command_count += len(commands)
        if command_count > _MAX_COMMANDS: raise PdfSourceError("Source SVG exceeds total command budget")
        source_id = "svg-paint-" + "-".join(str(v) for v in identity)
        paints.append(SourcePaint(source_id, len(paints), identity, node.get("id"), references[-1] if references else None,
                                  references, kind, commands, matrix, style, clips, groups, source_text,
                                  ET.tostring(node, encoding="unicode"), tuple(errors)))
    walk(root, _IDENTITY, _DEFAULT_STYLE, (), (), (), (0,))
    return PdfSourceDocument(hashlib.sha256(raw).hexdigest(), view_box, tuple(paints),
                             {key: ET.tostring(value, encoding="unicode") for key, value in definitions.items()})


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


def _rect_clip(context):
    if not context["element"]: raise UnsupportedPdfPaintError("Missing or external clip resource")
    root = ET.fromstring(context["element"])
    if _tag(root) != "clipPath" or root.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse":
        raise UnsupportedPdfPaintError("Only userSpaceOnUse rectangular clips can be proved no-op")
    if set(root.attrib) - {"id", "clipPathUnits", "transform"}:
        raise UnsupportedPdfPaintError("Unsupported clipPath attributes")
    children = list(root)
    if len(children) != 1: raise UnsupportedPdfPaintError("Complex clip has multiple children")
    child = children[0]
    matrix = _mul(_mul(context["transform"], _matrix(root.get("transform"))), _matrix(child.get("transform")))
    if _tag(child) == "rect" and not (set(child.attrib)-{"x", "y", "width", "height", "id", "transform"}):
        x, y = _number(child.get("x", "0")), _number(child.get("y", "0"))
        w, h = _number(child.get("width", "0")), _number(child.get("height", "0"))
        if w <= 0 or h <= 0: raise UnsupportedPdfPaintError("Degenerate clip rectangle")
        points = [_point(matrix, p) for p in ((x,y), (x+w,y), (x+w,y+h), (x,y+h))]
    elif _tag(child) == "path" and not (set(child.attrib)-{"d", "id", "transform", "clip-rule"}):
        commands = _commands(child.get("d", ""), matrix)
        if not commands or commands[-1][0] != "Z" or sum(c[0] == "M" for c in commands) != 1 or any(c[0] not in ("M", "L", "Z") for c in commands):
            raise UnsupportedPdfPaintError("Complex clip curve/contour requires explicit geometry support")
        points = [c[1] for c in commands if c[0] != "Z"]
        if len(points) > 1 and points[-1] == points[0]: points.pop()
    else:
        raise UnsupportedPdfPaintError("Complex clip shape requires explicit geometry support")
    xs, ys = {p[0] for p in points}, {p[1] for p in points}
    if len(points) != 4 or len(xs) != 2 or len(ys) != 2 or len(set(points)) != 4 or any(a[0] != b[0] and a[1] != b[1] for a,b in zip(points, points[1:]+points[:1])):
        raise UnsupportedPdfPaintError("Clip is not an axis-aligned rectangle in source space")
    return min(xs), min(ys), max(xs), max(ys)


def outline_paths(document: PdfSourceDocument, *, glyph_mode: str,
                  paint_ids: Sequence[str] | None = None, region: Sequence[float] | None = None,
                  transform: Sequence[float] = _IDENTITY) -> PdfOutlineResult:
    """Lower an explicit selection to native path objects, or raise.

    ``glyph_mode='outline'`` is required, including for ordinary labels. It
    produces editable geometry, never editable text or recovered semantics.
    Rectangular clips/ROI are accepted only when proven disjoint or no-op;
    crossing or complex clips, images and group compositing remain unsupported.
    ``paint_ids`` is an explicit subset, reported as such in the result.
    ``transform`` maps source PDF coordinates into the target canvas.
    """
    if glyph_mode != "outline": raise PdfSourceError("Explicit glyph_mode='outline' is required; live text policy is unchanged")
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
            if style["fill-rule"] != "nonzero": raise UnsupportedPdfPaintError("Evenodd fill requires explicit winding normalization")
            fill, stroke = (_color(style[k], style["color"]) for k in ("fill", "stroke"))
            width = _number(style["stroke-width"])
            if width < 0: raise UnsupportedPdfPaintError("Negative stroke width")
            source_scale = math.hypot(paint.transform[0], paint.transform[1])
            total = _mul(transform, paint.transform)
            target_scale = math.hypot(total[0], total[1])
            stroke_fields = {}
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
                for key, default in (("stroke-dasharray", "none"), ("stroke-dashoffset", "0")):
                    if style[key] != default: raise UnsupportedPdfPaintError(f"Unsupported stroke effect {key}={style[key]!r}")
            opacity, fa, sa = (_alpha(style.get(k, "1")) for k in ("opacity", "fill-opacity", "stroke-opacity"))
            if fill != "none" and stroke != "none" and (fa != 1 or sa != 1 or opacity != 1): raise UnsupportedPdfPaintError("Combined fill/stroke alpha requires separate verified compositing")
            bounds = _bounds(paint.commands)
            if bounds is None:
                skipped.append({"source_id": paint.source_id, "reason": "source outline has no drawable segment"})
                continue
            edge = width*source_scale*max(2, miter_limit/2) if stroke != "none" else 0  # conservative cap/join envelope
            bounds = (bounds[0]-edge, bounds[1]-edge, bounds[2]+edge, bounds[3]+edge)
            rectangles = [_rect_clip(c) for c in paint.clips]
            if region is not None: rectangles.append(region)
            if any(_disjoint(r, bounds) for r in rectangles):
                skipped.append({"source_id": paint.source_id, "reason": "outside source clip/region"})
                continue
            if any(not _contains(r, bounds) for r in rectangles): raise UnsupportedPdfPaintError("Path crosses clip/region; exact clipping is not implemented")
            commands = []
            for op, *points in paint.commands:
                points = [_point(transform, point) for point in points]
                if op == "Z": commands.append({"close": {}})
                elif op in ("M", "L"): commands.append({"moveTo" if op == "M" else "lineTo": {"x": points[0][0], "y": points[0][1]}})
                else: commands.append({"cubicTo": {"x1": points[0][0], "y1": points[0][1], "x2": points[1][0], "y2": points[1][1], "x": points[2][0], "y": points[2][1]}})
            objects.append({"id": paint.source_id, "kind": "path", "z_index": paint.paint_index, "commands": commands,
                            "style": {"fill": fill, "stroke": stroke, "stroke_width": width*target_scale if stroke != "none" else 0, "opacity": opacity*(fa if fill != "none" else sa), **stroke_fields}})
            provenance.append({"object_id": paint.source_id, "source_svg_sha256": document.source_sha256,
                               "source_paint_index": paint.paint_index, "source_xml_path": list(paint.xml_path),
                               "source_instance_path": list(paint.xml_path),
                               "source_element_id": paint.source_element_id, "resource_id": paint.resource_id,
                               "reference_chain": list(paint.reference_chain), "source_kind": paint.kind,
                               "source_text_unverified": paint.source_text, "text_editable": False,
                               "geometry_method": "source Bezier controls; exact affine and quadratic degree elevation",
                               "source_transform": list(paint.transform), "target_transform": list(transform),
                               "clip_context": list(paint.clips), "group_context": list(paint.groups),
                               "stroke_native_fields": stroke_fields,
                               "stroke_visual_verification_required": bool(stroke_fields),
                               "stroke_preview_renderer_support": "not_verified_or_unsupported" if stroke_fields else "not_applicable",
                               "stroke_miterlimit_native_quantization": 1e-5 if "stroke_miterlimit" in stroke_fields else None})
        except PdfSourceError as exc:
            raise UnsupportedPdfPaintError(f"{paint.source_id} (paint {paint.paint_index}): {exc}") from exc
    return PdfOutlineResult(objects, provenance, skipped, tuple(p.source_id for p in document.paints if p.source_id in selected), len(document.paints))
