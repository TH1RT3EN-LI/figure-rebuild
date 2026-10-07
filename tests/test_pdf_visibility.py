"""Source exclusion must preserve every real clip and occurrence identity."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths
from figure_rebuild.pdf_visibility import exact_chain, native_hull_certificate, prove_native_clip_disjoint, prove_source_paint_invisible
from figure_rebuild import pdf_images
from figure_rebuild.pdf_paint_context import inspect_pdf_paint_context
try:
    import pymupdf
except ImportError:
    pymupdf = None

def source_paint(path='M0 20L20 20', clips=True, **attributes):
    style = {'fill': 'none', 'stroke': '#000000', 'stroke-width': '2', 'stroke-linecap': 'butt', 'stroke-dasharray': '2 1', **attributes}
    attrs = ' '.join((f'{key}="{value}"' for key, value in style.items()))
    context = 'clip-path="url(#c)"' if clips else ''
    document = extract_outlined_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><clipPath id="c"><path d="M0 0H10V10H0Z"/></clipPath></defs><path d="{path}" {attrs} {context}/></svg>')
    return (document, document.paints[0])

class SourceVisibilityTests(unittest.TestCase):

    def test_single_butt_dash_outside_clip_skips_without_rewriting_source(self):
        doc, paint = source_paint()
        original = deepcopy(paint)
        cert = prove_source_paint_invisible(paint, None)
        self.assertEqual(cert['method'], 'single_nonzero_straight_butt_dash_no_join_full_segment_support_disjoint')
        result = outline_paths(doc, glyph_mode='outline')
        self.assertEqual(result.objects, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]['source_id'], paint.source_id)
        self.assertEqual(paint, original)

    def test_visible_touching_polyline_square_and_invalid_dash_not_certified(self):
        for args in [dict(path='M0 9L20 9'), dict(path='M0 11L20 11'), dict(path='M0 20L10 20L20 20'), {'stroke-linecap': 'square'}, {'stroke-dasharray': '2 -1'}, {'stroke-dasharray': 'garbage'}, {'stroke-width': '-1'}, {'fill-rule': 'invalid'}, {'mask': 'url(#unknown)'}, {'filter': 'url(#unknown)'}, {'opacity': '1', 'vector-effect': 'non-scaling-stroke'}]:
            with self.subTest(args=args):
                _, paint = source_paint(**args)
                self.assertIsNone(prove_source_paint_invisible(paint, None))

    def test_strictly_disjoint_active_rectangles_skip_irrelevant_affine_stroke(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><clipPath id="a"><path d="M0 0H10V10H0Z"/></clipPath><clipPath id="b"><path d="M30 30H40V40H30Z"/></clipPath></defs><g clip-path="url(#a)"><g clip-path="url(#b)"><path d="M0 0L30 30" transform="matrix(1,0.2,0,1,0,0)" fill="none" stroke="#ff0000"/></g></g></svg>'
        doc = extract_outlined_svg(svg)
        before = deepcopy(doc.paints[0])
        result = outline_paths(doc, glyph_mode='outline')
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]['visibility_certificate']['method'], 'strictly_empty_conservative_active_clip_intersection')
        self.assertEqual(doc.paints[0], before)

    def test_unknown_clip_and_group_effect_not_hidden_by_outside_roi(self):
        doc, paint = source_paint()
        clip = dict(paint.clips[0])
        clip['unsupported_resource_ancestors'] = [{'filter': 'url(#f)'}]
        from dataclasses import replace
        self.assertIsNone(prove_source_paint_invisible(replace(paint, clips=(clip,)), None))
        group = {'opacity': '0.5'}
        self.assertIsNone(prove_source_paint_invisible(replace(paint, groups=(group,)), None))

    def test_exact_chain_rejects_extreme_exponent_without_losing_float_geometry(self):
        from figure_rebuild.pdf_visibility import ID
        self.assertIsNone(exact_chain(ID, 'matrix(1,0,0,1,1e-999999999,0)'))
        self.assertEqual(exact_chain(ID, 'matrix(1,0,0,1,0.1,0.2)')[-2:], (Fraction(1, 10), Fraction(1, 5)))
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1 1L2 1L2 2Z" transform="matrix(1,0,0,1,1e-999999999,0)"/></svg>'
        doc = extract_outlined_svg(svg)
        self.assertEqual(doc.paints[0].exact_transform, ())
        self.assertEqual(len(outline_paths(doc, glyph_mode='outline').objects), 1)

    def test_hull_rejects_boolean_non_float32_and_degenerate_inputs(self):
        for local, matrix in [([False, 0, 1, 1], [1, 0, 0, 1, 0, 0]), ([0, 0, 1, 1], [1, 0, 0, 1, 0.1, 0]), ([0, 0, 0, 1], [1, 0, 0, 1, 0, 0]), ([0, 0, 1, 1], [1, 0, 0, 0, 0, 0])]:
            with self.assertRaises(ValueError):
                native_hull_certificate(local, matrix)

@unittest.skipIf(pymupdf is None, 'optional source dependency unavailable')
class NativeVisibilityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'clip.pdf'

    def context(self, clip):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=200)
            stream = doc.get_new_xref()
            doc.update_object(stream, '<<>>')
            doc.update_stream(stream, b'q ' + clip + b' W n 0 0 200 200 re f Q')
            page.set_contents(stream)
            doc.save(self.path)
        return inspect_pdf_paint_context(self.path)

    def test_real_triangle_hull_only_proves_outside_not_inside_its_box(self):
        c = self.context(b'10 150 m 20 150 l 15 180 l h')
        self.assertIsNotNone(prove_native_clip_disjoint(c, c['paints'][0], [100, 100, 120, 120]))
        self.assertIsNone(prove_native_clip_disjoint(c, c['paints'][0], [10, 20, 20, 50]))
        self.assertEqual(c['clips'][0]['conservative_extent']['native_path_rectangle_predicate_used'], False)

    def test_near_rectangle_real_corner_is_not_erased(self):
        c = self.context(b'10 150 m 20 150 l 20.000001 180 l 10 180 l h')
        cert = c['clips'][0]['conservative_extent']
        self.assertGreater(float(Fraction(cert['bounds_exact'][2])), 20)
        self.assertIsNone(prove_native_clip_disjoint(c, c['paints'][0], [20.0000001, 20, 20.0000005, 21]))

    def test_touching_roi_and_untrusted_contexts_get_no_certificate(self):
        original = self.context(b'10 150 20 30 re')
        self.assertIsNone(prove_native_clip_disjoint(original, original['paints'][0], [30, 20, 40, 50]))
        for mutation in ('mask', 'pattern', 'span', 'extent', 'nested', 'bad_clip_id', 'bad_seq', 'bad_region'):
            c = deepcopy(original)
            p = c['paints'][0]
            if mutation == 'mask':
                p['active_mask_ids'] = [1]
            elif mutation == 'pattern':
                p['pattern_depth'] = 1
            elif mutation == 'span':
                c['clips'][0]['begin_paint_seqno'] = 1
            elif mutation == 'extent':
                c['clips'][0]['conservative_extent']['bounds_exact'][1] = '1000'
            elif mutation == 'nested':
                p['group_ids'] = [1, 2]
            elif mutation == 'bad_clip_id':
                p['clip_ids'] = [False]
            elif mutation == 'bad_seq':
                p['source_seqno'] = -1
            raw = {k: v for k, v in c.items() if k != 'context_sha256'}
            c['context_sha256'] = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            region = [0, 0, 100, 100] if mutation != 'bad_region' else [0, 0, -1, 100]
            with self.subTest(mutation=mutation):
                self.assertIsNone(prove_native_clip_disjoint(c, p, region))

    def test_unverified_provider_leaves_native_roles_and_full_identity(self):
        self.context(b'10 150 20 30 re')
        with patch.object(pymupdf, 'VersionFitz', 'unverified'):
            c = inspect_pdf_paint_context(self.path)
        self.assertTrue(c['identity_complete'])
        self.assertNotIn('conservative_extent', c['clips'][0])
        self.assertIsNone(prove_native_clip_disjoint(c, c['paints'][0], [0, 0, 100, 100]))

    def test_auxiliary_text_number_collision_cannot_veto_correct_native_image(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=200)
            data = io.BytesIO()
            Image.new('RGB', (10, 20), 'red').save(data, format='PNG')
            page.insert_image(pymupdf.Rect(10, 10, 30, 50), stream=data.getvalue(), keep_proportion=False)
            doc.save(self.path)
        real = pymupdf.Page.get_text

        def unrelated(page, *args, **kwargs):
            result = real(page, *args, **kwargs)
            if args and args[0] == 'dict':
                for block in result['blocks']:
                    if block['type'] == 1:
                        block['transform'] = (1, 0, 0, 1, 100, 100)
                        block['width'] = 1000
            return result
        with patch.object(pymupdf.Page, 'get_text', unrelated):
            record, = pdf_images.extract_pdf_images(self.path)
        self.assertEqual(Image.open(io.BytesIO(record['asset_bytes'])).getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(record['provenance']['auxiliary_text_block']['status'], 'unrelated_text_namespace_number_collision')
        self.assertIsNone(record['provenance']['text_dict_bbox_pdf_pt'])
        self.assertTrue(record['provenance']['auxiliary_text_block']['candidate_is_unbound'])

    def test_wrong_primary_native_digest_still_rejects_after_auxiliary_fix(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=200)
            data = io.BytesIO()
            Image.new('RGB', (10, 20), 'red').save(data, format='PNG')
            page.insert_image(pymupdf.Rect(10, 10, 30, 50), stream=data.getvalue(), keep_proportion=False)
            doc.save(self.path)
        actual = pdf_images.capture_native_pdf_images

        def changed(*a, **k):
            result = actual(*a, **k)
            for row in result.values():
                row['native_digest'] = '0' * 32
            return result
        with patch.object(pdf_images, 'capture_native_pdf_images', changed):
            with self.assertRaisesRegex(pdf_images.UnsupportedPdfImageError, 'native image digest'):
                pdf_images.extract_pdf_images(self.path)
