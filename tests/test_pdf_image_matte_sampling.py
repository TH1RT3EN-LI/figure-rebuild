"""Matte forwarding: source render oracle, alpha and clipping, strict opt-in."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from figure_rebuild.pdf_images import extract_pdf_images, UnsupportedPdfImageError
from figure_rebuild.pdf_image_render import render_native_pdf_image
from figure_rebuild.pdf_image_native import PdfImageNativeError

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def png(image):
    output = io.BytesIO(); image.save(output, format='PNG'); return output.getvalue()


@unittest.skipUnless(fitz, 'optional PyMuPDF source dependency missing')
class NativeMatteSamplingTests(unittest.TestCase):
    def fixture(self, matte=(0,0,0), *, overlay=False, repeated=False):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        document=fitz.open();self.addCleanup(document.close)
        page=document.new_page(width=120,height=100)
        # Colors in a Matte image are preblended, not straight RGB. The source
        # native decoder must undo this while preserving the real mask draw.
        image=Image.new('RGBA',(24,32))
        for y in range(32):
            for x in range(24):
                alpha=[0,64,128,255][x//6]
                color=(255,0,0)if y<16 else(0,0,255)
                image.putpixel((x,y),tuple(round(c*alpha/255+b*(1-alpha/255))for c,b in zip(color,matte))+(alpha,))
        xref=page.insert_image(fitz.Rect(20.25,10.25,80.75,70.75),stream=png(image),keep_proportion=False)
        document.xref_set_key(xref,'ColorSpace','/DeviceRGB')
        mask=int(document.xref_get_key(xref,'SMask')[1].split()[0])
        document.xref_set_key(mask,'Matte','['+' '.join(str(v/255)for v in matte)+']')
        if repeated:
            page.insert_image(fitz.Rect(85,12,115,60),xref=xref,keep_proportion=False)
        if overlay:
            page.draw_rect(fitz.Rect(42,20,48,27),color=None,fill=(0,1,0))
            page.insert_text((22,88),'independent text')
        path=Path(tmp.name)/'source.pdf';document.save(path)
        return document,page,path,xref,mask

    def extract(self,path,**options):
        return extract_pdf_images(path,native_occurrence_rendering=True,
                                  allow_native_matte_sampling=True,**options)

    def image(self,record):return Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')

    def reference(self,path,record,scale=8):
        # Use a new source document and the ordinary whole-page rasterizer.
        # It does not use the selected-image forwarding implementation.
        with fitz.open(path)as doc:
            pix=doc[0].get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=True)
            image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGBA')
        box=record['box']
        return image.crop(tuple(round(v*scale)for v in
            [box['x'],box['y'],box['x']+box['width'],box['y']+box['height']]))

    def test_default_decoded_and_native_modes_still_reject_matte(self):
        _,_,path,_,_=self.fixture()
        with self.assertRaises(UnsupportedPdfImageError):extract_pdf_images(path)
        with self.assertRaisesRegex(UnsupportedPdfImageError,'Matte'):
            extract_pdf_images(path,native_occurrence_rendering=True)

    def test_black_and_white_matte_match_independent_source_grid_and_alpha(self):
        for matte in [(0,0,0),(255,255,255)]:
            with self.subTest(matte=matte):
                _,_,path,_,_=self.fixture(matte)
                record,=self.extract(path)
                actual=self.image(record);expected=self.reference(path,record)
                self.assertEqual(actual.tobytes(),expected.tobytes())
                box=record['box']
                pixel=actual.getpixel((int((43-box['x'])*8),int((24-box['y'])*8)))
                # Native 8-bit rendering may quantize 64 to 63; a second alpha
                # merge would produce 16. The full source oracle is byte-equal.
                self.assertLessEqual(abs(pixel[3]-64),1)
                # One premultiplied byte level becomes about four straight
                # RGB levels at quarter alpha, plus encode rounding.
                self.assertGreaterEqual(pixel[0],248)
                self.assertLessEqual(max(pixel[1:3]),7)
                receipt=record['provenance']['native_image']
                self.assertTrue(receipt['native_matte_combination_present'])
                self.assertFalse(receipt['decoded_alpha_manually_recombined'])
                self.assertFalse(receipt['matte_color_reconstructed'])
                self.assertIsNone(receipt['matte_sampling_rgb_alpha_error_bound'])
                self.assertFalse(receipt['exact_matte_decomposition_claimed'])
                self.assertTrue(receipt['attached_mask']['actual_handle_and_matrix_and_sequence_bound'])
                self.assertEqual(receipt['pixel_metadata_capture_phase'],'after_all_native_draw_devices_closed_and_png_encoded')

    def test_roi_keeps_full_transform_and_samples_original_clip(self):
        _,_,path,_,_=self.fixture()
        record,=self.extract(path,region=[35.5,20.5,73.5,63.5])
        self.assertEqual(record['full_source_box'],dict(x=20.25,y=10.25,width=60.5,height=60.5))
        self.assertEqual(record['box'],dict(x=35,y=20,width=39,height=44))
        actual=self.image(record);expected=self.reference(path,record)
        # Reference includes the source beyond the added ROI. Compare only its
        # interior; the storage padding must remain transparent in the asset.
        bounds=[4,4,actual.width-4,actual.height-4]
        self.assertEqual(actual.crop(bounds).tobytes(),expected.crop(bounds).tobytes())
        self.assertEqual(actual.getpixel((1,1))[3],0)
        self.assertEqual(record['crop'],dict(left=0.,top=0.,right=0.,bottom=0.))

    def test_other_paints_are_not_burned_into_matte_asset(self):
        _,_,path,_,_=self.fixture(overlay=True)
        record,=self.extract(path)
        image=self.image(record);box=record['box']
        pixel=image.getpixel((int((45-box['x'])*8),int((23-box['y'])*8)))
        self.assertLessEqual(pixel[1],3)
        self.assertLessEqual(abs(pixel[3]-64),1)
        receipt=record['provenance']['native_image']
        self.assertEqual(receipt['image_paints_forwarded'],1)
        self.assertEqual(receipt['independent_text_path_shading_other_image_paints_forwarded'],0)

    def test_repeated_image_is_selected_by_actual_occurrence(self):
        _,_,path,_,_=self.fixture(repeated=True)
        first,=self.extract(path,image_indices=[0]);second,=self.extract(path,image_indices=[1])
        self.assertNotEqual(first['paint_seqno'],second['paint_seqno'])
        self.assertEqual(first['provenance']['image_info_digest'],second['provenance']['image_info_digest'])
        self.assertEqual(second['full_source_box'],dict(x=85.,y=12.,width=30.,height=48.))
        self.assertEqual(self.image(second).tobytes(),self.reference(path,second).tobytes())

    def test_decode_changes_and_resampled_masks_remain_unsupported(self):
        for change in ['image_decode','mask_decode','mask_size']:
            with self.subTest(change=change):
                doc,_,path,xref,mask=self.fixture()
                if change=='image_decode':doc.xref_set_key(xref,'Decode','[1 0 1 0 1 0]')
                elif change=='mask_decode':doc.xref_set_key(mask,'Decode','[1 0]')
                else:
                    doc.xref_set_key(mask,'Width','12');doc.xref_set_key(mask,'Height','16');doc.update_stream(mask,bytes([128])*(12*16))
                changed=path.with_name('unsupported.pdf');doc.save(changed)
                with self.assertRaises(UnsupportedPdfImageError):self.extract(changed)

    def test_alpha_and_blend_effects_remain_unsupported_even_outside_roi(self):
        for effect in ['/ca .5','/BM /Multiply']:
            with self.subTest(effect=effect):
                doc,page,path,_,_=self.fixture();resources=int(doc.xref_get_key(page.xref,'Resources')[1].split()[0])
                doc.xref_set_key(resources,'ExtGState','<< /E << '+effect+' >> >>')
                stream=page.get_contents()[0];doc.update_stream(stream,b'q /E gs '+doc.xref_stream(stream)+b' Q')
                changed=path.with_name('effect.pdf');doc.save(changed)
                with self.assertRaises(UnsupportedPdfImageError):self.extract(changed,region=[110,90,115,95])

    def test_option_is_boolean_and_requires_native_mode(self):
        for value in [1,None,'yes']:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'boolean'):
                extract_pdf_images('unused.pdf',allow_native_matte_sampling=value)
        with self.assertRaisesRegex(ValueError,'requires native_occurrence_rendering'):
            extract_pdf_images('unused.pdf',allow_native_matte_sampling=True)

    def test_non_rgb_matte_remains_unsupported(self):
        doc,page,path,_,mask=self.fixture()
        # A real four-channel source, rather than invalidating RGB byte counts.
        stream=io.BytesIO();Image.new('CMYK',(24,32),(0,100,180,0)).save(stream,format='TIFF')
        xref=page.insert_image(fitz.Rect(85,12,115,60),stream=stream.getvalue(),keep_proportion=False)
        doc.xref_set_key(xref,'SMask',f'{mask} 0 R')
        changed=path.with_name('cmyk.pdf');doc.save(changed)
        with self.assertRaisesRegex(UnsupportedPdfImageError,'DeviceRGB'):
            self.extract(changed,image_indices=[1])

    def test_native_replay_cookie_error_does_not_return_a_partial_asset(self):
        _,_,path,_,_=self.fixture()
        with fitz.open(path)as doc:
            page=doc[0];bbox=page.get_bboxlog();seq=next(i for i,r in enumerate(bbox)if r[0]=='fill-image')
            run=fitz.mupdf.fz_run_page
            def incomplete(*args):
                run(*args);args[-1].set_incomplete(1)
            with mock.patch.object(fitz.mupdf,'fz_run_page',side_effect=incomplete):
                with self.assertRaisesRegex(PdfImageNativeError,'incomplete'):
                    render_native_pdf_image(page,bbox,seq,source_transform=[1,0,0,1,0,0],
                        source_bounds=[20.25,10.25,80.75,70.75],user_clip_pdf=[0,0,120,100],
                        allow_native_matte_sampling=True)


if __name__=='__main__':unittest.main()
