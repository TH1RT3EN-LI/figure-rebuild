"""Independent failures and occurrence-identity checks for native PDF image capture."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from figure_rebuild.pdf_images import extract_pdf_images, UnsupportedPdfImageError
from figure_rebuild.pdf_image_native import capture_native_pdf_images, PdfImageNativeError
try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def png(color):
    stream = io.BytesIO()
    Image.new('RGBA', (16, 16), color).save(stream, format='PNG')
    return stream.getvalue()


@unittest.skipIf(fitz is None, 'optional PyMuPDF source dependency unavailable')
class PdfImageAdversarialTests(unittest.TestCase):
    def document(self, colors=((255, 0, 0, 255),)):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'identity.pdf'
        with fitz.open() as doc:
            page = doc.new_page(width=100, height=100)
            for i, color in enumerate(colors):
                page.insert_image(fitz.Rect(10, 10, 30, 30), stream=png(color))
                page.draw_rect(fitz.Rect(50, 50, 60, 60), color=None, fill=(i/5, 0, 1))
            doc.save(path)
        return path

    def test_identical_rgb_same_bbox_interleaved_paints_preserve_distinct_masks(self):
        path = self.document(((255, 0, 0, 64), (255, 0, 0, 128), (255, 0, 0, 192)))
        records = extract_pdf_images(path)
        self.assertEqual([r['image_index'] for r in records], [0, 1, 2])
        self.assertEqual([r['paint_seqno'] for r in records], [0, 2, 4])
        self.assertEqual(len({r['provenance']['image_info_digest'] for r in records}), 1)
        self.assertEqual(len({r['provenance']['native_image']['attached_mask']['native_digest'] for r in records}), 3)
        for r, alpha in zip(records, (64, 128, 192)):
            with Image.open(io.BytesIO(r['asset_bytes'])) as image:
                self.assertEqual(image.getpixel((8, 8)), (255, 0, 0, alpha))
            self.assertEqual(r['provenance']['native_image']['verified_total_paint_count'], 6)
        selected, = extract_pdf_images(path, image_indices=[1])
        self.assertEqual(selected['paint_seqno'], 2)
        self.assertEqual(selected['asset_bytes'], records[1]['asset_bytes'])

    def test_state_callback_failure_is_not_silently_ignored(self):
        path = self.document()
        with fitz.open(path) as doc, patch.object(
                fitz.mupdf, 'll_fz_keep_default_colorspaces',
                side_effect=RuntimeError('injected defaults callback failure')):
            page = doc[0]
            with self.assertRaisesRegex(PdfImageNativeError, 'traversal failed'):
                capture_native_pdf_images(page, page.get_bboxlog(), None)

    def test_decode_callback_failure_does_not_return_partial_assets(self):
        path = self.document(((255, 0, 0, 64), (255, 0, 0, 128)))
        with fitz.open(path) as doc:
            page = doc[0]
            bbox = page.get_bboxlog()
            with patch.object(fitz.mupdf, 'll_fz_get_unscaled_pixmap_from_image',
                              side_effect=RuntimeError('injected decode callback failure')):
                with self.assertRaisesRegex(PdfImageNativeError, 'injected decode callback failure'):
                    capture_native_pdf_images(page, bbox, None)

    def test_missing_required_device_capability_fails_explicitly(self):
        path = self.document()
        with fitz.open(path) as doc:
            page = doc[0]
            bbox = page.get_bboxlog()
            with patch.object(fitz, 'mupdf', None):
                with self.assertRaisesRegex(PdfImageNativeError, 'required native image-device capabilities'):
                    capture_native_pdf_images(page, bbox, None)

    def test_incompatible_virtual_callback_capability_fails_explicitly(self):
        path = self.document()
        with fitz.open(path) as doc:
            page = doc[0]
            bbox = page.get_bboxlog()
            with patch.object(fitz.mupdf.FzDevice2, 'use_virtual_begin_group', None):
                with self.assertRaisesRegex(PdfImageNativeError, 'traversal failed'):
                    capture_native_pdf_images(page, bbox, None)

    def test_selected_nontrivial_group_outside_roi_is_still_rejected(self):
        path = self.document(((255, 0, 0, 128),))
        with fitz.open(path) as doc:
            doc.xref_set_key(doc[0].xref, 'Group', '<< /S /Transparency /CS /DeviceCMYK >>')
            grouped = path.with_name('grouped.pdf')
            doc.save(grouped)
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'transparency group'):
            extract_pdf_images(grouped, region=[70, 70, 90, 90], image_indices=[0])

    def test_selected_identity_outside_roi_cannot_hide_corrupted_paint_log(self):
        path = self.document()
        with fitz.open(path) as doc:
            page = doc[0]
            bbox = list(page.get_bboxlog())
            kind, box = bbox[-1]
            bbox[-1] = ('fill-image' if kind != 'fill-image' else 'fill-path', box)
            with self.assertRaisesRegex(PdfImageNativeError, 'type/bbox'):
                capture_native_pdf_images(page, bbox, {0})


if __name__ == '__main__':
    unittest.main()
