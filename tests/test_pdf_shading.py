import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import pymupdf as fitz
except ImportError:
    fitz = None

from figure_rebuild.pdf_shading import extract_constant_axial_shading, UnsupportedPdfShadingError


def _obj(doc, text, data=None):
    xref = doc.get_new_xref()
    doc.update_object(xref, text)
    if data is not None:
        doc.update_stream(xref, data)
    return xref


def fixture(path, *, samples=bytes([0,0,0,0])*4, decode='[0 1 0 1 0 1 0 1]',
            colorspace='/DeviceCMYK', order=1, extend='[true true]', coords='[0 0 100 0]',
            clip='20 20 80 60 re W n', contents=None, alpha=1, shade_extra='', function_extra='',
            group=False, duplicate=False, mask=False, source_domain='[0 1]', function_domain='[0 1]'):
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    components = 4 if colorspace == '/DeviceCMYK' else 3 if colorspace == '/DeviceRGB' else 1
    ranges = '['+' '.join(['0 1']*components)+']'
    function = _obj(doc, f'<< /FunctionType 0 /Domain {function_domain} /Range {ranges} /Size [4] /BitsPerSample 8 /Order {order} /Decode {decode} {function_extra} >>', samples)
    shade_text = f'<< /ShadingType 2 /ColorSpace {colorspace} /Coords {coords} /Domain {source_domain} /Extend {extend} /Function {function} 0 R {shade_extra} >>'
    shade = _obj(doc, shade_text)
    other = _obj(doc, shade_text) if duplicate else None
    mask_entry = ''
    if mask:
        form = _obj(doc, '<< /Type /XObject /Subtype /Form /BBox [0 0 200 120] /Resources << >> /Group << /S /Transparency /CS /DeviceRGB /I true >> >>', b'0 0 0 rg 0 0 200 120 re f')
        mask_entry = f'/SMask << /S /Alpha /G {form} 0 R >>'
    gs = _obj(doc, f'<< /Type /ExtGState /ca {alpha} {mask_entry} >>')
    resources = f'<< /Shading << /Sh0 {shade} 0 R'+(f' /Sh1 {other} 0 R' if duplicate else '')+f' >> /ExtGState << /Gs {gs} 0 R >> >>'
    resource = _obj(doc, resources)
    doc.xref_set_key(page.xref, 'Resources', f'{resource} 0 R')
    if group:
        doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB >>')
    content = contents or f'q /Gs gs {clip} /Sh0 sh Q'
    page.set_contents(_obj(doc, '<< >>', content.encode()))
    doc.save(path)
    doc.close()
    return shade, other, function


