"""Contain placement and conservative native DrawingML scaling.

Public canvas/box arguments use CSS pixels (96 px per inch).  OOXML geometry
uses EMUs; the import bakes one uniform scale into both geometry and absolute
appearance lengths so editable text and strokes resize along with the shapes.
Image bytes, percentage crops, rotations, flips, and local path coordinates
are deliberately left alone.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping

EMU_PER_PX = 9525
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"p": P, "a": A}
OBJECT_KINDS = {"sp", "pic", "grpSp", "cxnSp", "graphicFrame"}
COORDINATE_LIMIT = 27273042316900


class PlacementError(ValueError):
    """Placement cannot be validated or scaled without losing appearance."""


def _require(condition, message):
    if not condition:
        raise PlacementError(message)


def _finite(value, label):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool),
             f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise PlacementError(f"{label} must be a finite number") from error
    _require(math.isfinite(result), f"{label} must be a finite number")
    return result


def _canvas(value, label):
    if isinstance(value, Mapping):
        values = [value.get("width"), value.get("height")]
    else:
        try:
            values = list(value)
        except TypeError as error:
            raise PlacementError(f"{label} must contain width and height") from error
    _require(len(values) == 2, f"{label} must contain width and height")
    result = [_finite(item, f"{label} {axis}") for item, axis in zip(values, ("width", "height"))]
    _require(all(item > 0 for item in result), f"{label} dimensions must be positive")
    return result


def fit_placement(source_canvas, target_canvas, requested):
    """Validate a target box and center a uniformly scaled source canvas in it.

    Canvases are ``{'width': ..., 'height': ...}`` or two-item sequences; the
    requested box is ``[x, y, width, height]``.  All dimensions use CSS pixels.
    The returned ``placement`` is the actual occupied canvas rectangle.
    """
    sw, sh = _canvas(source_canvas, "source canvas")
    tw, th = _canvas(target_canvas, "target canvas")
    try:
        values = list(requested)
    except TypeError as error:
        raise PlacementError("placement must contain x y width height") from error
    _require(len(values) == 4, "placement must contain x y width height")
    x, y, width, height = [_finite(item, "placement " + name)
                           for item, name in zip(values, ("x", "y", "width", "height"))]
    _require(x >= 0 and y >= 0, "placement coordinates must be nonnegative")
    _require(width > 0 and height > 0, "placement dimensions must be positive")
    _require(x + width <= tw + .001 and y + height <= th + .001,
             "placement must fit within the target slide")
    scale = min(width / sw, height / sh)
    _require(math.isfinite(scale) and scale > 0, "placement scale must be finite and positive")
    actual_width, actual_height = sw * scale, sh * scale
    actual = [x + (width - actual_width) / 2, y + (height - actual_height) / 2,
              actual_width, actual_height]
    _require(all(math.isfinite(item) for item in actual), "placement result must be finite")
    return {"scale": scale, "requested": [x, y, width, height], "placement": actual}


def _local(element):
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else "#comment"


def _integer(element, attribute, label, *, default=None):
    value = element.get(attribute)
    if value is None and default is not None:
        value = str(default)
    _require(value is not None, f"{label} is missing {attribute}")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise PlacementError(f"{label} has an invalid {attribute}") from error


def _scaled(element, attribute, scale, label, *, default=None,
            minimum=-COORDINATE_LIMIT, maximum=COORDINATE_LIMIT, translate=0):
    original = _integer(element, attribute, label, default=default)
    result = original * scale + translate
    _require(math.isfinite(result), f"{label} exceeds the supported coordinate range")
    result = math.floor(result + .5)
    _require(minimum <= result <= maximum,
             f"{label} is outside the supported range after placement")
    element.set(attribute, str(result))


def _transform(element, label, *, group=False):
    children = {}
    expected = {"off", "ext", "chOff", "chExt"} if group else {"off", "ext"}
    for child in element:
        local = _local(child)
        _require(child.tag == f"{{{A}}}{local}" and local in expected and local not in children,
                 f"{label} has an unsupported transform child: {local}")
        children[local] = child
    _require(set(children) == expected, f"{label} requires an explicit complete transform")
    for name, child in children.items():
        attrs = ("x", "y") if name in {"off", "chOff"} else ("cx", "cy")
        for attr in attrs:
            value = _integer(child, attr, label)
            _require(abs(value) <= COORDINATE_LIMIT, f"{label} exceeds the supported coordinate range")
            if name in {"ext", "chExt"}:
                _require(value >= 0, f"{label} has a negative extent")
    return children


def require_identity_shape_tree(tree, label):
    """A slide's root shape tree must use unmodified slide coordinates."""
    properties = tree.find("p:grpSpPr", NS)
    _require(properties is not None, f"{label} has no root group properties")
    transform = properties.find("a:xfrm", NS)
    if transform is None:
        return
    _require(_integer(transform, "rot", label, default=0) % 21600000 == 0
             and transform.get("flipH", "0") in {"0", "false"}
             and transform.get("flipV", "0") in {"0", "false"},
             f"{label} has a nonidentity root shape-tree transform")
    # The exporter writes an empty root xfrm for the default slide coordinate
    # system.  Ordinary imported objects still require complete transforms.
    if not len(transform):
        return
    children = _transform(transform, label + " root shape tree", group=True)
    _require(all(_integer(children["off"], axis, label) == _integer(children["chOff"], axis, label)
                 for axis in ("x", "y"))
             and all(_integer(children["ext"], axis, label) == _integer(children["chExt"], axis, label)
                     for axis in ("cx", "cy")),
             f"{label} has a nonidentity root shape-tree transform")


