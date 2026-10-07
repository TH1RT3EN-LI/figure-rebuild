"""Actual PDF device decoding, including color and occurrence-bound masks."""
import io
import unittest

from PIL import Image

from figure_rebuild.pdf_image_native import PdfImageNativeError, capture_native_pdf_images

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def encode(image, format='PNG'):
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


@unittest.skipIf(fitz is None, 'optional PyMuPDF source dependency is unavailable')
class NativeImageTests(unittest.TestCase):
    def document(self, width=100, height=60):
        doc = fitz.open()
        self.addCleanup(doc.close)
        return doc, doc.new_page(width=width, height=height)

    def capture(self, page, selected=None):
        return capture_native_pdf_images(page, page.get_bboxlog(), selected)

    def assert_matches_page(self, record, page, pixel, page_point, tolerance=1):
        rgba = record['pixels'].getpixel(pixel)
        actual = tuple(round(c*rgba[3]/255 + 255*(1-rgba[3]/255)) for c in rgba[:3])
        expected = page.get_pixmap(alpha=False).pixel(*page_point)
        self.assertLessEqual(max(abs(a-b) for a,b in zip(actual, expected)), tolerance,
                             (rgba, actual, expected))

    def test_same_rgb_different_smask_uses_actual_masks_not_candidate_xref(self):
        doc, page = self.document()
        for i, alpha in enumerate((64, 128)):
            page.insert_image(fitz.Rect(10+40*i, 10, 30+40*i, 30),
                              stream=encode(Image.new('RGBA', (20, 20), (255, 0, 0, alpha))))
        reopened = fitz.open(stream=doc.tobytes(), filetype='pdf')
        self.addCleanup(reopened.close)
        page = reopened[0]
        first, second = self.capture(page).values()
        self.assertEqual(first['native_digest'], second['native_digest'])
        self.assertNotEqual(first['receipt']['attached_mask']['native_digest'],
                            second['receipt']['attached_mask']['native_digest'])
        self.assertEqual(first['pixels'].getpixel((5, 5)), (255, 0, 0, 64))
        self.assertEqual(second['pixels'].getpixel((5, 5)), (255, 0, 0, 128))
        self.assertFalse(first['receipt']['xref_lookup_used'])
        self.assertTrue(first['receipt']['neutral_page_transparency_wrapper'])
        self.assertTrue(first['receipt']['attached_mask_clip_wrapper_verified'])
        self.assert_matches_page(first, page, (5, 5), (15, 15))
        self.assert_matches_page(second, page, (5, 5), (55, 15))

    def test_cmyk_jpeg_retains_pdf_intent_and_matches_actual_page(self):
        doc, page = self.document()
        image = Image.new('CMYK', (20, 20), (20, 170, 180, 30))
        image.paste((160, 10, 20, 40), (10, 0, 20, 20))
        xref = page.insert_image(fitz.Rect(10, 10, 30, 30), stream=encode(image, 'JPEG'))
        doc.xref_set_key(xref, 'Intent', '/RelativeColorimetric')
        record, = self.capture(page).values()
        self.assertEqual(record['native_digest'], page.get_image_info(hashes=True)[0]['digest'].hex())
        self.assertEqual(record['receipt']['native_channels'], 4)
        self.assertEqual(record['receipt']['color_params']['ri'], 1)
        self.assertTrue(record['receipt']['actual_color_params_used_for_rgb_conversion'])
        self.assertTrue(record['receipt']['image_intent_applied_before_callback'])
        self.assertEqual(record['receipt']['image_intent'], 1)
        self.assert_matches_page(record, page, (4, 8), (14, 18))
        self.assert_matches_page(record, page, (16, 8), (26, 18))

    def test_indexed_rgb_with_reversed_decode_matches_actual_page(self):
        doc, page = self.document()
        image_xref = doc.get_new_xref()
        doc.update_object(image_xref, '<< /Type /XObject /Subtype /Image /Width 20 /Height 20 '
                          '/BitsPerComponent 8 /ColorSpace [/Indexed /DeviceRGB 1 <ff00000000ff>] '
                          '/Decode [1 0] >>')
        doc.update_stream(image_xref, (bytes([0])*10 + bytes([255])*10)*20)
        doc.xref_set_key(page.xref, 'Resources', f'<< /XObject << /Pic {image_xref} 0 R >> >>')
        stream_xref = doc.get_new_xref()
        doc.update_object(stream_xref, '<< >>')
        doc.update_stream(stream_xref, b'q 20 0 0 20 10 30 cm /Pic Do Q')
        page.set_contents(stream_xref)
        record, = self.capture(page).values()
        self.assertEqual(record['native_digest'], page.get_image_info(hashes=True)[0]['digest'].hex())
        self.assert_matches_page(record, page, (4, 8), (14, 18))
        self.assert_matches_page(record, page, (16, 8), (26, 18))
        self.assertNotEqual(record['pixels'].getpixel((4, 8)), record['pixels'].getpixel((16, 8)))

    def test_every_paint_is_verified_even_when_only_one_image_selected(self):
        doc, page = self.document()
        page.draw_rect(fitz.Rect(60, 10, 80, 30), fill=(0, 0, 1))
        page.insert_image(fitz.Rect(10, 10, 30, 30), stream=encode(Image.new('RGB', (20, 20), 'red')))
        log = page.get_bboxlog()
        selected = {i for i, record in enumerate(log) if record[0] == 'fill-image'}
        result = capture_native_pdf_images(page, log, selected)
        record, = result.values()
        self.assertEqual(record['receipt']['verified_total_paint_count'], len(log))
        tampered = list(log)
        kind, box = tampered[0]
        tampered[0] = (kind, (box[0]+.01, *box[1:]))
        with self.assertRaisesRegex(PdfImageNativeError, 'type/bbox'):
            capture_native_pdf_images(page, tampered, selected)

    def test_draw_alpha_rejected_only_for_affected_selected_paint(self):
        doc, page = self.document()
        for i in range(2):
            page.insert_image(fitz.Rect(10+40*i, 10, 30+40*i, 30),
                              stream=encode(Image.new('RGB', (20, 20), ('red', 'blue')[i])))
        resources = int(doc.xref_get_key(page.xref, 'Resources')[1].split()[0])
        doc.xref_set_key(resources, 'ExtGState', '<< /Half << /ca .5 >> >>')
        stream = page.get_contents()[0]
        doc.update_stream(stream, b'q /Half gs\n' + doc.xref_stream(stream) + b'\nQ')
        log = page.get_bboxlog()
        images = [i for i, row in enumerate(log) if row[0] == 'fill-image']
        with self.assertRaisesRegex(PdfImageNativeError, 'draw alpha'):
            capture_native_pdf_images(page, log, {images[0]})
        result = capture_native_pdf_images(page, log, {images[1]})
        self.assertEqual(set(result), {images[1]})

    def test_nontrivial_blend_group_is_rejected(self):
        doc, page = self.document()
        page.insert_image(fitz.Rect(10, 10, 30, 30), stream=encode(Image.new('RGBA', (20, 20), (255, 0, 0, 64))))
        resources = int(doc.xref_get_key(page.xref, 'Resources')[1].split()[0])
        doc.xref_set_key(resources, 'ExtGState', '<< /Blend << /BM /Multiply >> >>')
        stream = page.get_contents()[0]
        doc.update_stream(stream, b'q /Blend gs\n' + doc.xref_stream(stream) + b'\nQ')
        with self.assertRaisesRegex(PdfImageNativeError, 'transparency group'):
            self.capture(page)

    def test_page_group_with_blending_colorspace_is_rejected(self):
        doc, page = self.document()
        page.insert_image(fitz.Rect(10, 10, 30, 30), stream=encode(Image.new('RGBA', (20, 20), (255, 0, 0, 64))))
        doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /CS /DeviceCMYK >>')
        reopened = fitz.open(stream=doc.tobytes(), filetype='pdf')
        self.addCleanup(reopened.close)
        with self.assertRaisesRegex(PdfImageNativeError, 'transparency group'):
            self.capture(reopened[0])

    def test_matte_fails_closed_before_composing_alpha_twice(self):
        doc, page = self.document()
        xref = page.insert_image(fitz.Rect(10, 10, 30, 30), stream=encode(Image.new('RGBA', (20, 20), (255, 128, 128, 128))))
        mask = int(doc.xref_get_key(xref, 'SMask')[1].split()[0])
        doc.xref_set_key(mask, 'Matte', '[1 1 1]')
        with self.assertRaisesRegex(PdfImageNativeError, 'Matte'):
            self.capture(page)

    def test_color_key_mask_is_native_alpha_and_matches_page(self):
        doc, page = self.document()
        image = Image.new('RGB', (20, 20), 'red')
        image.paste((0, 0, 255), (10, 0, 20, 20))
        xref = page.insert_image(fitz.Rect(10, 10, 30, 30), stream=encode(image))
        doc.xref_set_key(xref, 'Mask', '[255 255 0 0 0 0]')
        record, = self.capture(page).values()
        self.assertEqual(record['pixels'].getpixel((4, 8))[3], 0)
        self.assertEqual(record['pixels'].getpixel((16, 8)), (0, 0, 255, 255))
        self.assertTrue(record['receipt']['color_key_alpha_handled_by_native_decoder'])
        self.assertIsNone(record['receipt']['attached_mask'])
        self.assert_matches_page(record, page, (4, 8), (14, 18))
        self.assert_matches_page(record, page, (16, 8), (26, 18))

    def test_explicit_device_rgb_page_wrapper_and_straight_mask_keep_intrinsic_bytes(self):
        doc, page = self.document()
        source = Image.new('RGBA', (20,20), (47, 113, 199, 17))
        source.putpixel((5,5), (213, 29, 91, 231))
        # Construct the original PDF resources directly. Inserting an RGBA
        # PNG through MuPDF would itself introduce a premultiply round trip
        # before the tested source callback ever receives the native samples.
        mask = doc.get_new_xref(); doc.update_object(mask, '<< /Type /XObject /Subtype /Image /Width 20 /Height 20 /BitsPerComponent 8 /ColorSpace /DeviceGray >>')
        doc.update_stream(mask, source.getchannel('A').tobytes())
        image = doc.get_new_xref(); doc.update_object(image, f'<< /Type /XObject /Subtype /Image /Width 20 /Height 20 /BitsPerComponent 8 /ColorSpace /DeviceRGB /SMask {mask} 0 R >>')
        doc.update_stream(image, source.convert('RGB').tobytes())
        doc.xref_set_key(page.xref, 'Resources', f'<< /XObject << /Pic {image} 0 R >> >>')
        stream = doc.get_new_xref(); doc.update_object(stream, '<< >>')
        doc.update_stream(stream, b'q 20 0 0 20 10 30 cm /Pic Do Q'); page.set_contents(stream)
        doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB /I true >>')
        reopened = fitz.open(stream=doc.tobytes(), filetype='pdf'); self.addCleanup(reopened.close); page = reopened[0]
        with self.assertRaisesRegex(PdfImageNativeError, 'transparency group'): self.capture(page)
        record, = capture_native_pdf_images(page, page.get_bboxlog(), None,
            allow_device_rgb_page_wrapper=True, preserve_straight_mask_colors=True).values()
        self.assertEqual(record['pixels'].tobytes(), source.tobytes())
        self.assertTrue(record['receipt']['explicit_device_rgb_page_wrapper'])
        self.assertTrue(record['receipt']['straight_attached_mask_rgb_preserved'])
        self.assertFalse(record['receipt']['straight_rgba_via_native_png_roundtrip'])
        # The output can be filtered again from intrinsic data; it is not a
        # fixed-resolution source render masquerading as original image pixels.
        self.assertEqual(record['pixels'].size, (20,20)); self.assert_matches_page(record, page, (4,4), (14,14), 2)

    def test_explicit_wrapper_does_not_admit_cmyk_or_blend_children_or_bad_flags(self):
        doc, page = self.document()
        page.insert_image(fitz.Rect(10,10,30,30), stream=encode(Image.new('RGBA',(20,20),(47,113,199,17))))
        doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /CS /DeviceCMYK /I true >>')
        reopened = fitz.open(stream=doc.tobytes(),filetype='pdf'); self.addCleanup(reopened.close)
        with self.assertRaisesRegex(PdfImageNativeError, 'transparency group'):
            capture_native_pdf_images(reopened[0], reopened[0].get_bboxlog(), None, allow_device_rgb_page_wrapper=True)
        for value in (1, None, 'true'):
            with self.assertRaisesRegex(PdfImageNativeError, 'boolean'):
                capture_native_pdf_images(page, page.get_bboxlog(), None, allow_device_rgb_page_wrapper=value)
        # A canonical RGB root must not authorize an active blending child.
        doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB /I true >>')
        resources = int(doc.xref_get_key(page.xref, 'Resources')[1].split()[0])
        doc.xref_set_key(resources, 'ExtGState', '<< /Blend << /BM /Multiply >> >>')
        stream = page.get_contents()[0]
        doc.update_stream(stream, b'q /Blend gs\n' + doc.xref_stream(stream) + b'\nQ')
        reopened = fitz.open(stream=doc.tobytes(), filetype='pdf'); self.addCleanup(reopened.close)
        with self.assertRaisesRegex(PdfImageNativeError, 'transparency group'):
            capture_native_pdf_images(reopened[0], reopened[0].get_bboxlog(), None,
                                     allow_device_rgb_page_wrapper=True, preserve_straight_mask_colors=True)


if __name__ == '__main__':
    unittest.main()
