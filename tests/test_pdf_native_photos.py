"""Photo placement must come from real PPT coordinates, never reference pixels."""
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from PIL import Image
from figure_rebuild import pdf_native_photos as photos

try:
    import pymupdf
except ImportError:
    pymupdf = None


class NativePhotoLimitTests(unittest.TestCase):
    def test_resource_limits_are_fixed_positive_integers(self):
        for key, maximum in photos.LIMITS.items():
            for value in (True, 0, -1, 1.0, maximum + 1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    photos.derive_native_photo_pdf('missing.pptx', 'missing.pdf', 'new.pdf', limits={key: value})
        with self.assertRaises(ValueError): photos.derive_native_photo_pdf('missing.pptx', 'missing.pdf', 'new.pdf', limits={'unknown': 1})


@unittest.skipUnless(pymupdf, 'optional PyMuPDF dependency')
class NativePhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pptx, self.raw, self.new = [self.root / n for n in ('native.pptx', 'raw.pdf', 'new.pdf')]
        self.native = [97631, 192881, 95250, 95250]
        self.partial = False
        self.extra = ''

    def fixture(self, *, negative=False, partial=False, state=''):
        if negative: self.native[0] = -2381
        self.partial = partial; self.extra = state
        self.image = Image.new('RGBA', (8, 8), (20, 80, 140, 255))
        for x in range(8):
            for y in range(8): self.image.putpixel((x, y), (20+x*10, 80+y*8, 140+x+y, 128 if partial and x == 0 else 255))
        b = io.BytesIO(); self.image.save(b, format='PNG'); self.encoded = b.getvalue()
        self.write_ppt()
        with pymupdf.open() as d:
            page = d.new_page(width=150, height=75)
            page.draw_line((2.4, 3.5), (98, 61), color=(1, .2, .4), width=1.3)
            x, y, w, h = [v / 12700 for v in self.native]
            # A controlled native-export drift, in physical PDF units. The
            # independent intended positions come only from the actual PPT.
            parent = page.insert_image((x+.03, y-.02, x+w+.01, y+h-.04), stream=self.encoded, keep_proportion=False)
            d.update_stream(parent, self.image.convert('RGB').tobytes())
            d.xref_set_key(parent, 'ColorSpace', '/DeviceRGB')
            d.xref_set_key(parent, 'DecodeParms', 'null')
            mask_value = d.xref_get_key(parent, 'SMask'); self.mask = None
            if mask_value[0] == 'xref':
                self.mask = int(mask_value[1].split()[0])
                d.update_stream(self.mask, self.image.getchannel('A').tobytes())
                d.xref_set_key(self.mask, 'BitsPerComponent', '8')
                d.xref_set_key(self.mask, 'DecodeParms', 'null')
            resources = int(d.xref_get_key(page.xref, 'Resources')[1].split()[0])
            d.xref_set_key(resources, 'XObject', f'<< /Im1 {parent} 0 R >>')
            data = page.read_contents().replace(b'/fzImg0', b'/Im1')
            self.content = page.get_contents()[0]
            page.set_contents(self.content); d.update_stream(self.content, data)
            d.save(self.raw)
        self.parent = parent

    def write_ppt(self):
        p, a, r = (photos.NS[k] for k in ('p', 'a', 'r'))
        x, y, w, h = self.native
        pic = f'''<p:pic><p:nvPicPr><p:cNvPr id="2" name="photo"/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="RImage"/><a:srcRect l="0" t="0" r="0" b="0"/><a:stretch/></p:blipFill>
        <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>
        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{self.extra}</p:spPr></p:pic>'''
        slide = f'<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{pic}</p:spTree></p:cSld></p:sld>'
        presentation = f'<p:presentation xmlns:p="{p}"><p:sldIdLst><p:sldId id="256"/></p:sldIdLst><p:sldSz cx="1905000" cy="952500"/></p:presentation>'
        rels = f'<Relationships><Relationship Id="RImage" Type="{r}/image" Target="../media/photo.png"/></Relationships>'
        with ZipFile(self.pptx, 'w') as z:
            for name, data in [('ppt/presentation.xml', presentation), ('ppt/slides/slide1.xml', slide),
                               ('ppt/slides/_rels/slide1.xml.rels', rels), ('ppt/media/photo.png', self.encoded)]:
                z.writestr(name, data)

    def rewrite_pdf(self, source, output, action):
        with pymupdf.open(source) as d:
            action(d); d.save(output)

    def test_fractional_and_negative_native_positions_preserve_all_pixels_alpha_and_vector_paints(self):
        for negative in (False, True):
            with self.subTest(negative=negative):
                self.fixture(negative=negative)
                ppt, pdf = self.pptx.read_bytes(), self.raw.read_bytes()
                receipt = photos.derive_native_photo_pdf(self.pptx, self.raw, self.new)
                self.assertEqual(photos.verify_native_photo_pdf(self.pptx, self.raw, self.new, receipt)['transformed_photos'], 1)
                self.assertEqual(self.pptx.read_bytes(), ppt); self.assertEqual(self.raw.read_bytes(), pdf)
                with pymupdf.open(self.raw) as old, pymupdf.open(self.new) as new:
                    self.assertEqual(old[0].get_drawings(), new[0].get_drawings())
                    self.assertEqual(old.xref_stream_raw(self.parent), new.xref_stream_raw(self.parent))
                    b = new[0].get_image_info(xrefs=True)[0]['bbox']
                    expected = [self.native[0]/12700, self.native[1]/12700,
                                (self.native[0]+self.native[2])/12700, (self.native[1]+self.native[3])/12700]
                    self.assertLess(max(abs(a-b)for a,b in zip(b,expected)), .0001)
                self.assertFalse(receipt['source_pixel_or_filter_equivalence_proved'])
                self.new.unlink()

    def test_partial_alpha_and_picture_effects_are_retained_without_changing_any_pdf_byte(self):
        for options in ({'partial': True}, {'state': '<a:effectLst/>'}):
            self.fixture(**options)
            receipt = photos.derive_native_photo_pdf(self.pptx, self.raw, self.new)
            self.assertEqual(receipt['transformed_photos'], [])
            self.assertEqual(len(receipt['retained_pictures']), 1)
            self.assertEqual(self.new.read_bytes(), self.raw.read_bytes())
            photos.verify_native_photo_pdf(self.pptx, self.raw, self.new, receipt)
            self.new.unlink()

    def test_rgb_correspondence_is_exact_and_rejects_one_visible_sample_change_before_writing(self):
        self.fixture()
        def alter(d):
            data = bytearray(d.xref_stream(self.parent)); data[0] ^= 1; d.update_stream(self.parent, data)
        changed = self.root/'bad-rgb.pdf'; self.rewrite_pdf(self.raw, changed, alter)
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            photos.derive_native_photo_pdf(self.pptx, changed, self.new)
        self.assertFalse(self.new.exists())

    def test_tampering_after_rebinding_hashes_cannot_bless_frames_pixels_alpha_or_nonimage_paints(self):
        self.fixture(); receipt = photos.derive_native_photo_pdf(self.pptx, self.raw, self.new)
        for mode in ('cm', 'rgb', 'interpolate', 'vector', 'page', 'font'):
            changed = self.root/(mode+'.pdf')
            def alter(d):
                if mode in ('cm', 'vector'):
                    data = d.xref_stream(self.content)
                    if mode == 'cm':
                        matrix = receipt['transformed_photos'][0]['derived_cm'].encode()
                        data = data.replace(matrix, b'7 0 0 7 2 2')
                    else: data += b'\n0 0 m 30 30 l S\n'
                    d.update_stream(self.content, data)
                elif mode == 'rgb':
                    data = bytearray(d.xref_stream(self.parent)); data[4] ^= 1; d.update_stream(self.parent, data)
                elif mode == 'interpolate': d.xref_set_key(self.parent, 'Interpolate', 'true')
                elif mode == 'page': d.xref_set_key(d[0].xref, 'MediaBox', '[0 0 151 75]')
                else: d[0].insert_text((10, 10), 'new font/text')
            self.rewrite_pdf(self.new, changed, alter)
            r = deepcopy(receipt); r['derived_pdf'] = {'path':str(changed),'sha256':hashlib.sha256(changed.read_bytes()).hexdigest()}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                photos.verify_native_photo_pdf(self.pptx, self.raw, changed, r)
        self.fixture(partial=True); self.new.unlink()
        receipt = photos.derive_native_photo_pdf(self.pptx, self.raw, self.new)
        changed = self.root/'alpha.pdf'
        def alpha(d):
            data = bytearray(d.xref_stream(self.mask)); data[0] ^= 1; d.update_stream(self.mask, data)
        self.rewrite_pdf(self.new, changed, alpha)
        r = deepcopy(receipt); r['derived_pdf'] = {'path':str(changed),'sha256':hashlib.sha256(changed.read_bytes()).hexdigest()}
        with self.assertRaises(ValueError): photos.verify_native_photo_pdf(self.pptx, self.raw, changed, r)

    def test_changed_native_frame_receipt_and_forged_types_are_rejected(self):
        self.fixture(); receipt = photos.derive_native_photo_pdf(self.pptx, self.raw, self.new)
        for key, value in [('schema_version', True), ('decoded_work_bytes', float(receipt['decoded_work_bytes'])),
                           ('source_reference_pixels_used', 0), ('source_pixel_or_filter_equivalence_proved', True),
                           ('native_photo_frame_guard_canvas_px', 1), ('extra', True)]:
            r = deepcopy(receipt); r[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): photos.verify_native_photo_pdf(self.pptx, self.raw, self.new, r)
        self.native[0] += 100; self.write_ppt()
        r = deepcopy(receipt); r['input_pptx'] = {'path':str(self.pptx),'sha256':hashlib.sha256(self.pptx.read_bytes()).hexdigest()}
        with self.assertRaises(ValueError): photos.verify_native_photo_pdf(self.pptx, self.raw, self.new, r)

    def test_lower_budgets_and_confused_operators_fail_before_writes(self):
        self.fixture(); ppt, pdf = self.pptx.read_bytes(), self.raw.read_bytes()
        for key in photos.LIMITS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                photos.derive_native_photo_pdf(self.pptx, self.raw, self.new, limits={key:1})
            self.assertFalse(self.new.exists())
            self.assertEqual(self.pptx.read_bytes(), ppt); self.assertEqual(self.raw.read_bytes(), pdf)
        for prefix in (b'% fake q 7 0 0 7 2 2 cm /Im1 Do Q\n', b'(q 7 0 0 7 2 2 cm /Im1 Do Q)\n', b'BI ID abc EI\n'):
            changed = self.root/'confused.pdf'
            self.rewrite_pdf(self.raw, changed, lambda d:d.update_stream(self.content,prefix+d.xref_stream(self.content)))
            with self.assertRaises(ValueError): photos.derive_native_photo_pdf(self.pptx, changed, self.new)
            self.assertFalse(self.new.exists())
            changed.unlink()

    def test_live_text_and_large_placement_mismatch_fail_closed(self):
        self.fixture()
        for mode in ('font', 'placement', 'rotation', 'pages'):
            changed=self.root/(mode+'.pdf')
            def alter(d):
                if mode=='font':d[0].insert_text((2,8),'live text')
                elif mode=='placement':
                    data=d.xref_stream(self.content);match=photos._IMAGE.search(data)
                    d.update_stream(self.content,data[:match.start('matrix')]+b'8 0 0 8 40 40'+data[match.end('matrix'):])
                elif mode=='rotation':d[0].set_rotation(90)
                else:d.new_page(width=150,height=75)
            self.rewrite_pdf(self.raw,changed,alter)
            with self.subTest(mode=mode),self.assertRaises(ValueError):photos.derive_native_photo_pdf(self.pptx,changed,self.new)
            self.assertFalse(self.new.exists())

    def test_inputs_and_existing_outputs_are_never_overwritten(self):
        self.fixture()
        for target in (self.pptx,self.raw):
            with self.assertRaises(ValueError):photos.derive_native_photo_pdf(self.pptx,self.raw,target)
        self.new.write_bytes(b'retained failed candidate')
        with self.assertRaises(ValueError):photos.derive_native_photo_pdf(self.pptx,self.raw,self.new)
        self.assertEqual(self.new.read_bytes(),b'retained failed candidate')


if __name__ == '__main__':
    unittest.main()
