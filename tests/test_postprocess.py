"""Crop/media preservation regressions using synthetic single-slide packages."""
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image


from figure_rebuild import postprocess, package
P, A, R, REL = package.P, package.A, package.R, package.REL
NS = postprocess.NS


def xml(node):
    return ET.tostring(node, encoding="utf-8", xml_declaration=True)


def png(color):
    stream = BytesIO()
    Image.new("RGB", (144, 42), color).save(stream, format="PNG")
    return stream.getvalue()


def one_slide_package(path, image_bytes, crop=None, frame=(100000, 100000, 1000000, 2700000)):
    """Supply only real package parts consumed by the postprocessor; no authoring API."""
    root = ET.Element(f"{{{P}}}sld")
    tree = ET.SubElement(ET.SubElement(root, f"{{{P}}}cSld"), f"{{{P}}}spTree")
    group_nv = ET.SubElement(tree, f"{{{P}}}nvGrpSpPr")
    ET.SubElement(group_nv, f"{{{P}}}cNvPr", id="1", name="root")
    ET.SubElement(group_nv, f"{{{P}}}cNvGrpSpPr")
    ET.SubElement(group_nv, f"{{{P}}}nvPr")
    ET.SubElement(tree, f"{{{P}}}grpSpPr")
    picture = ET.SubElement(tree, f"{{{P}}}pic")
    nv = ET.SubElement(picture, f"{{{P}}}nvPicPr")
    ET.SubElement(nv, f"{{{P}}}cNvPr", id="2", name="exporter-picture-name")
    ET.SubElement(nv, f"{{{P}}}cNvPicPr")
    ET.SubElement(nv, f"{{{P}}}nvPr")
    fill = ET.SubElement(picture, f"{{{P}}}blipFill")
    ET.SubElement(fill, f"{{{A}}}blip", {f"{{{R}}}embed": "rIdPhoto"})
    if crop is not None:
        attrs = {short: str(math.floor(crop[key] * 100000 + .5)) for key, short in
                 (("left", "l"), ("top", "t"), ("right", "r"), ("bottom", "b"))}
        ET.SubElement(fill, f"{{{A}}}srcRect", attrs)
    ET.SubElement(ET.SubElement(fill, f"{{{A}}}stretch"), f"{{{A}}}fillRect")
    geometry = ET.SubElement(picture, f"{{{P}}}spPr")
    transform = ET.SubElement(geometry, f"{{{A}}}xfrm")
    ET.SubElement(transform, f"{{{A}}}off", x=str(frame[0]), y=str(frame[1]))
    ET.SubElement(transform, f"{{{A}}}ext", cx=str(frame[2]), cy=str(frame[3]))
    rels = ET.Element(f"{{{REL}}}Relationships")
    ET.SubElement(rels, f"{{{REL}}}Relationship", Id="rIdPhoto", Type=R + "/image", Target="../media/original.png")
    payloads = {"ppt/slides/slide1.xml": xml(root),
                "ppt/slides/_rels/slide1.xml.rels": xml(rels),
                "ppt/media/original.png": image_bytes}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in payloads.items():
            archive.writestr(member, content)


