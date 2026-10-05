"""Zero-alpha RGB changes must preserve partial alpha and all other PDF data."""
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from figure_rebuild import pdf_zero_alpha_rgb as rgb

try:
    import pymupdf
except ImportError:
    pymupdf = None


class ZeroAlphaLimitsTests(unittest.TestCase):
    def test_limits_cannot_be_raised_or_coerced(self):
        for key, maximum in rgb.LIMITS.items():
            for value in (True, 0, -1, 1.0, maximum + 1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    rgb.derive_zero_alpha_rgb_pdf('missing.pdf', 'out.pdf', limits={key: value})
        for value in ([], {'unknown': 1}):
            with self.assertRaises(ValueError):
                rgb.derive_zero_alpha_rgb_pdf('missing.pdf', 'out.pdf', limits=value)


@unittest.skipUnless(pymupdf, 'optional PyMuPDF dependency')
class ZeroAlphaPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'raw.pdf'
        self.output = self.root / 'derived.pdf'

    def fixture(self, *, inverse=False, constant=None, already_white=False):
        alphas = [0, 1, 64, 128, 253, 254, 255, 0, 255, 0, 128, 255, 0, 1, 255, 0]
        if constant is not None:
            alphas = [constant] * len(alphas)
        colors = [(30 + i, 90 + i * 2, 150 + i * 3) for i in range(len(alphas))]
        if already_white:
            colors = [(255, 255, 255) if a == 0 else c for a, c in zip(alphas, colors)]
        image = Image.new('RGBA', (8, 2))
        image.putdata([(*c, a) for c, a in zip(colors, alphas)])
        stream = io.BytesIO(); image.save(stream, format='PNG')
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=120, height=80)
            page.draw_rect((2, 2, 115, 75), color=(1, 0, 0), width=1.75)
            page.insert_text((5, 78), 'preserve text/font/path')
            parent = page.insert_image((10.25, 9.5, 100.75, 62.3), stream=stream.getvalue())
            mask_value = pdf.xref_get_key(parent, 'SMask')
            if mask_value[0] != 'xref':
                mask = pdf.get_new_xref()
                pdf.update_object(mask, '<< /Type /XObject /Subtype /Image /ColorSpace /DeviceGray /Width 8 /Height 2 /BitsPerComponent 8 >>')
                pdf.xref_set_key(parent, 'SMask', f'{mask} 0 R')
            else:
                mask = int(mask_value[1].split()[0])
            pdf.update_stream(parent, bytes(channel for c in colors for channel in c))
            pdf.xref_set_key(parent, 'ColorSpace', '/DeviceRGB')
            pdf.xref_set_key(parent, 'DecodeParms', 'null')
            pdf.update_stream(mask, bytes(255 - a if inverse else a for a in alphas))
            pdf.xref_set_key(mask, 'BitsPerComponent', '8')
            pdf.xref_set_key(mask, 'DecodeParms', 'null')
            if inverse:
                pdf.xref_set_key(mask, 'Decode', '[1 0]')
            pdf.save(self.source)
        return parent, mask, alphas

    def rewrite_source(self, action):
        with pymupdf.open(self.source) as pdf:
            action(pdf)
            replacement = self.root / 'source-rewrite.pdf'
            pdf.save(replacement)
        self.source.write_bytes(replacement.read_bytes()); replacement.unlink()

    def test_all_partial_and_opaque_samples_masks_and_compositions_are_exact(self):
        for inverse in (False, True):
            with self.subTest(inverse=inverse):
                parent, mask, alphas = self.fixture(inverse=inverse)
                raw_bytes = self.source.read_bytes()
                receipt = rgb.derive_zero_alpha_rgb_pdf(self.source, self.output)
                self.assertEqual(self.source.read_bytes(), raw_bytes)
                self.assertEqual(rgb.verify_zero_alpha_rgb_pdf(self.source, self.output, receipt)['transformed_masks'], 1)
                self.assertEqual(receipt['transformed_masks'][0]['partial_alpha_pixels'], 7)
                self.assertFalse(receipt['rgb_alpha_filtering_error_bound_proved'])
                with pymupdf.open(self.source) as old, pymupdf.open(self.output) as new:
                    before, after = old.xref_stream(parent), new.xref_stream(parent)
                    self.assertEqual(old.xref_stream_raw(mask), new.xref_stream_raw(mask))
                    self.assertEqual(old.xref_object(mask), new.xref_object(mask))
                    self.assertEqual(new.xref_get_key(mask, 'Matte')[0], 'null')
                    self.assertEqual(old[0].get_texttrace(), new[0].get_texttrace())
                    self.assertEqual(old[0].get_drawings(), new[0].get_drawings())
                    for i, alpha in enumerate(alphas):
                        a, b = before[i * 3:i * 3 + 3], after[i * 3:i * 3 + 3]
                        self.assertEqual(b, a if alpha else b'\xff\xff\xff')
                        for background in ((0, 0, 0), (255, 255, 255), (17, 84, 133)):
                            self.assertEqual([c * alpha + bg * (255 - alpha) for c, bg in zip(a, background)],
                                             [c * alpha + bg * (255 - alpha) for c, bg in zip(b, background)])
                self.output.unlink()

    def test_existing_matte_decode_predictors_and_unsupported_shared_parents_are_retained(self):
        for mode in ('matte', 'rgb_decode', 'mask_decode', 'predictor', 'shared'):
            parent, mask, _ = self.fixture()
            def alter(pdf):
                if mode == 'matte': pdf.xref_set_key(mask, 'Matte', '[1 1 1]')
                elif mode == 'rgb_decode': pdf.xref_set_key(parent, 'Decode', '[0 .8 0 1 0 1]')
                elif mode == 'mask_decode': pdf.xref_set_key(mask, 'Decode', '[0 .9]')
                elif mode == 'predictor': pdf.xref_set_key(parent, 'DecodeParms', '<< /Predictor 12 >>')
                else:
                    clone = pdf.get_new_xref()
                    pdf.update_object(clone, pdf.xref_object(parent))
                    pdf.update_stream(clone, pdf.xref_stream(parent))
                    pdf.xref_set_key(clone, 'ColorSpace', '/DeviceCMYK')
            self.rewrite_source(alter)
            with self.subTest(mode=mode):
                receipt = rgb.derive_zero_alpha_rgb_pdf(self.source, self.output)
                self.assertEqual(receipt['transformed_masks'], [])
                self.assertEqual(self.output.read_bytes(), self.source.read_bytes())
            self.output.unlink()

    def test_constant_alpha_and_already_white_are_byte_identical_noops(self):
        for options in ({'constant': 0}, {'constant': 255}, {'already_white': True}):
            self.fixture(**options)
            receipt = rgb.derive_zero_alpha_rgb_pdf(self.source, self.output)
            self.assertEqual(receipt['transformed_masks'], [])
            self.assertEqual(self.source.read_bytes(), self.output.read_bytes())
            rgb.verify_zero_alpha_rgb_pdf(self.source, self.output, receipt)
            self.output.unlink()

    def test_lower_resource_limits_fail_before_writing_or_mutating_source(self):
        self.fixture(); before = self.source.read_bytes()
        for key in rgb.LIMITS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                rgb.derive_zero_alpha_rgb_pdf(self.source, self.output, limits={key: 1})
            self.assertFalse(self.output.exists())
            self.assertEqual(self.source.read_bytes(), before)

    def test_raw_and_existing_outputs_cannot_be_overwritten(self):
        self.fixture()
        with self.assertRaises(ValueError): rgb.derive_zero_alpha_rgb_pdf(self.source, self.source)
        self.output.write_bytes(b'retained failed candidate')
        with self.assertRaises(ValueError): rgb.derive_zero_alpha_rgb_pdf(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'retained failed candidate')

    def test_tampered_masks_visible_rgb_hidden_rgb_geometry_fonts_and_paths_are_rejected_after_rebinding(self):
        parent, mask, _ = self.fixture()
        receipt = rgb.derive_zero_alpha_rgb_pdf(self.source, self.output)
        for mode in ('partial_rgb', 'opaque_rgb', 'hidden_rgb', 'mask', 'matte', 'interpolate', 'size', 'path', 'font', 'page'):
            altered = self.root / (mode + '.pdf')
            with pymupdf.open(self.output) as pdf:
                if mode.endswith('_rgb'):
                    index = {'partial_rgb': 1, 'opaque_rgb': 6, 'hidden_rgb': 0}[mode]
                    data = bytearray(pdf.xref_stream(parent)); data[index * 3] ^= 1
                    pdf.update_stream(parent, data)
                elif mode == 'mask':
                    data = bytearray(pdf.xref_stream(mask)); data[1] ^= 1; pdf.update_stream(mask, data)
                elif mode == 'matte': pdf.xref_set_key(mask, 'Matte', '[1 1 1]')
                elif mode == 'interpolate': pdf.xref_set_key(parent, 'Interpolate', 'true')
                elif mode == 'size': pdf.xref_set_key(parent, 'Width', '4')
                elif mode == 'path':
                    xref = pdf[0].get_contents()[0]; pdf.update_stream(xref, pdf.xref_stream(xref) + b'\n0 0 m 20 20 l S\n')
                elif mode == 'font': pdf.xref_set_key(pdf[0].get_fonts()[0][0], 'BaseFont', '/Courier')
                else: pdf.xref_set_key(pdf[0].xref, 'MediaBox', '[0 0 121 80]')
                pdf.save(altered)
            changed = deepcopy(receipt)
            changed['derived_pdf'] = {'path': str(altered), 'sha256': hashlib.sha256(altered.read_bytes()).hexdigest()}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                rgb.verify_zero_alpha_rgb_pdf(self.source, altered, changed)

    def test_receipt_types_and_equivalence_claims_cannot_be_forged(self):
        self.fixture(); receipt = rgb.derive_zero_alpha_rgb_pdf(self.source, self.output)
        for key, value in (('schema_version', True), ('decoded_work_bytes', float(receipt['decoded_work_bytes'])),
                           ('alpha_and_positive_alpha_rgb_samples_unchanged', 1),
                           ('rgb_alpha_filtering_error_bound_proved', True), ('extra_claim', True)):
            changed = deepcopy(receipt); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                rgb.verify_zero_alpha_rgb_pdf(self.source, self.output, changed)
        changed = deepcopy(receipt); changed['transformed_masks'][0]['partial_alpha_pixels'] = 7.0
        with self.assertRaises(ValueError): rgb.verify_zero_alpha_rgb_pdf(self.source, self.output, changed)


if __name__ == '__main__':
    unittest.main()
