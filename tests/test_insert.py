"""Native PPTX insertion fits the source canvas without flattening its content."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from figure_rebuild import package
from figure_rebuild.paths import python_environment
from test_package import (A, CT, NS, P, R, connector, data, fixture, group,
                          picture, shape, slide, write_archive)


EMU = 9525
SVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"


def transform(element, rectangle, *, child_rectangle=None, **attributes):
    """Supply explicit native coordinates, measured in CSS pixels at 96 dpi."""
    props = element.find("p:grpSpPr", NS) if element.tag == f"{{{P}}}grpSp" else element.find("p:spPr", NS)
    xfrm = ET.SubElement(props, f"{{{A}}}xfrm", attributes)
    x, y, width, height = rectangle
    ET.SubElement(xfrm, f"{{{A}}}off", x=str(round(x * EMU)), y=str(round(y * EMU)))
    ET.SubElement(xfrm, f"{{{A}}}ext", cx=str(round(width * EMU)), cy=str(round(height * EMU)))
    if child_rectangle is not None:
        x, y, width, height = child_rectangle
        ET.SubElement(xfrm, f"{{{A}}}chOff", x=str(round(x * EMU)), y=str(round(y * EMU)))
        ET.SubElement(xfrm, f"{{{A}}}chExt", cx=str(round(width * EMU)), cy=str(round(height * EMU)))
    return element


def styled_text(identity, name, rectangle):
    element = transform(shape(identity, name, text="Editable native text"), rectangle)
    ET.SubElement(element.find("p:spPr", NS), f"{{{A}}}ln", w="19050")
    body = element.find("p:txBody", NS)
    body.find("a:bodyPr", NS).attrib.update(lIns="38100", rIns="19050", tIns="9525", bIns="57150")
    paragraph = body.find("a:p", NS)
    props = ET.Element(f"{{{A}}}pPr", marL="38100", marR="19050", indent="-9525")
    before = ET.SubElement(props, f"{{{A}}}spcBef")
    ET.SubElement(before, f"{{{A}}}spcPts", val="1000")
    lines = ET.SubElement(props, f"{{{A}}}lnSpc")
    ET.SubElement(lines, f"{{{A}}}spcPct", val="120000")
    ET.SubElement(props, f"{{{A}}}defRPr", sz="1800")
    paragraph.insert(0, props)
    run = paragraph.find("a:r", NS)
    run.insert(0, ET.Element(f"{{{A}}}rPr", sz="2400", kern="1400", spc="100", baseline="10000"))
    ET.SubElement(paragraph, f"{{{A}}}endParaRPr", sz="1200")
    return element


def curved_shape():
    element = transform(shape(8, "editable-curve"), (450, 120, 200, 100), rot="5400000", flipV="1")
    geometry = ET.SubElement(element.find("p:spPr", NS), f"{{{A}}}custGeom")
    for name in ("avLst", "gdLst", "ahLst", "cxnLst"):
        ET.SubElement(geometry, f"{{{A}}}{name}")
    ET.SubElement(geometry, f"{{{A}}}rect", l="0", t="0", r="r", b="b")
    path = ET.SubElement(ET.SubElement(geometry, f"{{{A}}}pathLst"), f"{{{A}}}path", w="1000", h="500")
    ET.SubElement(ET.SubElement(path, f"{{{A}}}moveTo"), f"{{{A}}}pt", x="0", y="500")
    segment = ET.SubElement(path, f"{{{A}}}cubicBezTo")
    for x, y in ((200, 0), (600, 300), (1000, 100)):
        ET.SubElement(segment, f"{{{A}}}pt", x=str(x), y=str(y))
    return element


class PlacementFitTests(unittest.TestCase):
    def test_shared_fit_centers_in_both_axes_and_supports_enlargement(self):
        from figure_rebuild.placement import fit_placement
        cases = [
            ({"width": 800, "height": 400}, [100, 50, 400, 300], .5, [100, 100, 400, 200]),
            ({"width": 400, "height": 800}, [50, 80, 600, 600], .75, [200, 80, 300, 600]),
            ({"width": 800, "height": 400}, [0, 0, 1200, 800], 1.5, [0, 100, 1200, 600]),
        ]
        for source, requested, expected_scale, expected_rectangle in cases:
            with self.subTest(source=source, requested=requested):
                result = fit_placement(source, {"width": 1200, "height": 800}, requested)
                self.assertEqual(result["scale"], expected_scale)
                self.assertEqual(result["requested"], requested)
                self.assertEqual(result["placement"], expected_rectangle)

    def test_shared_fit_rejects_invalid_canvas_dimensions(self):
        from figure_rebuild.placement import fit_placement
        valid = {"width": 1200, "height": 800}
        for dimension in (0, -1, True, float("nan"), float("inf")):
            for invalid_source in (True, False):
                with self.subTest(dimension=dimension, source=invalid_source):
                    invalid = {"width": dimension, "height": 800}
                    with self.assertRaises(ValueError):
                        fit_placement(invalid if invalid_source else valid,
                                      valid if invalid_source else invalid, [0, 0, 200, 100])


class NativeInsertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.base = self.directory / "base.pptx"
        self.source = self.directory / "source.pptx"
        self.output = self.directory / "inserted.pptx"
        self.target = "ppt/slides/native-target.xml"
        self.source_part = "ppt/slides/source.xml"
        self.original_title = styled_text(2, "retained-title", (10, 10, 300, 30))
        self.base_files = fixture([
            (257, "ppt/slides/unrelated.xml", slide([shape(2, "unrelated-content")])),
            (900, self.target, slide([self.original_title, shape(9, "replace-me")]))],
            size=(1200 * EMU, 800 * EMU),
            extras={"customXml/item.xml": b"opaque existing bytes"})
        nested = transform(group(5, "inner-group", [
            transform(shape(6, "inner-shape"), (8, 4, 10, 6))]),
            (20, 10, 80, 50), child_rectangle=(4, 2, 40, 25), flipV="1")
        self.outer = transform(group(3, "outer-group", [
            styled_text(4, "group-label", (10, 20, 40, 30)), nested,
            transform(connector(7, "native-link", 4, 6), (15, 20, 30, 20))]),
            (100, 60, 320, 180), child_rectangle=(0, 0, 160, 90), rot="1800000", flipH="1")
        self.curve = curved_shape()
        image = transform(picture(9, "cropped-vector", "rIdPng"), (500, 250, 100, 80), flipH="1")
        fill = image.find("p:blipFill", NS)
        ET.SubElement(fill, f"{{{A}}}srcRect", l="12500", r="25000", t="10000", b="20000")
        extensions = ET.SubElement(fill.find("a:blip", NS), f"{{{A}}}extLst")
        extension = ET.SubElement(extensions, f"{{{A}}}ext", uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}")
        ET.SubElement(extension, f"{{{SVG}}}svgBlip", {f"{{{R}}}embed": "rIdSvg"})
        self.objects = [styled_text(2, "editable-label", (40, 60, 160, 80)), self.outer, self.curve, image]
        self.png = b"\x89PNG\r\n\x1a\noriginal fallback bytes"
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><path d="M0 0L20 10"/></svg>'
        self.source_files = fixture([(256, self.source_part, slide(self.objects))],
            size=(800 * EMU, 400 * EMU),
            extras={"ppt/media/source.png": self.png, "ppt/media/source.svg": self.svg},
            slide_rels={self.source_part: [
                {"Id": "rIdPng", "Type": R + "/image", "Target": "../media/source.png"},
                {"Id": "rIdSvg", "Type": R + "/image", "Target": "../media/source.svg"}]})
        types = ET.fromstring(self.source_files["[Content_Types].xml"])
        ET.SubElement(types, f"{{{CT}}}Default", Extension="svg", ContentType="image/svg+xml")
        self.source_files["[Content_Types].xml"] = data(types)
        self.write_inputs()

    def write_inputs(self):
        write_archive(self.base, self.base_files)
        write_archive(self.source, self.source_files)

    def merge(self, placement=(100, 50, 400, 300), **overrides):
        options = dict(base=self.base, overlay=self.source, output=self.output, slide_id=900,
                       base_sha256=hashlib.sha256(self.base.read_bytes()).hexdigest(), placement=placement)
        options.update(overrides)
        return package.merge_overlay(**options)

    def result(self):
        with zipfile.ZipFile(self.output) as archive:
            return ET.fromstring(archive.read(self.target))

    def assert_merge_rejected(self, message, **overrides):
        originals = {path: path.read_bytes() for path in (self.base, self.source)}
        with self.assertRaisesRegex(package.PackageError, message):
            self.merge(**overrides)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".merge-receipt.json").exists())
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    @staticmethod
    def named(root, name):
        for element in root.iter():
            properties = package.native_properties(element)
            if properties is not None and properties.get("name") == name:
                return element
        raise AssertionError("Missing native object: " + name)

    def assert_rectangle(self, element, expected, *, child=False):
        if element.tag == f"{{{P}}}graphicFrame":
            xfrm = element.find("p:xfrm", NS)
        else:
            props = element.find("p:grpSpPr", NS) if element.tag == f"{{{P}}}grpSp" else element.find("p:spPr", NS)
            xfrm = props.find("a:xfrm", NS)
        off = xfrm.find("a:chOff" if child else "a:off", NS)
        ext = xfrm.find("a:chExt" if child else "a:ext", NS)
        actual = [int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))]
        for written, precise in zip(actual, expected):
            self.assertAlmostEqual(written, precise * EMU, delta=.5)

    def test_different_page_sizes_contain_centered_and_editable(self):
        self.merge()
        root = self.result()
        # 800x400 into 400x300 has scale 0.5, with a 50px vertical inset.
        label = self.named(root, "editable-label")
        self.assert_rectangle(label, (120, 130, 80, 40))
        self.assertEqual(label.find("p:txBody/a:p/a:r/a:t", NS).text, "Editable native text")
        self.assertEqual(package.inspect_pptx(self.output)["slide_size_emu"],
                         {"cx": 1200 * EMU, "cy": 800 * EMU})

    def test_enlargement_scales_geometry_and_text(self):
        self.merge(placement=(0, 0, 1200, 800))
        label = self.named(self.result(), "editable-label")
        self.assert_rectangle(label, (60, 190, 240, 120))
        self.assertEqual(label.find(".//a:rPr", NS).get("sz"), "3600")
        self.assertEqual(label.find("p:spPr/a:ln", NS).get("w"), "28575")

    def test_native_text_stroke_insets_and_absolute_spacing_scale_together(self):
        self.merge()
        label = self.named(self.result(), "editable-label")
        run = label.find(".//a:rPr", NS)
        self.assertEqual({key: run.get(key) for key in ("sz", "kern", "spc", "baseline")},
                         {"sz": "1200", "kern": "700", "spc": "50", "baseline": "10000"})
        self.assertEqual(label.find(".//a:defRPr", NS).get("sz"), "900")
        self.assertEqual(label.find(".//a:endParaRPr", NS).get("sz"), "600")
        self.assertEqual(label.find("p:spPr/a:ln", NS).get("w"), "9525")
        body = label.find("p:txBody/a:bodyPr", NS)
        for key, precise in zip(("lIns", "rIns", "tIns", "bIns"), (19050, 9525, 4762.5, 28575)):
            self.assertAlmostEqual(int(body.get(key)), precise, delta=.5)
        props = label.find(".//a:pPr", NS)
        for key, precise in zip(("marL", "marR", "indent"), (19050, 9525, -4762.5)):
            self.assertAlmostEqual(int(props.get(key)), precise, delta=.5)
        self.assertEqual(label.find(".//a:spcPts", NS).get("val"), "500")
        self.assertEqual(label.find(".//a:spcPct", NS).get("val"), "120000")

    def test_nested_groups_keep_coordinate_ratios_and_rotation_flip(self):
        self.merge()
        root = self.result()
        outer = self.named(root, "outer-group")
        self.assert_rectangle(outer, (150, 130, 160, 90))
        self.assert_rectangle(outer, (0, 0, 80, 45), child=True)
        xfrm = outer.find("p:grpSpPr/a:xfrm", NS)
        self.assertEqual((xfrm.get("rot"), xfrm.get("flipH")), ("1800000", "1"))
        inner = self.named(root, "inner-group")
        self.assert_rectangle(inner, (10, 5, 40, 25))
        self.assert_rectangle(inner, (2, 1, 20, 12.5), child=True)
        self.assertEqual(inner.find("p:grpSpPr/a:xfrm", NS).get("flipV"), "1")
        self.assert_rectangle(self.named(root, "inner-shape"), (4, 2, 5, 3))
        label = self.named(root, "group-label")
        self.assert_rectangle(label, (5, 10, 20, 15))
        self.assertEqual(label.find(".//a:rPr", NS).get("sz"), "1200")

    def test_custom_curve_coordinates_unchanged_while_frame_scales(self):
        original = data(self.curve.find("p:spPr/a:custGeom", NS))
        self.merge()
        curve = self.named(self.result(), "editable-curve")
        self.assert_rectangle(curve, (325, 160, 100, 50))
        self.assertEqual(data(curve.find("p:spPr/a:custGeom", NS)), original)
        self.assertEqual(curve.find("p:spPr/a:xfrm", NS).attrib, {"rot": "5400000", "flipV": "1"})

    def test_connector_endpoints_resolve_to_imported_native_shapes(self):
        self.merge()
        root = self.result()
        ids = [element.get("id") for element in root.findall(".//p:cNvPr", NS)]
        self.assertEqual(len(ids), len(set(ids)))
        link = self.named(root, "native-link")
        for connection, destination in (("stCxn", "group-label"), ("endCxn", "inner-shape")):
            self.assertEqual(link.find(".//a:" + connection, NS).get("id"),
                             package.native_properties(self.named(root, destination)).get("id"))
        self.assert_rectangle(link, (7.5, 10, 15, 10))

    def test_image_crop_and_svg_png_source_bytes_preserved(self):
        self.merge()
        image = self.named(self.result(), "cropped-vector")
        self.assert_rectangle(image, (350, 225, 50, 40))
        self.assertEqual(image.find("p:blipFill/a:srcRect", NS).attrib,
                         {"l": "12500", "r": "25000", "t": "10000", "b": "20000"})
        self.assertEqual(image.find("p:spPr/a:xfrm", NS).get("flipH"), "1")
        with zipfile.ZipFile(self.output) as archive:
            relationships = ET.fromstring(archive.read(package.relationship_part(self.target)))
            by_id = {element.get("Id"): element for element in relationships}
            png_id = image.find("p:blipFill/a:blip", NS).get(f"{{{R}}}embed")
            svg_id = image.find(f".//{{{SVG}}}svgBlip").get(f"{{{R}}}embed")
            self.assertNotEqual(png_id, svg_id)
            for rid, expected in ((png_id, self.png), (svg_id, self.svg)):
                part = package.resolve_target(self.target, by_id[rid])
                self.assertEqual(archive.read(part), expected)

    def test_uninvolved_slides_base_objects_and_inputs_are_unchanged(self):
        old_base, old_source = self.base.read_bytes(), self.source.read_bytes()
        self.merge()
        self.assertEqual(self.base.read_bytes(), old_base)
        self.assertEqual(self.source.read_bytes(), old_source)
        self.assertEqual(data(self.named(self.result(), "retained-title")), data(self.original_title))
        with zipfile.ZipFile(self.base) as before, zipfile.ZipFile(self.output) as after:
            for member in ("ppt/slides/unrelated.xml", "ppt/presentation.xml", "customXml/item.xml"):
                self.assertEqual(after.read(member), before.read(member))
                self.assertEqual(after.getinfo(member).date_time, before.getinfo(member).date_time)
                self.assertEqual(after.getinfo(member).comment, before.getinfo(member).comment)

    def test_invalid_placement_fails_without_publishing_output_or_receipt(self):
        invalid = ([], [0, 0, 10], [0, 0, 0, 10], [0, 0, -1, 10], [-1, 0, 10, 10],
                   [0, 0, 1201, 10], [0, 799, 10, 2], [0, 0, float("nan"), 10],
                   [0, 0, float("inf"), 10], [True, 0, 10, 10])
        for placement in invalid:
            with self.subTest(placement=placement):
                with self.assertRaises((package.PackageError, ValueError)):
                    self.merge(placement=placement)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_suffix(".merge-receipt.json").exists())

    def test_invalid_slide_dimensions_fail_without_publishing(self):
        for which, files in (("source", self.source_files), ("base", self.base_files)):
            original = files["ppt/presentation.xml"]
            for value in ("0", "-1", "nan", "inf"):
                with self.subTest(input=which, width=value):
                    root = ET.fromstring(original)
                    root.find("p:sldSz", NS).set("cx", value)
                    files["ppt/presentation.xml"] = data(root)
                    self.write_inputs()
                    with self.assertRaises((package.PackageError, ValueError)):
                        self.merge()
                    self.assertFalse(self.output.exists())
                    self.assertFalse(self.output.with_suffix(".merge-receipt.json").exists())
            files["ppt/presentation.xml"] = original
            self.write_inputs()

    def test_output_cannot_replace_either_input(self):
        originals = {path: path.read_bytes() for path in (self.base, self.source)}
        for path in originals:
            with self.subTest(output=path), self.assertRaisesRegex(package.PackageError, "output must differ"):
                self.merge(output=path)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_public_insert_cli_works_without_configured_artifact_runtime(self):
        environment = python_environment()
        environment.update(FIGURE_REBUILD_CONFIG=str(self.directory / "absent-runtime.json"),
                           RUNTIME_NODE="/missing/node", RUNTIME_PYTHON="/missing/python",
                           RUNTIME_NODE_MODULES="/missing/modules", PRESENTATION_SKILL_DIR="/missing/skill")
        receipt = self.directory / "custom-receipt.json"
        completed = subprocess.run([sys.executable, "-m", "figure_rebuild", "insert",
            "--input", str(self.source), "--base", str(self.base), "--slide-id", "900",
            "--placement", "100", "50", "400", "300", "--output", str(self.output),
            "--replace-id", "replace-me", "--prefix", "figure-v2", "--receipt", str(receipt)],
            capture_output=True, text=True, env=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(self.output.is_file())
        self.assertTrue(receipt.is_file())
        self.assertEqual(json.loads(receipt.read_text())["output_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertIn(str(self.output), json.dumps(report))
        root = self.result()
        self.assert_rectangle(self.named(root, "figure-v2:editable-label"), (120, 130, 80, 40))
        self.assertFalse(any(properties.get("name") == "replace-me"
                             for properties in root.findall(".//p:cNvPr", NS)))

    def test_public_insert_cli_stale_base_hash_fails_without_output(self):
        completed = subprocess.run([sys.executable, "-m", "figure_rebuild", "insert",
            "--input", str(self.source), "--base", str(self.base), "--slide-id", "900",
            "--placement", "100", "50", "400", "300", "--output", str(self.output),
            "--base-sha256", "f" * 64], capture_output=True, text=True, env=python_environment())
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("SHA256", completed.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".merge-receipt.json").exists())

    def test_lower_package_cli_accepts_placement(self):
        completed = subprocess.run([sys.executable, "-m", "figure_rebuild.package", "merge",
            "--overlay", str(self.source), "--base", str(self.base), "--slide-id", "900",
            "--placement", "100", "50", "400", "300", "--output", str(self.output),
            "--base-sha256", hashlib.sha256(self.base.read_bytes()).hexdigest()],
            capture_output=True, text=True, env=python_environment())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_rectangle(self.named(self.result(), "editable-label"), (120, 130, 80, 40))

    def test_empty_root_transforms_from_native_exporters_are_accepted(self):
        for files, part in ((self.source_files, self.source_part), (self.base_files, self.target)):
            root = ET.fromstring(files[part])
            ET.SubElement(root.find("p:cSld/p:spTree/p:grpSpPr", NS), f"{{{A}}}xfrm")
            files[part] = data(root)
        self.write_inputs()
        old_base, old_source = self.base.read_bytes(), self.source.read_bytes()
        self.merge()
        self.assert_rectangle(self.named(self.result(), "editable-label"), (120, 130, 80, 40))
        self.assertEqual(self.base.read_bytes(), old_base)
        self.assertEqual(self.source.read_bytes(), old_source)

    def test_nonidentity_slide_root_transforms_are_rejected(self):
        for which, files, part in (("source", self.source_files, self.source_part),
                                  ("base", self.base_files, self.target)):
            original = files[part]
            for operation in ("rotation", "translation", "stretch"):
                with self.subTest(input=which, operation=operation):
                    root = ET.fromstring(original)
                    props = root.find("p:cSld/p:spTree/p:grpSpPr", NS)
                    xfrm = ET.SubElement(props, f"{{{A}}}xfrm")
                    if operation == "rotation":
                        xfrm.set("rot", "5400000")
                    else:
                        ET.SubElement(xfrm, f"{{{A}}}off", x="9525" if operation == "translation" else "0", y="0")
                        ET.SubElement(xfrm, f"{{{A}}}ext", cx="95250", cy="95250")
                        ET.SubElement(xfrm, f"{{{A}}}chOff", x="0", y="0")
                        ET.SubElement(xfrm, f"{{{A}}}chExt", cx="190500" if operation == "stretch" else "95250", cy="95250")
                    files[part] = data(root)
                    self.write_inputs()
                    self.assert_merge_rejected("nonidentity root")
            files[part] = original
            self.write_inputs()

    def test_missing_or_partial_object_transforms_are_rejected(self):
        original = self.source_files[self.source_part]
        for name, omitted in (("editable-label", "xfrm"), ("editable-label", "off"),
                              ("editable-label", "ext"), ("inner-shape", "xfrm")):
            with self.subTest(object=name, omitted=omitted):
                root = ET.fromstring(original)
                properties = self.named(root, name).find("p:spPr", NS)
                xfrm = properties.find("a:xfrm", NS)
                if omitted == "xfrm":
                    properties.remove(xfrm)
                else:
                    xfrm.remove(xfrm.find("a:" + omitted, NS))
                self.source_files[self.source_part] = data(root)
                self.write_inputs()
                self.assert_merge_rejected("explicit.*transform")

    def test_zero_group_child_extents_are_rejected(self):
        original = self.source_files[self.source_part]
        for name, axis in (("outer-group", "cx"), ("inner-group", "cy")):
            with self.subTest(group=name, axis=axis):
                root = ET.fromstring(original)
                self.named(root, name).find("p:grpSpPr/a:xfrm/a:chExt", NS).set(axis, "0")
                self.source_files[self.source_part] = data(root)
                self.write_inputs()
                self.assert_merge_rejected("positive group child extents")

    def test_multislide_sources_are_rejected(self):
        presentation = ET.fromstring(self.source_files["ppt/presentation.xml"])
        ET.SubElement(presentation.find("p:sldIdLst", NS), f"{{{P}}}sldId",
                      {"id": "257", f"{{{R}}}id": "rIdSecondSourceSlide"})
        relpart = package.relationship_part("ppt/presentation.xml")
        relationships = ET.fromstring(self.source_files[relpart])
        ET.SubElement(relationships, f"{{{package.REL}}}Relationship", Id="rIdSecondSourceSlide",
                      Type=R + "/slide", Target="slides/another-source.xml")
        self.source_files["ppt/presentation.xml"] = data(presentation)
        self.source_files[relpart] = data(relationships)
        self.source_files["ppt/slides/another-source.xml"] = data(slide([shape(2, "second-source-object")]))
        self.write_inputs()
        self.assert_merge_rejected("exactly one slide")

    def test_inherited_placeholder_appearance_is_rejected(self):
        root = ET.fromstring(self.source_files[self.source_part])
        label = self.named(root, "editable-label")
        ET.SubElement(label.find("p:nvSpPr/p:nvPr", NS), f"{{{P}}}ph", type="body", idx="1")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("inherited placeholder")

    def test_font_size_inherited_from_source_theme_is_rejected(self):
        root = ET.fromstring(self.source_files[self.source_part])
        label = self.named(root, "editable-label")
        label.find(".//a:rPr", NS).attrib.pop("sz")
        label.find(".//a:defRPr", NS).attrib.pop("sz")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("inherited font size")

    def test_inherited_theme_line_appearance_is_rejected(self):
        root = ET.fromstring(self.source_files[self.source_part])
        label = self.named(root, "editable-label")
        properties = label.find("p:spPr", NS)
        properties.remove(properties.find("a:ln", NS))
        style = ET.SubElement(label, f"{{{P}}}style")
        reference = ET.SubElement(style, f"{{{A}}}lnRef", idx="1")
        ET.SubElement(reference, f"{{{A}}}schemeClr", val="accent1")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("inherited theme lnRef")

    def test_complex_drawing_effects_are_rejected(self):
        root = ET.fromstring(self.source_files[self.source_part])
        label = self.named(root, "editable-label")
        effects = ET.SubElement(label.find("p:spPr", NS), f"{{{A}}}effectLst")
        shadow = ET.SubElement(effects, f"{{{A}}}outerShdw", blurRad="19050", dist="19050", dir="5400000")
        ET.SubElement(shadow, f"{{{A}}}srgbClr", val="000000")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("drawing effects")

    def test_font_sizes_outside_native_range_after_scaling_are_rejected(self):
        original = self.source_files[self.source_part]
        for size, placement in (("100", (100, 50, 400, 300)), ("300000", (0, 0, 1200, 800))):
            with self.subTest(size=size, placement=placement):
                root = ET.fromstring(original)
                self.named(root, "editable-label").find(".//a:rPr", NS).set("sz", size)
                self.source_files[self.source_part] = data(root)
                self.write_inputs()
                self.assert_merge_rejected("text sz.*outside.*range", placement=placement)

    def test_native_table_materializes_and_scales_default_cell_margins(self):
        root = ET.fromstring(self.source_files[self.source_part])
        frame = ET.SubElement(root.find("p:cSld/p:spTree", NS), f"{{{P}}}graphicFrame")
        nv = ET.SubElement(frame, f"{{{P}}}nvGraphicFramePr")
        ET.SubElement(nv, f"{{{P}}}cNvPr", id="10", name="editable-table")
        ET.SubElement(nv, f"{{{P}}}cNvGraphicFramePr")
        ET.SubElement(nv, f"{{{P}}}nvPr")
        xfrm = ET.SubElement(frame, f"{{{P}}}xfrm")
        ET.SubElement(xfrm, f"{{{A}}}off", x=str(100 * EMU), y=str(310 * EMU))
        ET.SubElement(xfrm, f"{{{A}}}ext", cx=str(300 * EMU), cy=str(40 * EMU))
        graphic = ET.SubElement(frame, f"{{{A}}}graphic")
        content = ET.SubElement(graphic, f"{{{A}}}graphicData", uri=A + "/table")
        table = ET.SubElement(content, f"{{{A}}}tbl")
        ET.SubElement(table, f"{{{A}}}tblPr")
        grid = ET.SubElement(table, f"{{{A}}}tblGrid")
        for width in (200, 100):
            ET.SubElement(grid, f"{{{A}}}gridCol", w=str(width * EMU))
        row = ET.SubElement(table, f"{{{A}}}tr", h=str(40 * EMU))
        for value in ("First cell", "Second cell"):
            cell = ET.SubElement(row, f"{{{A}}}tc")
            body = ET.SubElement(cell, f"{{{A}}}txBody")
            ET.SubElement(body, f"{{{A}}}bodyPr")
            ET.SubElement(body, f"{{{A}}}lstStyle")
            run = ET.SubElement(ET.SubElement(body, f"{{{A}}}p"), f"{{{A}}}r")
            ET.SubElement(run, f"{{{A}}}rPr", sz="1800")
            ET.SubElement(run, f"{{{A}}}t").text = value
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.merge()
        imported = self.named(self.result(), "editable-table")
        self.assert_rectangle(imported, (150, 255, 150, 20))
        self.assertEqual([int(item.get("w")) for item in imported.findall(".//a:gridCol", NS)],
                         [100 * EMU, 50 * EMU])
        self.assertEqual(imported.find(".//a:tr", NS).get("h"), str(20 * EMU))
        self.assertEqual([item.get("sz") for item in imported.findall(".//a:rPr", NS)], ["900", "900"])
        for cell in imported.findall(".//a:tc", NS):
            properties = cell.find("a:tcPr", NS)
            self.assertIsNotNone(properties)
            self.assertEqual(properties.attrib, {"marL": "45720", "marR": "45720", "marT": "22860", "marB": "22860"})
        self.assertEqual([item.text for item in imported.findall(".//a:t", NS)], ["First cell", "Second cell"])

    def test_tab_spacing_from_unrelated_list_level_is_rejected(self):
        root = ET.fromstring(self.source_files[self.source_part])
        label = self.named(root, "editable-label")
        label.find(".//a:pPr", NS).set("lvl", "0")
        label.find(".//a:t", NS).text = "Before\tAfter"
        ET.SubElement(label.find("p:txBody/a:lstStyle", NS), f"{{{A}}}lvl2pPr", defTabSz="914400")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("explicit tab spacing")

    def test_tab_spacing_from_matching_level_or_local_default_scales(self):
        original = self.source_files[self.source_part]
        for level in ("lvl1pPr", "defPPr"):
            with self.subTest(default=level):
                self.output = self.directory / (level + ".pptx")
                root = ET.fromstring(original)
                label = self.named(root, "editable-label")
                label.find(".//a:pPr", NS).set("lvl", "0")
                label.find(".//a:t", NS).text = "Before\tAfter"
                ET.SubElement(label.find("p:txBody/a:lstStyle", NS), f"{{{A}}}{level}", defTabSz="914400")
                self.source_files[self.source_part] = data(root)
                self.write_inputs()
                self.merge()
                imported = self.named(self.result(), "editable-label")
                self.assertEqual(imported.find("p:txBody/a:lstStyle/a:" + level, NS).get("defTabSz"), "457200")
                self.assertEqual(imported.find(".//a:t", NS).text, "Before\tAfter")

    def test_explicit_source_theme_color_references_are_rejected(self):
        root = ET.fromstring(self.source_files[self.source_part])
        properties = self.named(root, "editable-label").find("p:spPr", NS)
        fill = ET.SubElement(properties, f"{{{A}}}solidFill")
        ET.SubElement(fill, f"{{{A}}}schemeClr", val="accent1")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("source theme colors")

    def test_source_theme_typeface_references_are_rejected(self):
        original = self.source_files[self.source_part]
        for language, typeface in (("latin", "+mn-lt"), ("ea", "+mj-ea")):
            with self.subTest(language=language, typeface=typeface):
                root = ET.fromstring(original)
                run = self.named(root, "editable-label").find(".//a:rPr", NS)
                ET.SubElement(run, f"{{{A}}}{language}", typeface=typeface)
                self.source_files[self.source_part] = data(root)
                self.write_inputs()
                self.assert_merge_rejected("source theme fonts")

    def test_theme_line_reference_requires_explicit_width_or_no_fill(self):
        root = ET.fromstring(self.source_files[self.source_part])
        label = self.named(root, "editable-label")
        label.find("p:spPr/a:ln", NS).attrib.pop("w")
        style = ET.SubElement(label, f"{{{P}}}style")
        reference = ET.SubElement(style, f"{{{A}}}lnRef", idx="1")
        ET.SubElement(reference, f"{{{A}}}srgbClr", val="123456")
        self.source_files[self.source_part] = data(root)
        self.write_inputs()
        self.assert_merge_rejected("inherited theme line width")


if __name__ == "__main__":
    unittest.main()