def _object_transform(element):
    name = _local(element)
    path = {"grpSp": "p:grpSpPr/a:xfrm", "graphicFrame": "p:xfrm"}.get(name, "p:spPr/a:xfrm")
    node = element.find(path, NS)
    _require(node is not None, f"placement requires an explicit transform for {name}")
    return node


def _geometry(element, scale, tx, ty, known_transforms, *, top_level):
    name = _local(element)
    _require(name in OBJECT_KINDS, f"unsupported placement object: {name}")
    transform = _object_transform(element)
    known_transforms.add(transform)
    children = _transform(transform, name, group=name == "grpSp")
    for child_name, child in children.items():
        for attribute in (("x", "y") if child_name in {"off", "chOff"} else ("cx", "cy")):
            translation = (tx if attribute == "x" else ty) if child_name == "off" and top_level else 0
            _scaled(child, attribute, scale, name + " " + child_name,
                    minimum=0 if child_name in {"ext", "chExt"} else -COORDINATE_LIMIT,
                    translate=translation)
    if name == "grpSp":
        _require(all(_integer(children["chExt"], axis, name) > 0 for axis in ("cx", "cy")),
                 "placement requires positive group child extents")
        for child in element:
            if _local(child) not in {"nvGrpSpPr", "grpSpPr", "extLst"}:
                _geometry(child, scale, tx, ty, known_transforms, top_level=False)


