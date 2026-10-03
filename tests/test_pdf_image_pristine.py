"""Real native regression: metadata decoding must not alter clipped image sampling.

The fixture is generated from a color formula; it contains no paper resources.
A narrow clip, affine placement and interpolation reproduce the MuPDF 1.28.2
image-cache effect even for one ordinary DeviceRGB image without a soft mask.
"""
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from figure_rebuild.pdf_images import extract_pdf_images
from figure_rebuild.pdf_image_render import render_native_pdf_image
from figure_rebuild.pdf_image_native import PdfImageNativeError

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def synthetic_pdf(*, alpha=False, occurrences=(0,)):
    width, height = 427, 233
    image = Image.new('RGBA' if alpha else 'RGB', (width, height))
    pixels = []
    for y in range(height):
        for x in range(width):
            rgb = ((x*13+y*7) % 256, (x*3+y*17) % 256, (x*19+y*3) % 256)
            if alpha:
                radius = ((x-width*.55)/(width*.42))**2+((y-height*.47)/(height*.42))**2
                rgb += (255-min(255, int(radius*255)),)
            pixels.append(rgb)
    image.putdata(pixels)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    with fitz.open() as document:
        page = document.new_page(width=160, height=160)
        xref = page.insert_image(fitz.Rect(5, 5, 120, 100), stream=stream.getvalue(), keep_proportion=False)
        document.xref_set_key(xref, 'Interpolate', 'true')
        mask = document.xref_get_key(xref, 'SMask')
        if mask[0] == 'xref':
            document.xref_set_key(int(mask[1].split()[0]), 'Interpolate', 'true')
        resources = int(document.xref_get_key(page.xref, 'Resources')[1].split()[0])
        document.xref_set_key(resources, 'XObject', f'<< /I {xref} 0 R >>')
        matrices = (
            '-33.7451286315918 100.758903503418 54.9577903747559 18.4080295562744 50.7975769042969 13.0355834960938',
            '-33.7451286315918 100.758903503418 54.9577903747559 18.4080295562744 56.8901062011719 6.9031677246094',
        )
        content = b'q 45 100 10 15 re W n\n'+b'\n'.join(
            ('q '+matrices[i]+' cm /I Do Q').encode() for i in occurrences)+b'\nQ'
        document.update_stream(page.get_contents()[0], content)
        return document.tobytes()


@unittest.skipIf(fitz is None, 'optional PyMuPDF source dependency missing')
class PristineImageStateTests(unittest.TestCase):
    def write_pdf(self, data):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)/'source.pdf'
        path.write_bytes(data)
        return path

    def extract(self, path, *, scale=.25, **kwargs):
        return extract_pdf_images(path, native_occurrence_rendering=True,
                                  allow_affine_rasterization=True,
                                  source_transform=[[scale, 0, 0], [0, scale, 0]], **kwargs)

    def assert_pristine_pixels(self, row, original, *, scale=.25):
        # Direct draw opens the known original fixture bytes independently and
        # performs no SVG / dictionary / image-hash / unscaled-pixmap queries.
        with fitz.open(stream=original, filetype='pdf') as document:
            pix = document[0].get_pixmap(matrix=fitz.Matrix(scale*8, scale*8), alpha=True)
            reference = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGBA')
        box = row['box']
        bounds = [box['x'], box['y'], box['x']+box['width'], box['y']+box['height']]
        reference = reference.crop(tuple(round(v*8) for v in bounds))
        actual = Image.open(io.BytesIO(row['asset_bytes'])).convert('RGBA')
        self.assertEqual(actual.size, reference.size)
        self.assertEqual(actual.tobytes(), reference.tobytes())
        receipt = row['provenance']['native_image']
        self.assertEqual(receipt['render_document_state'], 'fresh_source_bytes_per_occurrence')
        self.assertEqual(receipt['pixel_metadata_capture_phase'],
                         'after_all_native_draw_devices_closed_and_png_encoded')

    def test_clipped_rgb_and_soft_mask_sampling_match_pristine_actual_draw(self):
        for alpha in (False, True):
            data = synthetic_pdf(alpha=alpha)
            for scale in (.25, .5):
                with self.subTest(alpha=alpha, scale=scale):
                    row, = self.extract(self.write_pdf(data), scale=scale)
                    self.assert_pristine_pixels(row, data, scale=scale)
                    self.assertEqual(bool(row['provenance']['native_image']['attached_mask']), alpha)

    def test_reused_resource_does_not_inherit_first_occurrence_receipt_cache(self):
        for alpha in (False, True):
            data = synthetic_pdf(alpha=alpha, occurrences=(0, 1))
            path = self.write_pdf(data)
            both = self.extract(path)
            self.assertEqual(len(both), 2)
            self.assertEqual(both[0]['provenance']['image_info_digest'],
                             both[1]['provenance']['image_info_digest'])
            for index, row in enumerate(both):
                with self.subTest(alpha=alpha, index=index):
                    alone, = self.extract(path, image_indices=[index])
                    self.assertEqual(row['asset_bytes'], alone['asset_bytes'])
                    self.assertEqual(row['paint_seqno'], alone['paint_seqno'])
                    self.assert_pristine_pixels(row, synthetic_pdf(alpha=alpha, occurrences=(index,)))

    def test_receipt_decode_failure_is_after_completed_png_and_fails_closed(self):
        data = synthetic_pdf(alpha=True)
        with fitz.open(stream=data, filetype='pdf') as document:
            page = document[0]
            bboxlog = page.get_bboxlog()
            seqno = next(i for i, row in enumerate(bboxlog) if row[0] == 'fill-image')
            events = []
            original_close = fitz.mupdf.fz_close_device
            original_encode = fitz.Pixmap.tobytes

            def close(*args):
                result = original_close(*args)
                events.append('close')
                return result

            def encode(*args, **kwargs):
                result = original_encode(*args, **kwargs)
                events.append('encoded')
                return result

            def fail_decode(*args):
                self.assertEqual(events, ['close', 'close', 'encoded'])
                raise RuntimeError('injected receipt decoder failure')

            with mock.patch.object(fitz.mupdf, 'fz_close_device', side_effect=close), \
                    mock.patch.object(fitz.Pixmap, 'tobytes', side_effect=encode, autospec=True), \
                    mock.patch.object(fitz.mupdf, 'll_fz_get_unscaled_pixmap_from_image', side_effect=fail_decode), \
                    self.assertRaisesRegex(PdfImageNativeError, 'post-render receipt capture failed.*injected'):
                render_native_pdf_image(page, bboxlog, seqno, source_transform=(.25, 0, 0, .25, 0, 0),
                                        source_bounds=(11.25, 11.25, 13.75, 15),
                                        user_clip_pdf=(0, 0, 160, 160))


if __name__ == '__main__':
    unittest.main()
