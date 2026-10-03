"""Generate-time placement freezes the selected deck and region before authoring."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_cli
from test_package import fixture, shape, slide, write_archive
from figure_rebuild import cli


class BuildPlacementTests(unittest.TestCase):
    def setUp(self):
        test_cli.BuildInputChecks.setUp(self)
        self.base = self.job / 'template.pptx'
        write_archive(self.base, fixture([
            (257, 'ppt/slides/untouched.xml', slide([shape(2, 'other-slide')])),
            (900, 'ppt/slides/target.xml', slide([shape(2, 'title')]))
        ], size=(1200 * 9525, 800 * 9525)))
        self.base_bytes = self.base.read_bytes()
        self.args.base = str(self.base)
        self.args.slide_id = '900'
        self.args.placement = [100, 100, 400, 400]
        self.args.base_sha256 = cli.digest(self.base)

    def tearDown(self):
        test_cli.BuildInputChecks.tearDown(self)

    def build_without_authoring(self):
        test_cli.BuildInputChecks.build_without_authoring(self)

    def test_selected_slide_and_placement_are_bound_to_a_base_snapshot(self):
        self.build_without_authoring()
        config = json.loads((self.job / 'build/run-001/build-config.json').read_text())
        self.assertEqual(config['base']['slide_id'], '900')
        self.assertEqual(config['base']['placement'], [100, 100, 400, 400])
        self.assertEqual(config['base']['canvas'], {'width': 1200, 'height': 800})
        self.assertEqual(config['base']['placement_audit'], {
            'scale': 2, 'requested': [100, 100, 400, 400],
            'placement': [100, 200, 400, 200]})
        self.assertEqual(config['base']['sha256'], cli.digest(self.base))
        self.assertNotEqual(Path(config['base']['path']), self.base)
        self.assertEqual(Path(config['base']['path']).read_bytes(), self.base_bytes)
        self.assertEqual(self.base.read_bytes(), self.base_bytes)

    def test_bad_region_or_identity_fails_before_authoring_or_run_allocation(self):
        invalid = [
            ('placement', [100, 100, 0, 400], 'positive'),
            ('placement', [100, 100, float('nan'), 400], 'finite'),
            ('placement', [100, 100, float('inf'), 400], 'finite'),
            ('placement', [1100, 100, 400, 400], 'outside'),
            ('placement', [-1, 100, 400, 400], 'outside'),
            ('placement', None, 'explicit placement'),
            ('slide_id', '2', 'absent'),
            ('slide_id', None, 'stable native'),
            ('base_sha256', '0' * 64, 'checksum'),
        ]
        for attribute, value, message in invalid:
            original = getattr(self.args, attribute)
            with self.subTest(attribute=attribute, value=value):
                setattr(self.args, attribute, value)
                with patch.object(cli, 'runtime', return_value=self.rt), patch.object(cli.subprocess, 'run') as run:
                    with self.assertRaisesRegex(ValueError, message):
                        cli.build(self.args)
                    run.assert_not_called()
                    self.assertFalse((self.job / 'build').exists())
            setattr(self.args, attribute, original)

    def test_replacement_names_reach_the_frozen_build_configuration(self):
        self.args.replace_id = ['title']
        self.build_without_authoring()
        config = json.loads((self.job / 'build/run-001/build-config.json').read_text())
        self.assertEqual(config['base']['replace_ids'], ['title'])

    def test_base_change_during_snapshot_fails_before_authoring(self):
        original_copy = cli.shutil.copy2
        def copy_with_changed_base(source, target):
            if Path(source) == self.base:
                Path(target).write_bytes(b'base changed during snapshot')
            else:
                original_copy(source, target)
        with patch.object(cli, 'runtime', return_value=self.rt), patch.object(cli.shutil, 'copy2', side_effect=copy_with_changed_base), patch.object(cli.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Base changed during snapshot'):
                cli.build(self.args)
            run.assert_not_called()