@unittest.skipUnless(fitz, 'source extra requires PyMuPDF')
class PdfShadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name)/'source.pdf'

    def extract(self, shade, **kwargs):
        options = dict(page=1, paint_seqno=0, shading_xref=shade, region=[0,0,200,120],
                       source_transform=[[1,0,0],[0,1,0]])
        options.update(kwargs)
        return extract_constant_axial_shading(self.pdf, **options)

    def test_native_constant_cmyk_preserves_white_and_identity(self):
        shade, _, function = fixture(self.pdf)
        before = self.pdf.read_bytes()
        result = self.extract(shade)
        self.assertEqual(result['object']['style']['fill'], '#FFFFFF')
        self.assertEqual(result['object']['kind'], 'path')
        proof = result['provenance']
        self.assertEqual(proof['function']['function_xref'], function)
        self.assertEqual(proof['function']['constant_components'], [0,0,0,0])
        self.assertTrue(proof['actual_native_resource_pointer_verified'])
        self.assertEqual(proof['source_pdf_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.pdf.read_bytes(), before)
        self.assertEqual(proof['native_clips'][0]['kind'], 'clip_path')

    def test_decode_and_nonwhite_rgb_use_original_constant_function(self):
        shade, _, _ = fixture(self.pdf, colorspace='/DeviceRGB', samples=bytes([255,0,0])*4,
                              decode='[1 0 0 1 1 1]')
        result = self.extract(shade)
        self.assertEqual(result['object']['style']['fill'], '#0000FF')
        self.assertEqual(result['provenance']['function']['decode'], [1,0,0,1,1,1])

    def test_native_complex_curve_preserved_and_implicit_subpaths_closed(self):
        clip = '0 0 200 120 re W n 20 20 m 20 70 80 70 100 20 c 30 30 m 40 50 l 60 30 l W n'
        shade, _, _ = fixture(self.pdf, clip=clip)
        result = self.extract(shade, source_transform=[[-2,0,400],[0,3,0]])
        commands = result['object']['commands']
        self.assertEqual(sum('close' in c for c in commands), 2)
        self.assertEqual(sum('cubicTo' in c for c in commands), 1)
        self.assertEqual(commands[1]['cubicTo'], {'x1':360,'y1':150,'x2':240,'y2':150,'x':200,'y':300})
        self.assertFalse(result['provenance']['geometry_proof']['curves_flattened'])

    def test_finite_extension_requires_entire_control_hull(self):
        shade, _, _ = fixture(self.pdf, extend='[false false]', clip='20 20 60 60 re W n')
        self.extract(shade)
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'axial domain'):
            shade, _, _ = fixture(self.pdf, extend='[false false]', coords='[30 0 70 0]')
            self.extract(shade)

    def test_varying_samples_order_and_domain_rejected(self):
        cases = [dict(samples=bytes([0,0,0,0])*3+bytes([1,0,0,0])), dict(order=3),
                 dict(function_domain='[0 2]'), dict(source_domain='[1 0]'), dict(coords='[1 1 1 1]')]
        for case in cases:
            with self.subTest(case=case):
                shade, _, _ = fixture(self.pdf, **case)
                with self.assertRaises(UnsupportedPdfShadingError): self.extract(shade)

    def test_group_alpha_bbox_background_and_evenodd_rejected(self):
        cases = [dict(group=True), dict(alpha=.5), dict(shade_extra='/Background [0 0 0 0]'),
                 dict(shade_extra='/BBox [0 0 100 100]'), dict(shade_extra='/AntiAlias true'),
                 dict(clip='20 20 m 20 70 80 70 100 20 c W* n')]
        for case in cases:
            with self.subTest(case=case):
                shade, _, _ = fixture(self.pdf, **case)
                with self.assertRaises(UnsupportedPdfShadingError): self.extract(shade)

    def test_two_close_resources_not_interchangeable_and_repeated_occurrences(self):
        shade, other, _ = fixture(self.pdf, duplicate=True,
                                 contents='q 20 20 20 20 re W n /Sh0 sh Q q 70 20 20 20 re W n /Sh0 sh Q')
        first = self.extract(shade)
        second = self.extract(shade, paint_seqno=1)
        self.assertNotEqual(first['object']['commands'], second['object']['commands'])
        self.assertEqual(second['provenance']['verified_source_paint_count'], 2)
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'pointer'):
            self.extract(other)

    def test_roi_and_control_hull_not_approximated(self):
        shade, _, _ = fixture(self.pdf, clip='20 20 m 20 70 80 70 100 20 c W n')
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'control hull'):
            self.extract(shade, region=[30,0,200,120])
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'within the PDF page'):
            self.extract(shade, region=[-1,0,200,120])
        with self.assertRaises(UnsupportedPdfShadingError):
            self.extract(shade, source_transform=[[float('inf'),0,0],[0,1,0]])

    def test_native_capability_callback_failure_and_budget_fail_closed(self):
        shade, _, _ = fixture(self.pdf)
        with patch.object(fitz.mupdf, 'fz_walk_path', side_effect=RuntimeError('injected native callback failure')):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'injected native callback failure'):
                self.extract(shade)
        with patch('figure_rebuild.pdf_shading._MAX_COMMANDS', 2):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'budget'):
                self.extract(shade)
        with patch('figure_rebuild.pdf_shading._MAX_CONTEXT_EVENTS', 1):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'budget'): self.extract(shade)
        with patch('figure_rebuild.pdf_shading._MAX_DEPTH', 0):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'budget'): self.extract(shade)
        with patch.object(fitz.mupdf, 'fz_default_rgb', return_value=fitz.mupdf.fz_device_gray()):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'default-colorspace'): self.extract(shade)
        with patch.object(fitz.mupdf, 'fz_convert_color', return_value=(float('nan'),0,0,0)):
            with self.assertRaises(UnsupportedPdfShadingError): self.extract(shade)

    def test_quantized_nonrepresentable_color_rejected(self):
        shade, _, _ = fixture(self.pdf, colorspace='/DeviceRGB', samples=bytes([0,0,0])*4,
                              decode='[0.123 0.123 0 1 0 1]')
        with self.assertRaisesRegex(UnsupportedPdfShadingError, '8-bit RGB'):
            self.extract(shade)


    def test_real_soft_mask_and_nonconstant_function_types_rejected(self):
        shade, _, _ = fixture(self.pdf, mask=True)
        with self.assertRaises(UnsupportedPdfShadingError):
            self.extract(shade, paint_seqno=1)
        shade, _, function = fixture(self.pdf)
        with fitz.open(self.pdf) as doc:
            doc.xref_set_key(function, 'FunctionType', '2')
            doc.saveIncr()
        with self.assertRaises(UnsupportedPdfShadingError): self.extract(shade)

    def test_shade_to_path_real_pdf_render_matches_curved_clip(self):
        shade, _, _ = fixture(self.pdf, colorspace='/DeviceRGB', samples=bytes([0,0,255])*4,
                              decode='[0 1 0 1 0 1]',
                              clip='20 20 m 20 90 100 90 120 20 c h W n')
        result = self.extract(shade)
        with fitz.open(self.pdf) as doc:
            expected = doc[0].get_pixmap(matrix=fitz.Matrix(4,4), alpha=True)
            expected_samples = expected.samples
        commands = ['0 0 1 rg']
        for cmd in result['object']['commands']:
            if 'moveTo' in cmd:
                p=cmd['moveTo'];commands.append(f"{p['x']} {120-p['y']} m")
            elif 'lineTo' in cmd:
                p=cmd['lineTo'];commands.append(f"{p['x']} {120-p['y']} l")
            elif 'cubicTo' in cmd:
                p=cmd['cubicTo'];commands.append(f"{p['x1']} {120-p['y1']} {p['x2']} {120-p['y2']} {p['x']} {120-p['y']} c")
            else: commands.append('h')
        commands.append('f')
        with fitz.open() as doc:
            page=doc.new_page(width=200,height=120)
            page.set_contents(_obj(doc,'<< >>',' '.join(commands).encode()))
            actual=page.get_pixmap(matrix=fitz.Matrix(4,4),alpha=True)
            actual_samples = actual.samples
        self.assertEqual(len(expected_samples), len(actual_samples))
        differences = [abs(a-b) for a, b in zip(expected_samples, actual_samples)]
        self.assertLessEqual(max(differences), 1)
        self.assertLess(sum(differences)/len(differences), .01)

    def test_midpath_close_not_misidentified_as_rectangle(self):
        shade, _, _ = fixture(self.pdf, clip='20 20 m 100 20 l h 100 80 l 20 80 l h W n')
        with self.assertRaises(UnsupportedPdfShadingError): self.extract(shade)


    def test_missing_optional_runtime_and_native_api_fail_closed(self):
        import sys
        shade, _, _ = fixture(self.pdf)
        with patch.dict(sys.modules, {'pymupdf': None}):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'requires PyMuPDF'):
                self.extract(shade)
        with patch.object(fitz.mupdf, 'fz_convert_color', None):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'capabilities'):
                self.extract(shade)

    def test_declared_stream_size_is_a_decode_budget(self):
        shade, _, _ = fixture(self.pdf, samples=bytes([0])*4000)
        with self.assertRaises(UnsupportedPdfShadingError):
            self.extract(shade)


    def test_original_native_fractional_boundary_is_not_rounded_into_domain(self):
        from decimal import Decimal
        token = format(Decimal(str(2**-50)), 'f')
        cases = [
            f'q 1 0 0 1 100 0 cm -{token} 20 20 60 re W n /Sh0 sh Q',
            f'q 1 0 0 1 100 0 cm -20 20 m {token} 20 l {token} 80 l -20 80 l h W n 1 0 0 1 -100 0 cm /Sh0 sh Q',
        ]
        for content in cases:
            with self.subTest(content=content):
                shade, _, _ = fixture(self.pdf, extend='[false false]', contents=content)
                with self.assertRaisesRegex(UnsupportedPdfShadingError, 'axial domain'):
                    self.extract(shade)

    def test_original_complex_clip_hull_not_rounded_into_roi(self):
        from decimal import Decimal
        token = format(Decimal(str(2**-50)), 'f')
        content = f'q 1 0 0 1 100 0 cm -{token} 20 m -{token} 80 20 80 20 20 c h W n /Sh0 sh Q'
        shade, _, _ = fixture(self.pdf, contents=content)
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'control hull'):
            self.extract(shade, region=[100,0,200,120])

    def test_original_tiny_shear_not_misclassified_as_axis_rectangle(self):
        from decimal import Decimal
        token = format(Decimal(str(2**-50)), 'f')
        content = f'q 1 0 {token} 1 100 0 cm 0 20 20 60 re W n /Sh0 sh Q'
        shade, _, _ = fixture(self.pdf, contents=content)
        result = self.extract(shade)
        proof = result['provenance']
        self.assertEqual(proof['geometry_proof']['complex_clip_count'], 1)
        self.assertIn('exact_rationals', proof['geometry_proof']['predicate_arithmetic'])
        self.assertIn('axis_parameter_control_hull_exact', proof)
        self.assertIn('maximum_coordinate_roundoff_target_units_exact', proof['output_coordinate_conversion'])

    def test_shade_bbox_callback_failure_explicitly_aborts(self):
        shade, _, _ = fixture(self.pdf, contents='q 20 20 80 60 re W n /Sh0 sh Q 0 0 0 rg 0 0 10 10 re f')
        original = fitz.jm_bbox_fill_shade
        original_abort = fitz.mupdf.FzCookie.set_abort
        events = []
        def fail(device, *args):
            if hasattr(device, 'selected'):
                raise RuntimeError('SHADE_BBOX_SENTINEL')
            return original(device, *args)
        def abort(cookie, *args):
            events.append('abort')
            return original_abort(cookie, *args)
        with patch.object(fitz, 'jm_bbox_fill_shade', fail), patch.object(fitz.mupdf.FzCookie, 'set_abort', abort):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'Native fill_shade bbox callback failed: SHADE_BBOX_SENTINEL'):
                self.extract(shade)
        self.assertIn('abort', events)


if __name__ == '__main__':
    unittest.main()
