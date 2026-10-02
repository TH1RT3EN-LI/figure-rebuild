"""Alpha-aware source/output comparison; pixel error is diagnostic, not acceptance."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

def white(image):
    image = image.convert('RGBA')
    background = Image.new('RGBA', image.size, 'white'); background.alpha_composite(image)
    return background.convert('RGB')

def compare(reference, rebuilt, output, metrics, font_path, region=None):
    source, target = white(Image.open(reference)), white(Image.open(rebuilt))
    if region:
        x, y, w, h = region
        target = target.crop((round(x), round(y), round(x+w), round(y+h)))
    same_canvas = source.size == target.size
    source = source.resize(target.size, Image.Resampling.LANCZOS)
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
    report = {'same_canvas_size': same_canvas, 'rendered_size': list(target.size), 'mean_absolute_rgb_error': sum(stat.mean)/3, 'pixel_error_is_diagnostic_only': True, 'visual_acceptance': 'pending', 'note': 'Source-font adaptation, antialiasing and curve sampling can contribute differences; check text and relations visually.'}
    Path(metrics).write_text(json.dumps(report, indent=2)+'\n')
    return report

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--reference',required=True);p.add_argument('--rebuilt',required=True);p.add_argument('--output',required=True);p.add_argument('--metrics',required=True);p.add_argument('--font',required=True);p.add_argument('--region',type=float,nargs=4)
    a=p.parse_args(); print(json.dumps(compare(a.reference,a.rebuilt,a.output,a.metrics,a.font,a.region)))
