import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import pymupdf as fitz
except ImportError:
    fitz = None

from figure_rebuild.pdf_shading import (extract_constant_axial_shading,
                                      extract_linear_axial_shading, UnsupportedPdfShadingError)


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


def linear_fixture(path, *, pattern=False, matrix='[1 0 0 1 0 0]',
                   function='<< /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >>',
                   coords='[0 0 100 0]', extend='[true true]', clip='20 20 80 60 re W n',
                   colorspace='/DeviceRGB', duplicate=False, contents=None, shade_extra='',
                   pattern_extra='', indirect_function=False):
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    if indirect_function:
        function = f'{_obj(doc, function)} 0 R'
    shade_text = f'<< /ShadingType 2 /ColorSpace {colorspace} /Coords {coords} /Extend {extend} /Function {function} {shade_extra} >>'
    shade = _obj(doc, shade_text)
    if pattern:
        text = f'<< /Type /Pattern /PatternType 2 /Matrix {matrix} /Shading {shade_text} {pattern_extra} >>'
        resource = _obj(doc, text)
        other = _obj(doc, text) if duplicate else None
        resources = f'<< /Pattern << /P0 {resource} 0 R'+(f' /P1 {other} 0 R' if duplicate else '')+' >> >>'
        paint = '/Pattern cs /P0 scn 0 0 200 120 re f'
    else:
        resource = shade
        other = _obj(doc, shade_text) if duplicate else None
        resources = f'<< /Shading << /Sh0 {shade} 0 R'+(f' /Sh1 {other} 0 R' if duplicate else '')+' >> >>'
        paint = '/Sh0 sh'
    resource_dict = _obj(doc, resources)
    doc.xref_set_key(page.xref, 'Resources', f'{resource_dict} 0 R')
    page.set_contents(_obj(doc, '<< >>', (contents or f'q {clip} {paint} Q').encode()))
    doc.save(path)
    doc.close()
    return resource, other, shade


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


