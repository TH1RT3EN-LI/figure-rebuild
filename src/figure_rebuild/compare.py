"""Alpha-aware source/output comparison; pixel error is diagnostic, not acceptance."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
from .registration import diagnose_registration, manifest_regions

def white(image):
    image = image.convert('RGBA')
    background = Image.new('RGBA', image.size, 'white'); background.alpha_composite(image)
    return background.convert('RGB')

def compare(reference, rebuilt, output, metrics, font_path, region=None, manifest=None, *, diagnostic_options=None):
    """Compare without registration; optional bounded limits tune local coverage.

    diagnostic_options is passed to diagnose_registration (max_regions,
    max_roi_pixels, max_analysis_side, source_grid_cell_size, max_shift_px).
    It changes diagnostic sampling only, never raw RGB error or the preview.
    """
    source, target = white(Image.open(reference)), white(Image.open(rebuilt))
    if region:
        x, y, w, h = region
        target = target.crop((round(x), round(y), round(x+w), round(y+h)))
    same_canvas = source.size == target.size
    source = source.resize(target.size, Image.Resampling.LANCZOS)
    scene = json.loads(Path(manifest).read_text()) if isinstance(manifest, (str, Path)) else manifest
    regions = manifest_regions(scene, target.size) if scene is not None else None
    geometry = diagnose_registration(source, target, regions=regions, **(diagnostic_options or {}))
    difference = ImageChops.difference(source, target)
    stat = ImageStat.Stat(difference)
    width = min(900, target.width); height = round(target.height * width / target.width)
    gutter, header = 20, 48
    sheet = Image.new('RGB', (width*3+gutter*2, height+header), 'white')
    draw = ImageDraw.Draw(sheet); font = ImageFont.truetype(str(font_path), 22)
    for i, (label, im) in enumerate([('Reference', source), ('Editable PPT render', target), ('Pixel difference', difference)]):
        x = i*(width+gutter)
        draw.text((x+width/2, 9), label, font=font, fill='#333333', anchor='mt')
        sheet.paste(im.resize((width, height), Image.Resampling.LANCZOS), (x, header))
    sheet.save(output)
    report = {'same_canvas_size': same_canvas, 'rendered_size': list(target.size), 'mean_absolute_rgb_error': sum(stat.mean)/3, 'pixel_error_is_diagnostic_only': True, 'geometry': geometry, 'visual_acceptance': 'pending', 'note': 'Source-font adaptation, antialiasing and curve sampling can contribute differences; check text and relations visually. Geometry diagnosis never moves or warps the output; raw and local edge errors remain unaligned. Source-grid coverage is independent of output object boxes. Complete coverage means sampled diagnostics at the reported analysis resolution, not visual acceptance; inspect all recorded omissions.'}
    Path(metrics).write_text(json.dumps(report, indent=2)+'\n')
    return report

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--reference',required=True);p.add_argument('--rebuilt',required=True);p.add_argument('--output',required=True);p.add_argument('--metrics',required=True);p.add_argument('--font',required=True);p.add_argument('--region',type=float,nargs=4);p.add_argument('--manifest')
    p.add_argument('--max-regions', type=int, help='Object ROI limit (0 for source-grid only; maximum 16384).')
    p.add_argument('--max-roi-pixels', type=int, help='Combined source-grid/object core+context pixel budget (maximum 256000000).')
    p.add_argument('--max-analysis-side', type=int, help='Longest analysis side (8..4096); does not change raw RGB comparison.')
    p.add_argument('--source-grid-cell-size', type=int, help='Source-grid cell side in analysis pixels (64..1024).')
    a=p.parse_args()
    options={key:getattr(a,key) for key in ('max_regions','max_roi_pixels','max_analysis_side','source_grid_cell_size') if getattr(a,key) is not None}
    print(json.dumps(compare(a.reference,a.rebuilt,a.output,a.metrics,a.font,a.region,a.manifest,diagnostic_options=options)))
