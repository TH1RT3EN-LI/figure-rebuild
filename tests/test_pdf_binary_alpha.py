"""Actual PDF samples, shared masks, bounded decoding and final replay."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib
from PIL import Image, ImageDraw

from figure_rebuild import pdf_binary_alpha as binary
from figure_rebuild.native_preview import validate_pdf_alpha_policy

try:
    import pymupdf
except ImportError:
    pymupdf = None


class BinaryAlphaCoreTests(unittest.TestCase):
    def test_limits_and_policy_reject_invalid_types_before_file_access(self):
        with patch.object(binary, '_load', side_effect=AssertionError('file access')):
            for value in [True, {'max_input_bytes': True}, {'max_objects': 0},
                          {'max_images': 2049}, {'unknown': 1}]:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    binary.derive_binary_alpha_pdf('missing.pdf', 'out.pdf', limits=value)
        self.assertIsNone(validate_pdf_alpha_policy(None, 'artifact'))
        self.assertEqual(validate_pdf_alpha_policy(binary.POLICY, 'libreoffice'), binary.POLICY)
        for value, backend in [(True, 'libreoffice'), ('auto', 'libreoffice'), (binary.POLICY, 'artifact')]:
            with self.assertRaises(ValueError):
                validate_pdf_alpha_policy(value, backend)

    def test_bounded_flate_rejects_expansion_trailing_data_and_wrong_extent(self):
        class Pdf:
            def __init__(self, data): self.data = data
            def xref_get_key(self, xref, key):
                return ('name', '/FlateDecode') if key == 'Filter' else ('null', 'null')
            def xref_stream_raw(self, xref): return self.data
        self.assertEqual(binary._stream(Pdf(zlib.compress(b'abcdefgh')), 1, 8), b'abcdefgh')
        for data in [zlib.compress(b'x' * 1_000_000), zlib.compress(b'abcdefgh') + b'junk',
                     zlib.compress(b'abc'), b'invalid']:
            with self.assertRaises(ValueError):
                binary._stream(Pdf(data), 1, 8)

    def test_exact_decode_domain_and_long_numeric_rejection(self):
        self.assertEqual(binary._decode(('array', '[1.0 +0]'), 1), [1, 0])
        for value in ['[1e0 0]', '[0 1 2]', '[0 ' + '1' * 300 + ']', '[0 1] junk']:
            self.assertIsNone(binary._decode(('array', value), 1))


@unittest.skipUnless(pymupdf, 'optional PyMuPDF dependency')
class BinaryAlphaPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'raw.pdf'
        self.output = self.root / 'derived.pdf'

    def fixture(self, partial=False, inverse=False):
        image = Image.new('RGBA', (40, 24), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 3, 35, 20), fill=(30, 90, 150, 128 if partial else 255))
        draw.rectangle((9, 6, 28, 17), fill=(255, 255, 255, 255))
        stream = io.BytesIO(); image.save(stream, format='PNG')
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=120, height=80)
            page.draw_rect((2, 2, 115, 75), color=(1, 0, 0))
            page.insert_text((5, 78), 'unchanged vector/font')
            parent = page.insert_image((10.25, 9.5, 100.75, 62.3), stream=stream.getvalue())
            mask = int(pdf.xref_get_key(parent, 'SMask')[1].split()[0])
            # The generic PNG importer optimizes binary masks to 1 bit and
            # writes indirect color spaces. This fixture deliberately selects
            # the declared 8-bit, direct DeviceRGB/Gray policy domain.
            pdf.update_stream(parent, image.convert('RGB').tobytes())
            pdf.xref_set_key(parent, 'ColorSpace', '/DeviceRGB')
            pdf.xref_set_key(parent, 'DecodeParms', 'null')
            pdf.update_stream(mask, image.getchannel('A').tobytes())
            pdf.xref_set_key(mask, 'BitsPerComponent', '8')
            pdf.xref_set_key(mask, 'DecodeParms', 'null')
            if inverse:
                data = pdf.xref_stream(mask).translate(bytes(range(255, -1, -1)))
                pdf.update_stream(mask, data)
                pdf.xref_set_key(mask, 'Decode', '[1 0]')
            pdf.save(self.source)
        return parent, mask

    def rewrite_source(self, action):
        with pymupdf.open(self.source) as pdf:
            action(pdf)
            replacement = self.root / 'replaced-source.pdf'
            pdf.save(replacement)
        self.source.write_bytes(replacement.read_bytes()); replacement.unlink()

    def test_positive_and_inverse_binary_masks_preserve_every_decoded_rgba_sample(self):
        for inverse in (False, True):
            with self.subTest(inverse=inverse):
                parent, mask = self.fixture(inverse=inverse)
                before = self.source.read_bytes()
                receipt = binary.derive_binary_alpha_pdf(self.source, self.output)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(len(receipt['transformed_masks']), 1)
                receipt = json.loads(json.dumps(receipt))
                self.assertEqual(binary.verify_binary_alpha_pdf(self.source, self.output, receipt)['status'], 'PASS')
                with pymupdf.open(self.source) as old, pymupdf.open(self.output) as new:
                    raw = old.xref_stream(parent); derived = new.xref_stream(parent)
                    values = old.xref_stream(mask)
                    self.assertEqual(values, new.xref_stream(mask))
                    self.assertEqual(new.xref_get_key(mask, 'Matte'), ('array', '[1 1 1]'))
                    transparent = 255 if inverse else 0
                    for i, value in enumerate(values):
                        if value == transparent:
                            self.assertEqual(derived[i * 3:i * 3 + 3], b'\xff\xff\xff')
                        else:
                            self.assertEqual(derived[i * 3:i * 3 + 3], raw[i * 3:i * 3 + 3])
                self.output.unlink()

    def test_partial_alpha_is_retained_without_quantization_or_new_matte(self):
        self.fixture(partial=True)
        receipt = binary.derive_binary_alpha_pdf(self.source, self.output)
        self.assertEqual(receipt['transformed_masks'], [])
        self.assertEqual(receipt['retained_masks'][0]['reason'], 'nonbinary_alpha_retained')
        self.assertEqual(self.output.read_bytes(), self.source.read_bytes())

    def test_existing_matte_and_nondefault_rgb_decode_remain_unchanged(self):
        for field, value in [('Matte', '[0 0 0]'), ('Decode', '[0 .8 0 1 0 1]')]:
            parent, mask = self.fixture()
            target = mask if field == 'Matte' else parent
            self.rewrite_source(lambda pdf: pdf.xref_set_key(target, field, value))
            receipt = binary.derive_binary_alpha_pdf(self.source, self.output)
            self.assertEqual(receipt['transformed_masks'], [])
            self.assertEqual(self.output.read_bytes(), self.source.read_bytes())
            self.output.unlink()

    def test_shared_mask_with_unsupported_parent_blocks_the_whole_mask(self):
        parent, mask = self.fixture()
        def alter(pdf):
            clone = pdf.get_new_xref()
            pdf.update_object(clone, pdf.xref_object(parent))
            pdf.update_stream(clone, pdf.xref_stream(parent))
            pdf.xref_set_key(clone, 'ColorSpace', '/DeviceCMYK')
        self.rewrite_source(alter)
        receipt = binary.derive_binary_alpha_pdf(self.source, self.output)
        self.assertEqual(receipt['transformed_masks'], [])
        self.assertEqual(receipt['retained_masks'][0]['reason'], 'unsupported_or_shared_parent_context')
        self.assertEqual(self.source.read_bytes(), self.output.read_bytes())

    def test_budget_failure_precedes_writes_and_source_mutation(self):
        self.fixture()
        before = self.source.read_bytes()
        for limits in [{'max_input_bytes': 1}, {'max_objects': 1}, {'max_images': 1},
                       {'max_image_pixels': 1}, {'max_decoded_work_bytes': 1}]:
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                binary.derive_binary_alpha_pdf(self.source, self.output, limits=limits)
            self.assertFalse(self.output.exists())
            self.assertEqual(self.source.read_bytes(), before)

    def test_destination_never_overwrites_raw_or_existing_output(self):
        self.fixture()
        with self.assertRaises(ValueError):
            binary.derive_binary_alpha_pdf(self.source, self.source)
        self.output.write_bytes(b'preserved')
        with self.assertRaises(ValueError):
            binary.derive_binary_alpha_pdf(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'preserved')

    def test_tampering_is_rejected_even_after_rebinding_derived_file_hash(self):
        parent, mask = self.fixture()
        receipt = binary.derive_binary_alpha_pdf(self.source, self.output)
        for mode in ['rgb', 'matte', 'content', 'scalar']:
            altered = self.root / (mode + '.pdf')
            with pymupdf.open(self.output) as pdf:
                if mode == 'rgb':
                    data = bytearray(pdf.xref_stream(parent)); data[12 * 40 * 3 + 20 * 3] ^= 1
                    pdf.update_stream(parent, data)
                elif mode == 'matte': pdf.xref_set_key(mask, 'Matte', '[0 0 0]')
                elif mode == 'content':
                    xref = pdf[0].get_contents()[0]
                    pdf.update_stream(xref, pdf.xref_stream(xref) + b'\n0 0 m 20 20 l S\n')
                else:
                    for xref in range(1, pdf.xref_length()):
                        if pdf.xref_get_key(xref, 'Subtype') == ('name', '/Type1'):
                            pdf.xref_set_key(xref, 'BaseFont', '/Courier'); break
                pdf.save(altered)
            changed = deepcopy(receipt)
            changed['derived_pdf'] = {'path': str(altered), 'sha256': hashlib.sha256(altered.read_bytes()).hexdigest()}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                binary.verify_binary_alpha_pdf(self.source, altered, changed)
        for key, value in [('schema_version', True), ('rgb_alpha_filtering_error_bound_proved', True)]:
            changed = deepcopy(receipt); changed[key] = value
            with self.assertRaises(ValueError): binary.verify_binary_alpha_pdf(self.source, self.output, changed)
        changed = deepcopy(receipt); changed['transformed_masks'][0]['size'][0] = 40.0
        with self.assertRaises(ValueError): binary.verify_binary_alpha_pdf(self.source, self.output, changed)


if __name__ == '__main__':
    unittest.main()