@unittest.skipUnless(fitz, 'source extra requires PyMuPDF')
class PdfLinearShadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name)/'source.pdf'

    def extract(self, xref, **kwargs):
        options = dict(page=1, paint_seqno=0, resource_xref=xref, resource_kind='shading',
                       region=[0,0,200,120], source_transform=[1,0,0,1,0,0])
        options.update(kwargs)
        return extract_linear_axial_shading(self.pdf, **options)

    def test_direct_linear_function_recovers_phase_without_modifying_source(self):
        xref,_,_ = linear_fixture(self.pdf)
        before = self.pdf.read_bytes()
        result = self.extract(xref)
        gradient = result['object']['style']['fill_gradient']
        self.assertEqual(gradient, {'type':'linear', 'angle':0, 'stops':[
            {'offset':0, 'color':'#CC0033'}, {'offset':1, 'color':'#0000FF'}]})
        self.assertNotIn('fill', result['object']['style'])
        proof = result['provenance']
        self.assertEqual(proof['function']['function_type'], 2)
        self.assertIsNone(proof['function']['function_xref'])
        self.assertTrue(proof['actual_native_resource_pointer_verified'])
        self.assertEqual(proof['gradient_encoding_proof']['maximum_component_encoding_error_exact'], '0')
        self.assertEqual(self.pdf.read_bytes(), before)
        self.assertEqual(proof['source_pdf_sha256'], hashlib.sha256(before).hexdigest())

    def test_pattern_identity_includes_original_matrix_not_nested_dictionary(self):
        xref,other,shade = linear_fixture(self.pdf, pattern=True, duplicate=True,
                                         matrix='[0 2 -3 0 120 10]')
        result = self.extract(xref, resource_kind='pattern')
        self.assertEqual(result['provenance']['native_shade_matrix'], [0,2,-3,0,120,10])
        self.assertEqual(result['object']['style']['fill_gradient']['angle'], 270)
        self.assertEqual(result['provenance']['resource_xref'], xref)
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'pointer'):
            self.extract(other, resource_kind='pattern')
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'pointer'):
            self.extract(shade)

    def test_original_curve_clip_and_reflected_vertical_gradient_preserved(self):
        xref,_,_ = linear_fixture(self.pdf, coords='[0 0 0 100]',
                                  clip='20 20 m 20 90 100 90 120 20 c h W n')
        result = self.extract(xref, source_transform=[2,0,0,-3,0,360])
        self.assertEqual(result['object']['style']['fill_gradient']['angle'], 90)
        self.assertEqual(result['object']['commands'][1]['cubicTo'],
                         {'x1':40,'y1':270,'x2':200,'y2':270,'x':240,'y':60})
        self.assertFalse(result['provenance']['geometry_proof']['curves_flattened'])

    def test_extension_clamps_are_native_stops_with_bounded_complete_field(self):
        xref,_,_ = linear_fixture(self.pdf, coords='[40 0 80 0]')
        result = self.extract(xref)
        self.assertEqual(result['object']['style']['fill_gradient']['stops'], [
            {'offset':0, 'color':'#FF0000'}, {'offset':.25, 'color':'#FF0000'},
            {'offset':.75, 'color':'#0000FF'}, {'offset':1, 'color':'#0000FF'}])
        self.assertEqual(result['provenance']['gradient_encoding_proof']['maximum_component_encoding_error_exact'], '0')
        xref,_,_ = linear_fixture(self.pdf, coords='[40 0 80 0]', extend='[false true]')
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'unextended axial domain'):
            self.extract(xref)

    def test_indirect_function_and_decimal_colors_have_explicit_encoding_bound(self):
        xref,_,_ = linear_fixture(self.pdf, indirect_function=True,
            function='<< /FunctionType 2 /Domain [0 1] /C0 [.835 .91 .831] /C1 [.592 .816 .467] /N 1 >>')
        result = self.extract(xref)
        proof = result['provenance']
        self.assertIsInstance(proof['function']['function_xref'], int)
        self.assertLessEqual(proof['gradient_encoding_proof']['maximum_component_encoding_error'], 1/255)
        self.assertIn('excludes DrawingML', proof['gradient_encoding_proof']['scope'])

    def test_near_clamp_merges_only_identical_encoded_colors(self):
        xref,_,_ = linear_fixture(self.pdf, coords='[20.00001 0 100 0]')
        proof = self.extract(xref)['provenance']['gradient_encoding_proof']
        self.assertTrue(any(r.get('merged_with_identical_encoded_color') for r in proof['stop_encodings']))
        self.assertLessEqual(proof['maximum_component_encoding_error'], 1/255)

    def test_unsupported_functions_domains_colors_and_patterns_fail_closed(self):
        cases = [dict(function='<< /FunctionType 2 /Domain [0 1] /N 2 >>'),
                 dict(function='<< /FunctionType 2 /Domain [0 2] /N 1 >>'),
                 dict(function='<< /FunctionType 2 /Domain [0 1] /N 1 /Range [0 .5 0 1 0 1] >>'),
                 dict(function='<< /FunctionType 2 /Domain [0 1] /N 1 /C0 [1 0] >>'),
                 dict(function='<< /FunctionType 2 /Domain [0 1] /N 1 /C1 [2 0 0] >>'),
                 dict(function='<< /FunctionType 2 /Domain [0 1] /N true >>'),
                 dict(colorspace='/DeviceCMYK'), dict(shade_extra='/BBox [0 0 100 100]'),
                 dict(pattern=True, matrix='[1 0 0 0 0 0]'),
                 dict(pattern=True, matrix='[1 0 0 1 false 0]'),
                 dict(pattern=True, pattern_extra='/ExtGState << /ca .5 >>')]
        for case in cases:
            with self.subTest(case=case):
                xref,_,_ = linear_fixture(self.pdf, **case)
                with self.assertRaises(UnsupportedPdfShadingError):
                    self.extract(xref, resource_kind='pattern' if case.get('pattern') else 'shading')

    def test_tiny_covector_shear_not_rounded_to_cardinal(self):
        xref,_,_ = linear_fixture(self.pdf)
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'cardinal'):
            self.extract(xref, source_transform=[1,0,2**-50,1,0,0])

    def test_color_collision_at_stop_precision_is_rejected(self):
        xref,_,_ = linear_fixture(self.pdf, coords='[40 0 40.0001 0]')
        with self.assertRaisesRegex(UnsupportedPdfShadingError, 'collide at native stop precision'):
            self.extract(xref)

    def test_repeated_pattern_occurrences_keep_their_actual_clip_and_order(self):
        xref,_,_ = linear_fixture(self.pdf, pattern=True,
            contents='q 20 20 20 20 re W n /Pattern cs /P0 scn 0 0 200 120 re f Q '
                     'q 70 20 20 20 re W n /Pattern cs /P0 scn 0 0 200 120 re f Q')
        first = self.extract(xref, resource_kind='pattern')
        second = self.extract(xref, resource_kind='pattern', paint_seqno=1)
        self.assertNotEqual(first['object']['commands'], second['object']['commands'])
        self.assertEqual(second['object']['z_index'], 1)
        self.assertEqual(second['provenance']['verified_source_paint_count'], 2)

    def test_linear_native_conversion_and_clip_callback_fail_closed(self):
        xref,_,_ = linear_fixture(self.pdf)
        with patch.object(fitz.mupdf, 'fz_convert_color', return_value=(.5,0,0,0)):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'not identity'): self.extract(xref)
        with patch.object(fitz.mupdf, 'fz_walk_path', side_effect=RuntimeError('LINEAR_CLIP_SENTINEL')):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'LINEAR_CLIP_SENTINEL'): self.extract(xref)
        with patch('figure_rebuild.pdf_shading._MAX_COMMANDS', 2):
            with self.assertRaisesRegex(UnsupportedPdfShadingError, 'budget'): self.extract(xref)

    def test_gradient_field_matches_independently_rendered_pdf(self):
        xref,_,_ = linear_fixture(self.pdf, clip='20 20 m 20 90 80 90 100 20 c h W n')
        result = self.extract(xref)
        with fitz.open(self.pdf) as doc:
            expected = doc[0].get_pixmap(matrix=fitz.Matrix(4,4), alpha=True).samples
        gradient = result['object']['style']['fill_gradient']
        self.assertEqual(len(gradient['stops']), 2)
        from figure_rebuild.linear_gradient import gradient_axis, native_path_frame
        a,b,c,d = gradient_axis(native_path_frame(result['object']['commands']), gradient['angle'])
        components = [[int(s['color'][i:i+2],16)/255 for i in (1,3,5)] for s in gradient['stops']]
        commands = []
        for cmd in result['object']['commands']:
            if 'moveTo' in cmd:
                p=cmd['moveTo'];commands.append(f"{p['x']} {120-p['y']} m")
            elif 'lineTo' in cmd:
                p=cmd['lineTo'];commands.append(f"{p['x']} {120-p['y']} l")
            elif 'cubicTo' in cmd:
                p=cmd['cubicTo'];commands.append(f"{p['x1']} {120-p['y1']} {p['x2']} {120-p['y2']} {p['x']} {120-p['y']} c")
            else: commands.append('h')
        commands.append('W n /Sh0 sh')
        with fitz.open() as doc:
            page = doc.new_page(width=200,height=120)
            fun = _obj(doc,'<< /FunctionType 2 /Domain [0 1] /N 1 /C0 ['+' '.join(map(str,components[0]))+'] /C1 ['+' '.join(map(str,components[1]))+'] >>')
            shade = _obj(doc,f'<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [{a} {120-b} {c} {120-d}] /Extend [true true] /Function {fun} 0 R >>')
            resource = _obj(doc,f'<< /Shading << /Sh0 {shade} 0 R >> >>')
            doc.xref_set_key(page.xref,'Resources',f'{resource} 0 R')
            page.set_contents(_obj(doc,'<< >>',' '.join(commands).encode()))
            actual = page.get_pixmap(matrix=fitz.Matrix(4,4),alpha=True).samples
        self.assertEqual(len(expected),len(actual))
        errors = [abs(a-b) for a,b in zip(expected,actual)]
        self.assertLessEqual(max(errors),2)  # source encoding + raster rounding
        self.assertLess(sum(errors)/len(errors),.03)


if __name__ == '__main__':
    unittest.main()
