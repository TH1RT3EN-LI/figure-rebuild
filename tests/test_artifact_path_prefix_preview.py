import base64
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from PIL import Image
from figure_rebuild import artifact_path_prefix_preview as prefix
if __package__:
    from . import test_native_svg_preview as native_fixture
else:
    import test_native_svg_preview as native_fixture


class PathPrefixTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.ppt = Path(temp.name)/'native.pptx'
        self.files, self.manifest = native_fixture.fixture()
        self.request = dict(schema_version=1, object_ids=['ring'], frame_emu=[0,0,952500,762000], pixel_grid=[100,80])
        self.write()

    def write(self, files=None):
        with ZipFile(self.ppt, 'w') as z:
            for name, data in (files or self.files).items(): z.writestr(name, data)

    def prepare(self):
        return prefix.prepare_path_prefix_preview(self.ppt, self.manifest, self.request)

    @unittest.skipUnless(native_fixture.pymupdf, 'optional PyMuPDF source dependency')
    def test_actual_native_prefix_hole_is_opaque_background_and_all_target_grids_replay(self):
        before = self.ppt.read_bytes(); d = self.prepare()
        self.assertEqual(d, self.prepare()); self.assertEqual(before, self.ppt.read_bytes())
        im = Image.open(io.BytesIO(base64.b64decode(d['opaque_grid_png_base64']))).convert('RGBA')
        self.assertEqual(im.getextrema()[3], (255,255))
        self.assertEqual(im.getpixel((15,15)), (255,0,0,255))
        self.assertEqual(im.getpixel((35,35)), (255,255,255,255))
        self.assertEqual([v['id'] for v in d['objects']], ['ring'])
        self.assertEqual([v['id'] for v in d['paint_order']], ['ring','curve'])
        for p in d['previews']:
            im = Image.open(io.BytesIO(base64.b64decode(p['png_base64']))).convert('RGBA')
            self.assertEqual(im.size, (100*p['scale'],80*p['scale']))
            self.assertEqual(im.getpixel((15*p['scale'],15*p['scale'])), (255,0,0,255))
        self.assertFalse(d['reference_pixels_used']); self.assertFalse(d['native_delivery_modified'])

    @unittest.skipUnless(native_fixture.pymupdf, 'optional PyMuPDF source dependency')
    def test_actual_native_color_coordinate_and_alpha_mutations_change_replayed_native_definition(self):
        original = self.prepare()
        for before, after in [('FF0000','00FF00'), ('x="60"','x="63"'),
                ('val="FF0000"/>','val="FF0000"><a:alpha val="50000"/></a:srgbClr>')]:
            files = copy.deepcopy(self.files); files['ppt/slides/slide1.xml'] = files['ppt/slides/slide1.xml'].replace(before, after)
            self.write(files); changed = self.prepare()
            self.assertNotEqual(original['native_slide_xml_sha256'], changed['native_slide_xml_sha256'])
            self.assertNotEqual(original['opaque_grid_png_sha256'], changed['opaque_grid_png_sha256'])

    def test_prefix_cannot_skip_first_paint_or_include_live_text_picture_or_stroked_path(self):
        for ids in [['curve'], ['ring','curve']]:
            self.request['object_ids'] = ids
            with self.assertRaisesRegex(ValueError, 'first paint|filled paths'): self.prepare()
        self.request['object_ids'] = ['ring']; self.manifest['objects'][0]['kind'] = 'text'
        with self.assertRaisesRegex(ValueError, 'without text or pictures'): self.prepare()
        self.manifest['objects'][0]['kind'] = 'path'; self.manifest['objects'].reverse()
        with self.assertRaisesRegex(ValueError, 'paint order'): self.prepare()

    def test_actual_control_hull_background_canvas_and_active_relationship_are_bound(self):
        for file, before, after, message in [
            ('ppt/slides/slide1.xml','x="60"','x="101"','control hull'),
            ('ppt/slides/slide1.xml','val="FFFFFF"','val="EEEEEE"','opaque RGB'),
            ('ppt/presentation.xml','cx="952500"','cx="952501"','canvas differs'),
            ('ppt/_rels/presentation.xml.rels','slides/slide1.xml','slides/slide2.xml','relationship')]:
            files = copy.deepcopy(self.files); files[file] = files[file].replace(before, after); self.write(files)
            with self.assertRaisesRegex(ValueError, message): self.prepare()
        self.write(); self.manifest['source_canvas_clip'] = {}
        with self.assertRaisesRegex(ValueError, 'source clipping'): self.prepare()

    def test_requests_budgets_and_types_fail_before_native_package_or_renderer_is_opened(self):
        for field, value in [('schema_version', True), ('object_ids',['ring','ring']), ('frame_emu',[0,0,952500,True]),
                             ('pixel_grid',[32768,32768]), ('pixel_grid',[1,False]), ('extra',True)]:
            request = copy.deepcopy(self.request); request[field] = value
            with patch.object(prefix, '_native_picture_inputs', side_effect=AssertionError('Native open must not run')):
                with self.assertRaises(ValueError): prefix.prepare_path_prefix_preview(self.ppt, self.manifest, request)

    def test_native_effects_formula_rotation_hidden_and_external_paints_refuse_before_sampling(self):
        for before, after in [('<a:xfrm>', '<a:xfrm rot="60000">'), ('<a:custGeom>','<a:custGeom><a:gdLst/>'),
                             ('name="ring"','name="ring" hidden="1"'), ('<a:solidFill>','<a:effectLst/><a:solidFill>'),
                             ('<a:srgbClr val="FF0000"/>','<a:schemeClr val="accent1"/>')]:
            files = copy.deepcopy(self.files); files['ppt/slides/slide1.xml'] = files['ppt/slides/slide1.xml'].replace(before, after)
            self.write(files)
            with self.assertRaises(ValueError): self.prepare()


if __name__ == '__main__': unittest.main()
