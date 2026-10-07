"""Visible/partial-alpha invariants and adversarial saved-asset readback."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from figure_rebuild.image_hidden_rgb import derive_zero_alpha_rgb_image, verify_zero_alpha_rgb_image


class HiddenRGBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, **metadata):
        im = Image.new('RGBA', (9, 7), (11, 33, 199, 0))
        for x, a in enumerate([0, 1, 64, 128, 253, 254, 255]):
            im.putpixel((x + 1, 3), (202 - x, 31 + x, 90 + x, a))
        im.save(self.root / 'source.png', **metadata)
        obj = {'id': 'symbol', 'kind': 'image', 'editable': False, 'path': 'source.png',
               'sha256': sha256((self.root / 'source.png').read_bytes()).hexdigest(),
               'fit': 'stretch', 'rotation': 21, 'crop': {'left': .013, 'top': .007, 'right': 0, 'bottom': 0},
               'box': {'x': 9.375, 'y': -4.5, 'width': 32.125, 'height': 7.75}}
        return im, obj

    def changed(self, obj, edit):
        after, _ = derive_zero_alpha_rgb_image(obj, self.root, 'out.png')
        with Image.open(self.root / 'out.png') as im:
            im.load(); edit(im); im.save(self.root / 'bad.png')
        return {**after, 'path': 'bad.png', 'sha256': sha256((self.root / 'bad.png').read_bytes()).hexdigest()}

    def test_all_partial_alpha_and_arbitrary_background_pointwise_composition_preserved(self):
        original, obj = self.fixture(); before = deepcopy(obj); raw = (self.root / 'source.png').read_bytes()
        after, receipt = derive_zero_alpha_rgb_image(obj, self.root, 'derived/white.png')
        with Image.open(self.root / after['path']) as im:
            original_pixels, derived_pixels = original.tobytes(), im.tobytes()
            for i in range(0, len(original_pixels), 4):
                p, q = tuple(original_pixels[i:i+4]), tuple(derived_pixels[i:i+4])
                self.assertEqual(p[3], q[3])
                if p[3]: self.assertEqual(p, q)
                else: self.assertEqual(q[:3], (255, 255, 255))
                for bg in [(0, 0, 0), (255, 255, 255), (19, 71, 201)]:
                    self.assertEqual(tuple(c*p[3]+b*(255-p[3]) for c,b in zip(p[:3], bg)),
                                     tuple(c*q[3]+b*(255-q[3]) for c,b in zip(q[:3], bg)))
        self.assertEqual(obj, before); self.assertEqual((self.root / 'source.png').read_bytes(), raw)
        self.assertEqual(verify_zero_alpha_rgb_image(obj, after, self.root), receipt)
        self.assertFalse(receipt['RGB_alpha_filtering_or_PDF_error_bound_proved'])

    def test_density_grid_rotation_crop_and_other_properties_are_exact(self):
        _, obj = self.fixture(dpi=(120, 144)); after, _ = derive_zero_alpha_rgb_image(obj, self.root, 'out.png')
        self.assertEqual({k:v for k,v in after.items() if k not in ['path','sha256']},
                         {k:v for k,v in obj.items() if k not in ['path','sha256']})
        with Image.open(self.root/'source.png') as a, Image.open(self.root/'out.png') as b:
            self.assertEqual(a.size,b.size); self.assertEqual(a.info,b.info)

    def test_saved_positive_alpha_color_change_is_rejected_even_with_updated_hash(self):
        _, obj = self.fixture(); after = self.changed(obj,lambda im:im.putpixel((4,3),(1,2,3,128)))
        with self.assertRaisesRegex(ValueError,'positive-alpha'): verify_zero_alpha_rgb_image(obj,after,self.root)

    def test_saved_alpha_change_is_rejected_even_with_updated_hash(self):
        _, obj = self.fixture(); after=self.changed(obj,lambda im:im.putpixel((4,3),(199,34,93,127)))
        with self.assertRaisesRegex(ValueError,'changed alpha'): verify_zero_alpha_rgb_image(obj,after,self.root)

    def test_saved_nonwhite_hidden_RGB_is_rejected(self):
        _, obj=self.fixture(); after=self.changed(obj,lambda im:im.putpixel((0,0),(255,254,255,0)))
        with self.assertRaisesRegex(ValueError,'not white'): verify_zero_alpha_rgb_image(obj,after,self.root)

    def test_changed_object_geometry_and_numeric_type_are_rejected(self):
        _, obj=self.fixture(); after,_=derive_zero_alpha_rgb_image(obj,self.root,'out.png')
        for change in [{'rotation':22},{'box':{**after['box'],'width':32.126}},{'editable':0},{'crop':{**after['crop'],'right':.001}}]:
            with self.subTest(change=change),self.assertRaises(ValueError): verify_zero_alpha_rgb_image(obj,{**after,**change},self.root)

    def test_no_visible_support_or_no_changed_hidden_RGB_is_rejected(self):
        for rgba in [(1,2,3,0),(255,255,255,0),(1,2,3,255)]:
            _,obj=self.fixture();im=Image.new('RGBA',(9,7),rgba);im.save(self.root/'source.png');obj['sha256']=sha256((self.root/'source.png').read_bytes()).hexdigest()
            with self.subTest(rgba=rgba),self.assertRaises(ValueError): derive_zero_alpha_rgb_image(obj,self.root,'out.png')
            self.assertFalse((self.root/'out.png').exists())

    def test_profiles_hash_mismatches_and_external_paths_are_rejected(self):
        _,obj=self.fixture(icc_profile=b'unknown profile')
        with self.assertRaises(ValueError): derive_zero_alpha_rgb_image(obj,self.root,'out.png')
        _,obj=self.fixture()
        for modified,dest in [({**obj,'sha256':'0'*64},'out.png'),(obj,'../escape.png'),(obj,str(self.root/'absolute.png')),(obj,'out.jpg')]:
            with self.subTest(dest=dest),self.assertRaises(ValueError): derive_zero_alpha_rgb_image(modified,self.root,dest)
        self.assertFalse((self.root/'out.png').exists())

    def test_existing_source_and_destination_are_never_overwritten(self):
        _,obj=self.fixture();(self.root/'out.png').write_bytes(b'earlier candidate')
        for dest in ['source.png','out.png']:
            with self.assertRaises(ValueError): derive_zero_alpha_rgb_image(obj,self.root,dest)
        self.assertEqual((self.root/'out.png').read_bytes(),b'earlier candidate')

    def test_resource_guards_reject_before_decompression(self):
        _,obj=self.fixture()
        for key,value in [('MAX_PIXELS',62),('MAX_DIMENSION',8),('MAX_INPUT_BYTES',1)]:
            with patch('figure_rebuild.image_hidden_rgb.'+key,value),self.assertRaises(ValueError):
                derive_zero_alpha_rgb_image(obj,self.root,'out.png')
        for v in [True,float('inf'),10**400,0]:
            with self.subTest(v=v),self.assertRaises(ValueError):
                derive_zero_alpha_rgb_image({**obj,'box':{**obj['box'],'width':v}},self.root,'out.png')
