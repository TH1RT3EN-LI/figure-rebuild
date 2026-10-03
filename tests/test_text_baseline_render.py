"""Actual exported-PPT regression; opt in with FIGURE_REBUILD_RENDER_TESTS=1.

Uses the configured, caller-owned Artifact runtime and at least two real font
families. Set FIGURE_REBUILD_RENDER_EVIDENCE_ROOT to retain all jobs and logs.
Default test discovery skips it and does not load a presentation renderer.
"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


@unittest.skipUnless(os.environ.get('FIGURE_REBUILD_RENDER_TESTS') == '1',
                     'opt-in real Artifact Tool rendering regression')
class RenderedTextBaselineTests(unittest.TestCase):
    def test_source_baselines_survive_spacing_alignment_rotation_and_placement(self):
        from figure_rebuild.cli import runtime
        config = runtime()
        explicit = os.environ.get('FIGURE_REBUILD_RENDER_EVIDENCE_ROOT')
        if explicit:
            root = Path(explicit).resolve()
            root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='fr-baseline-render-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        (root / 'runtime.json').write_text(json.dumps(config))
        repo = Path(__file__).resolve().parents[1]

        def command(label, args):
            started = datetime.datetime.now(datetime.timezone.utc).isoformat()
            result = subprocess.run([str(x) for x in args], cwd=repo, text=True, capture_output=True)
            (root / (label + '.stdout.log')).write_text(result.stdout)
            (root / (label + '.stderr.log')).write_text(result.stderr)
            with (root / 'commands.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(label=label, command=[str(x) for x in args],
                    started_at=started, returncode=result.returncode)) + '\n')
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-3000:])
            return result.stdout

        cli = [sys.executable, '-m', 'figure_rebuild']
        command('fixture', [config['node'], repo / 'tests/fixtures/text_baseline_render.mjs',
                            root / 'runtime.json', root])
        fixture = json.loads((root / 'fixture.json').read_text())
        measurements = []
        for scale in (1, .637):
            label = str(scale)
            job = root / ('job-' + label)
            command('prepare-' + label, [*cli, 'prepare', '--input', root / 'reference-1-1x.png',
                '--job', job, '--kind', 'generated_diagram', '--id', 'baseline-regression'])
            manifest = job / 'manifest.json'
            scene = json.loads(manifest.read_text())
            scene['objects'] = fixture['objects']
            scene['recognition']['unresolved'] = []
            manifest.write_text(json.dumps(scene))
            command('review-' + label, [*cli, 'review', '--manifest', manifest,
                '--note', 'Known alphabetic-baseline synthetic fixture; review covers all generated text and geometry.'])
            args = [*cli, 'build', '--manifest', manifest, '--output', root / ('rendered-' + label + '.pptx')]
            offset = 0 if scale == 1 else 17
            if scale != 1:
                info = json.loads(command('inspect-base', [*cli, 'inspect-base', root / 'blank-base.pptx']))
                slide_id = info['slides'][0].get('slide_id', info['slides'][0].get('id'))
                args.extend(['--base', root / 'blank-base.pptx', '--slide-id', str(slide_id), '--placement',
                             str(offset), str(offset), str(fixture['canvas']['width'] * scale),
                             str(fixture['canvas']['height'] * scale)])
            command('build-' + label, args)
            run = sorted((job / 'build').glob('run-*'))[-1]
            failures = []
            with Image.open(root / f'reference-renderer-{label}-4x.png') as src, Image.open(run / 'preview-4x.png') as out:
                source, actual = src.convert('L'), out.convert('L')
                self.assertEqual(source.size, actual.size)
                for sample in fixture['samples']:
                    roi = tuple(round((v * scale + offset) * 4) for v in sample['roi'])
                    left = source.crop(roi).point(lambda value: 255 if value < 128 else 0).getbbox()
                    right = actual.crop(roi).point(lambda value: 255 if value < 128 else 0).getbbox()
                    self.assertIsNotNone(left, sample['id'])
                    self.assertIsNotNone(right, sample['id'])
                    delta = [(right[i] - left[i]) / 4 for i in range(4)]
                    measurements.append(dict(id=sample['id'], scale=scale, delta_physical_px=delta,
                                             rotation=sample['rotation'], lines=sample['lines']))
                    # 4x pixel sampling gives a 0.25 physical-slide-pixel bound.
                    # Check actual ink against independently drawn known source
                    # baselines, not the helper's own baseline algebra.
                    if max(abs(delta[1]), abs(delta[3])) > .25:
                        failures.append(f"{sample['id']} scale={scale}: {delta}")
            (root / 'measurements.json').write_text(json.dumps(measurements, indent=2))
            self.assertEqual(failures, [], '\n'.join(failures))


if __name__ == '__main__':
    unittest.main()