def _validate_style(element):
    """Do not silently borrow geometric appearance from the target's theme."""
    if element.find(".//p:ph", NS) is not None:
        raise PlacementError("placement does not support inherited placeholder appearance")
    for shape in element.iter():
        if shape.tag not in {f"{{{P}}}{name}" for name in OBJECT_KINDS}:
            continue
        style = shape.find("p:style", NS)
        if style is None:
            continue
        properties = shape.find("p:spPr", NS)
        for reference, explicit in (("lnRef", ("ln",)),
                                    ("fillRef", ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")),
                                    ("effectRef", ("effectLst", "effectDag"))):
            node = style.find("a:" + reference, NS)
            if node is not None and _integer(node, "idx", reference) != 0:
                _require(properties is not None and any(properties.find("a:" + item, NS) is not None for item in explicit),
                         f"placement cannot preserve inherited theme {reference}; make appearance explicit first")
                if reference == "lnRef":
                    line = properties.find("a:ln", NS)
                    _require(line.get("w") is not None or line.find("a:noFill", NS) is not None,
                             "placement cannot scale inherited theme line width; make it explicit first")


def _validate_text_body(body):
    paragraphs = body.findall("a:p", NS)
    local_style = body.find("a:lstStyle", NS)
    for paragraph in paragraphs:
        properties = paragraph.find("a:pPr", NS)
        level = _integer(properties, "lvl", "paragraph", default=0) if properties is not None else 0
        _require(0 <= level <= 8, "placement encountered an unsupported paragraph level")
        paragraph_defaults = [properties]
        if local_style is not None:
            paragraph_defaults.extend([local_style.find(f"a:lvl{level + 1}pPr", NS),
                                       local_style.find("a:defPPr", NS)])
        defaults = [node.find("a:defRPr", NS) if node is not None else None
                    for node in paragraph_defaults]
        default_size = next((node.get("sz") for node in defaults if node is not None and node.get("sz") is not None), None)
        runs = [child for child in paragraph if child.tag in {f"{{{A}}}r", f"{{{A}}}fld", f"{{{A}}}br"}]
        for run in runs:
            props = run.find("a:rPr", NS)
            size = props.get("sz") if props is not None else None
            _require(size is not None or default_size is not None,
                     "placement cannot scale inherited font size; use explicit run or local paragraph sizes")
        if not runs and len(paragraphs) > 1:
            end = paragraph.find("a:endParaRPr", NS)
            _require(default_size is not None or end is not None and end.get("sz") is not None,
                     "placement cannot scale inherited font size in an empty paragraph")
        if any("\t" in (text.text or "") for text in paragraph.findall(".//a:t", NS)):
            _require(any(node is not None and node.get("defTabSz") is not None
                         for node in paragraph_defaults),
                     "placement requires explicit tab spacing for text containing tabs")


def _appearance(element, scale):
    _validate_style(element)
    for body in element.iter():
        if body.tag in {f"{{{P}}}txBody", f"{{{A}}}txBody"}:
            _validate_text_body(body)
        if body.tag == f"{{{A}}}tc" and body.find("a:tcPr", NS) is None:
            extension = body.find("a:extLst", NS)
            at = list(body).index(extension) if extension is not None else len(body)
            body.insert(at, ET.Element(f"{{{A}}}tcPr"))
    paragraph_kinds = {"pPr", "defPPr"} | {f"lvl{index}pPr" for index in range(1, 10)}
    line_kinds = {"ln", "lnL", "lnR", "lnT", "lnB", "lnTlToBr", "lnBlToTr"}
    body_defaults = {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}
    cell_defaults = {"marL": 91440, "marR": 91440, "marT": 45720, "marB": 45720}
    for node in element.iter():
        name = _local(node)
        if node.tag == f"{{{MC}}}AlternateContent":
            raise PlacementError("placement does not support alternate drawing content")
        if node.tag not in {f"{{{A}}}{name}", f"{{{P}}}{name}"}:
            continue
        if name in {"scene3d", "sp3d", "cell3D", "txXfrm"}:
            raise PlacementError(f"placement does not support {name}")
        if name in {"effectLst", "effectDag"} and len(node):
            raise PlacementError("placement does not support drawing effects; make an explicit flat overlay first")
        if name == "tableStyleId" and (node.text or "").strip():
            raise PlacementError("placement cannot preserve inherited table styles; make cell appearance explicit first")
        if name == "spAutoFit":
            raise PlacementError("placement does not support shape autofit; use explicit text layout")
        if name == "schemeClr":
            raise PlacementError("placement cannot preserve source theme colors; make colors explicit first")
        if name in {"latin", "ea", "cs", "sym", "buFont"} and node.get("typeface", "").startswith(("+mj", "+mn")):
            raise PlacementError("placement cannot preserve source theme fonts; make font families explicit first")
        if name in {"rPr", "defRPr", "endParaRPr"}:
            for attr in ("sz", "kern", "spc"):
                if attr in node.attrib:
                    _scaled(node, attr, scale, "text " + attr,
                            minimum=100 if attr == "sz" else -400000 if attr == "spc" else 0,
                            maximum=400000)
        if name in line_kinds and "w" in node.attrib:
            _scaled(node, "w", scale, "line width", minimum=0, maximum=20116800)
        if name == "bodyPr":
            for attr, default in body_defaults.items():
                _scaled(node, attr, scale, "text inset", default=default,
                        minimum=-2147483648, maximum=2147483647)
            if "spcCol" in node.attrib:
                _scaled(node, "spcCol", scale, "text column spacing", minimum=0, maximum=2147483647)
        if name in paragraph_kinds:
            for attr in ("marL", "marR", "indent", "defTabSz"):
                if attr in node.attrib:
                    _scaled(node, attr, scale, "paragraph " + attr,
                            minimum=-2147483648, maximum=2147483647)
        if name in {"spcPts", "buSzPts"}:
            _scaled(node, "val", scale, name, minimum=0, maximum=20116800)
        if name == "tab":
            _scaled(node, "pos", scale, "tab stop", minimum=-2147483648, maximum=2147483647)
        if name in {"gridCol", "tr"}:
            _scaled(node, "w" if name == "gridCol" else "h", scale, "table " + name, minimum=1)
        if name == "tcPr":
            for attr, default in cell_defaults.items():
                _scaled(node, attr, scale, "cell margin", default=default,
                        minimum=0, maximum=2147483647)
        if name == "tile":
            for attr in ("tx", "ty"):
                if attr in node.attrib:
                    _scaled(node, attr, scale, "image tile offset")


def place_native_objects(objects, fitted):
    """Mutate copied native objects according to a validated contain fit.

    Callers must operate on detached copies and write only after this succeeds.
    All child coordinate systems resize too, preserving group ext/chExt ratios.
    """
    scale = _finite(fitted["scale"], "placement scale")
    _require(scale > 0, "placement scale must be positive")
    tx, ty = (_finite(item, "placement offset") * EMU_PER_PX for item in fitted["placement"][:2])
    known_transforms = set()
    for item in objects:
        _geometry(item, scale, tx, ty, known_transforms, top_level=True)
        for node in item.iter():
            if node.tag in {f"{{{A}}}xfrm", f"{{{P}}}xfrm"}:
                _require(node in known_transforms, "placement encountered an unsupported nested transform")
        _appearance(item, scale)
    return {"native_object_count": sum(1 for item in objects for node in item.iter()
                                        if node.tag in {f"{{{P}}}{name}" for name in OBJECT_KINDS}),
            "transform_count": len(known_transforms),
            "strategy": "uniform scale baked into native geometry and explicit absolute appearance lengths"}