class PostprocessTextBaselineTests(unittest.TestCase):
    def test_default_spacing_still_dispatches_renderer_baseline_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            page = ET.Element(f'{{{P}}}sld')
            tree = ET.SubElement(ET.SubElement(page, f'{{{P}}}cSld'), f'{{{P}}}spTree')
            shape = ET.SubElement(tree, f'{{{P}}}sp')
            nv = ET.SubElement(shape, f'{{{P}}}nvSpPr')
            ET.SubElement(nv, f'{{{P}}}cNvPr', id='2', name='plain-label')
            props = ET.SubElement(shape, f'{{{P}}}spPr')
            transform = ET.SubElement(props, f'{{{A}}}xfrm')
            ET.SubElement(transform, f'{{{A}}}off', x='95250', y='95250')
            ET.SubElement(transform, f'{{{A}}}ext', cx='476250', cy='285750')
            body = ET.SubElement(shape, f'{{{P}}}txBody')
            ET.SubElement(body, f'{{{A}}}bodyPr', anchor='t', tIns='0', bIns='0')
            paragraph = ET.SubElement(body, f'{{{A}}}p')
            ET.SubElement(ET.SubElement(paragraph, f'{{{A}}}r'), f'{{{A}}}t').text = 'Text'
            source, output = root / 'before.pptx', root / 'after.pptx'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('ppt/slides/slide1.xml', xml(page))
            box = dict(x=10, y=10, width=50, height=30)
            manifest, mapped = root / 'manifest.json', root / 'objects.json'
            manifest.write_text(json.dumps({'canvas': {'width': 100, 'height': 100}, 'objects': [
                {'id': 'plain-label', 'kind': 'text', 'text': 'Text', 'font_size': 20, 'box': box}]}))
            mapped.write_text(json.dumps({'placement': [0, 0, 100, 100], 'objects': [
                {'id': 'plain-label', 'kind': 'text', 'box': box, 'text_layout': {
                    'native_baseline_ascent': 20, 'baseline_adjustment_px': 1,
                    'renderer_baseline': {'model': 'artifact_presentation_v1',
                                          'first_baseline_px': 19, 'scale': 1}}}]}))
            report = postprocess.process(source, output, manifest, root / 'receipt.json', object_map=mapped)
            self.assertTrue(report['text_layout'][0]['baseline_calibrated'])
            with zipfile.ZipFile(output) as archive:
                actual = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
            body_pr = actual.find('.//p:txBody/a:bodyPr', NS)
            self.assertEqual(body_pr.get('tIns'), '9525')
            self.assertEqual(body_pr.get('bIns'), '-9525')
            self.assertIsNone(actual.find('.//a:lnSpc', NS))


class PostprocessCropTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "synthetic-input.pptx"
        self.output = self.root / "checked-output.pptx"
        self.receipt = self.root / "editability.json"
        self.manifest_path = self.root / "manifest.json"
        self.object_map_path = self.root / "object-map.json"
        self.canvas = {"width": 1442, "height": 422}
        self.original = png("navy")
        # The actual user's portrait panel is cut from a much wider diagram.
        # Nonround fractions ensure exporter quantization is exercised.
        self.crop = {"left": 122/1442, "top": 50/422,
                     "right": (1442-229)/1442, "bottom": (422-329)/422}
        self.object = {"id": "original-photo", "kind": "image", "editable": False,
                       "sha256": hashlib.sha256(self.original).hexdigest(),
                       "path": "sources/original.png", "crop": self.crop,
                       "box": {"x": 122, "y": 50, "width": 107, "height": 279}}

    def run_postprocess(self, object_map=None, objects=None):
        self.manifest_path.write_text(json.dumps({"canvas": self.canvas, "objects": objects or [self.object]}), encoding="utf-8")
        return postprocess.process(self.source, self.output, self.manifest_path, self.receipt, object_map=object_map)

    def assert_rejected_without_publication(self, expected):
        with self.assertRaisesRegex(ValueError, expected):
            self.run_postprocess()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

    def test_normalized_crop_and_original_embedded_bytes_are_preserved(self):
        one_slide_package(self.source, self.original, self.crop)
        original_input = self.source.read_bytes()
        report = self.run_postprocess()
        self.assertEqual(self.source.read_bytes(), original_input)
        self.assertEqual(report["raster_count"], 1)
        self.assertFalse(report["fully_native"])
        audit = report["raster_crops"][0]
        self.assertTrue(audit["source_bytes_preserved"])
        self.assertEqual(audit["original_sha256"], self.object["sha256"])
        for key, value in self.crop.items():
            self.assertAlmostEqual(audit["crop"][key], value, delta=.0000051)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("ppt/media/original.png"), self.original)
            root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            native = root.find(".//p:pic/p:nvPicPr/p:cNvPr", NS)
            self.assertEqual(native.get("name"), "original-photo")
            self.assertEqual(native.get("descr"), "source_id=original-photo")
            rect = root.find(".//p:pic/p:blipFill/a:srcRect", NS)
            self.assertEqual(rect.get("l"), str(round(self.crop["left"]*100000)))
        self.assertEqual(json.loads(self.receipt.read_text())["raster_crops"], report["raster_crops"])

    def test_discarded_manual_crop_is_rejected(self):
        one_slide_package(self.source, self.original, crop=None)
        self.assert_rejected_without_publication("changed or discarded source crop")

    def test_changed_manual_crop_is_rejected(self):
        wrong = dict(self.crop, left=self.crop["left"]+.01)
        one_slide_package(self.source, self.original, wrong)
        self.assert_rejected_without_publication("changed or discarded source crop")

    def test_changed_embedded_raster_bytes_are_rejected(self):
        one_slide_package(self.source, png("orange"), self.crop)
        self.assert_rejected_without_publication("changed original raster bytes")

    def test_uncropped_original_image_passes_without_src_rect(self):
        self.object.pop("crop")
        one_slide_package(self.source, self.original, crop=None)
        report = self.run_postprocess()
        self.assertEqual(report["raster_crops"][0]["crop"],
                         {"left": 0, "top": 0, "right": 0, "bottom": 0})
        self.assertTrue(report["raster_crops"][0]["source_bytes_preserved"])

    def edit_native_slide(self, callback):
        with zipfile.ZipFile(self.source) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(files['ppt/slides/slide1.xml'])
        callback(root)
        files['ppt/slides/slide1.xml'] = xml(root)
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, content in files.items(): archive.writestr(name, content)

    def write_map(self, placement, box=None):
        box = box or self.object['box']
        self.object_map_path.write_text(json.dumps({'placement': placement, 'objects': [
            {'id': self.object['id'], 'kind': 'image', 'box': box}]}), encoding='utf-8')
        scale = placement[2] / self.canvas['width']
        return tuple(round(value * 9525) for value in
                     (placement[0]+box['x']*scale, placement[1]+box['y']*scale,
                      box['width']*scale, box['height']*scale))

    def test_crop_rounding_is_half_up_and_one_native_unit_change_fails(self):
        self.crop = {'left': .123445, 'top': .1, 'right': .2, 'bottom': .1}
        self.object['crop'] = self.crop
        one_slide_package(self.source, self.original, self.crop)
        report = self.run_postprocess()
        self.assertEqual(report['raster_crops'][0]['crop_units']['left'], 12345)
        self.output.unlink(); self.receipt.unlink()
        self.edit_native_slide(lambda root: root.find('.//a:srcRect', NS).set('l', '12346'))
        self.assert_rejected_without_publication('changed or discarded source crop')

    def test_positive_float_crop_collapsing_after_quantization_fails(self):
        self.object['crop'] = {'left': .499996, 'top': 0, 'right': .499996, 'bottom': 0}
        # The native rectangle still has area, but the requested crop rounds to
        # 50000+50000 and must be rejected instead of accepting a zero-width view.
        one_slide_package(self.source, self.original, {'left': .49999, 'top': 0, 'right': .49999, 'bottom': 0})
        self.assert_rejected_without_publication('quantization leaves no remaining')

    def test_zero_remaining_native_crop_is_rejected(self):
        one_slide_package(self.source, self.original, self.crop)
        def collapse(root):
            rect = root.find('.//a:srcRect', NS)
            rect.set('l', '50000'); rect.set('r', '50000')
        self.edit_native_slide(collapse)
        self.assert_rejected_without_publication('no valid remaining area')

    def test_fitted_image_frame_is_audited_with_translated_uniform_placement(self):
        placement = [35, 60, 721, 211]
        # A contain result narrower than the requested object box must be
        # checked against mapped fitted geometry, not against requested_box.
        fitted = {'x': 128.5, 'y': 50, 'width': 94, 'height': 279}
        frame = self.write_map(placement, fitted)
        one_slide_package(self.source, self.original, self.crop, frame)
        report = self.run_postprocess(object_map=self.object_map_path)
        audit = report['raster_crops'][0]['frame_audit']
        self.assertTrue(audit['verified'])
        self.assertEqual(audit['actual_emu'], list(frame))
        self.assertEqual(audit['tolerance_emu'], 2)
        self.assertAlmostEqual(audit['expected_emu'][2], 94*.5*9525)

    def test_changed_fitted_frame_is_rejected_without_publication(self):
        frame = self.write_map([0, 0, 1442, 422])
        one_slide_package(self.source, self.original, self.crop,
                          (frame[0]+3, frame[1], frame[2], frame[3]))
        with self.assertRaisesRegex(ValueError, 'changed fitted image frame'):
            self.run_postprocess(object_map=self.object_map_path)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_quantized_frame_two_emu_tolerance_and_orientation_guard(self):
        frame = self.write_map([0, 0, 1442, 422])
        one_slide_package(self.source, self.original, self.crop,
                          (frame[0]+2, frame[1]-2, frame[2]+2, frame[3]-2))
        self.run_postprocess(object_map=self.object_map_path)
        self.output.unlink(); self.receipt.unlink()
        self.edit_native_slide(lambda root: root.find('.//p:pic/p:spPr/a:xfrm', NS).set('flipH', 'true'))
        with self.assertRaisesRegex(ValueError, 'changed image orientation'):
            self.run_postprocess(object_map=self.object_map_path)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_invalid_object_map_and_missing_image_entry_are_rejected(self):
        one_slide_package(self.source, self.original, self.crop)
        self.write_map([0, 0, 1442, 421])
        with self.assertRaisesRegex(ValueError, 'not uniformly scaled'):
            self.run_postprocess(object_map=self.object_map_path)
        self.object_map_path.write_text(json.dumps({'placement': [0, 0, 1442, 422], 'objects': []}))
        with self.assertRaisesRegex(ValueError, 'Missing mapped image frame'):
            self.run_postprocess(object_map=self.object_map_path)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_group_union_encloses_rotated_text_and_stroke_without_moving_children(self):
        self.object['group_id'] = 'figure-panel'
        one_slide_package(self.source, self.original, self.crop)
        transforms = {}
        def append(root):
            tree = root.find('p:cSld/p:spTree', NS)
            for identity, name, kind, box, rotation, stroke in [
                (3, 'rotated-label', 'text', (1000000, 2000000, 4000000, 400000), 90, 0),
                (4, 'stroked-region', 'path', (4000000, 3000000, 1000000, 1000000), 0, 100000)]:
                shape = ET.SubElement(tree, f'{{{P}}}sp')
                props = ET.SubElement(shape, f'{{{P}}}nvSpPr')
                ET.SubElement(props, f'{{{P}}}cNvPr', id=str(identity), name=name)
                ET.SubElement(props, f'{{{P}}}cNvSpPr'); ET.SubElement(props, f'{{{P}}}nvPr')
                sppr = ET.SubElement(shape, f'{{{P}}}spPr')
                xf = ET.SubElement(sppr, f'{{{A}}}xfrm', rot=str(rotation*60000))
                ET.SubElement(xf, f'{{{A}}}off', x=str(box[0]), y=str(box[1]))
                ET.SubElement(xf, f'{{{A}}}ext', cx=str(box[2]), cy=str(box[3]))
                line = ET.SubElement(sppr, f'{{{A}}}ln', w=str(stroke))
                if stroke:
                    ET.SubElement(ET.SubElement(line, f'{{{A}}}solidFill'), f'{{{A}}}srgbClr', val='000000')
                else: ET.SubElement(line, f'{{{A}}}noFill')
                if kind == 'text':
                    ET.SubElement(shape, f'{{{P}}}txBody')
                else: ET.SubElement(sppr, f'{{{A}}}custGeom')
                transforms[name] = xml(xf)
            transforms['original-photo'] = xml(root.find('.//p:pic/p:spPr/a:xfrm', NS))
        self.edit_native_slide(append)
        objects = [self.object, {'id': 'rotated-label', 'kind': 'text', 'group_id': 'figure-panel'},
                   {'id': 'stroked-region', 'kind': 'path', 'group_id': 'figure-panel'}]
        report = self.run_postprocess(objects=objects)
        group_audit = report['native_groups'][0]
        self.assertTrue(group_audit['identity_transform'])
        left, top, right, bottom = group_audit['visual_bounds_emu']
        # Rotation turns the label's 4M-wide editing frame into a 4M-high
        # visible footprint (y=.2M..4.2M); the line extends beyond x=5M.
        self.assertLessEqual(left, 100000); self.assertLessEqual(top, 100000)
        self.assertGreaterEqual(right, 5050000); self.assertGreaterEqual(bottom, 4200000)
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
            group = root.find('.//p:grpSp', NS)
            group_xf = group.find('p:grpSpPr/a:xfrm', NS)
            self.assertEqual(group_xf.find('a:off', NS).attrib, group_xf.find('a:chOff', NS).attrib)
            self.assertEqual(group_xf.find('a:ext', NS).attrib, group_xf.find('a:chExt', NS).attrib)
            for child in list(group)[2:]:
                name = child.find('.//p:cNvPr', NS).get('name')
                self.assertEqual(xml(child.find('p:spPr/a:xfrm', NS)), transforms[name])


if __name__ == "__main__":
    unittest.main()
