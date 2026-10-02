"""Keep an inspectable vector master alongside the native PPT reconstruction."""
import argparse
import base64
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from PIL import Image, ImageFont

SVG='http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)

def text_lines(text, font, width=None):
    """Preserve literal line breaks and approximate box wrapping in the SVG aide."""
    lines=[]
    for paragraph in text.split('\n'):
        if width is None:
            lines.append(paragraph); continue
        current=''
        for token in re.findall(r'\S+\s*|\s+', paragraph):
            if current and font.getlength(current+token)>width:
                lines.append(current.rstrip());current=''
            if font.getlength(token.rstrip())>width:
                for char in token:
                    if current and font.getlength(current+char)>width:
                        lines.append(current.rstrip());current=''
                    current+=char
            else: current+=token
        lines.append(current.rstrip())
    return lines

def export(manifest_path, output, font_path, asset_root=None, bold_font_path=None, family=None):
    if not isinstance(family, str) or not family.strip():
        raise ValueError('An explicit configured font family is required')
    manifest_path=Path(manifest_path); m=json.loads(manifest_path.read_text())
    c=m['canvas']; root=ET.Element('{'+SVG+'}svg',{'width':str(c['width']),'height':str(c['height']),'viewBox':f"0 0 {c['width']} {c['height']}"})
    ET.SubElement(root,'{'+SVG+'}rect',{'id':'canvas-background','width':str(c['width']),'height':str(c['height']),'fill':c.get('background','#FFFFFF')})
    rows=sorted(enumerate(m['objects']),key=lambda row:(row[1].get('z_index',row[0]),row[0]))
    for _,o in rows:
        s=o.get('style',{});attrs={'id':o['id'],'opacity':str(s.get('opacity',1))}
        if o.get('group_id'): attrs['data-group-id']=o['group_id']
        if o['kind']=='path':
            parts=[]
            for command in o['commands']:
                if 'close' in command: parts.append('Z')
                else:
                    key='moveTo' if 'moveTo' in command else 'lineTo'; p=command[key]
                    parts.append(('M' if key=='moveTo' else 'L')+f" {p['x']} {p['y']}")
            attrs.update(d=' '.join(parts),fill=s.get('fill','none'),stroke=s.get('stroke','none'))
            attrs['stroke-width']=str(s.get('stroke_width',0)); ET.SubElement(root,'{'+SVG+'}path',attrs)
        elif o['kind']=='text':
            size=o['font_size'];face=bold_font_path if o.get('bold') and bold_font_path else font_path
            font=ImageFont.truetype(str(face),max(1,round(size)));ascent,descent=font.getmetrics()
            lines=text_lines(o['text'],font,o['box']['width'] if o.get('wrap')=='square' and 'box' in o else None)
            line_height=size*1.2;block_height=ascent+descent+(len(lines)-1)*line_height
            if 'anchor' in o: x,y=o['anchor']['x'],o['anchor']['y']
            else:
                b=o['box'];align=o.get('alignment','left');x=b['x']+(b['width']/2 if align=='center' else b['width'] if align=='right' else 0)
                va=o.get('vertical_alignment','top');offset=(b['height']-block_height)/2 if va=='middle' else b['height']-block_height if va=='bottom' else 0
                y=b['y']+offset+ascent
            attrs.update(x=str(x),y=str(y),fill=s.get('fill','#000000'))
            attrs['font-family']=family;attrs['font-size']=str(size);attrs['font-weight']='bold' if o.get('bold') else 'normal';attrs['font-style']='italic' if o.get('italic') else 'normal';attrs['text-anchor']={'left':'start','center':'middle','right':'end'}[o.get('alignment','left')]
            if o.get('rotation'):
                b=o['box'];attrs['transform']=f"rotate({o['rotation']} {b['x']+b['width']/2} {b['y']+b['height']/2})"
            node=ET.SubElement(root,'{'+SVG+'}text',attrs)
            node.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            if len(lines)==1:node.text=lines[0]
            else:
                for index,line in enumerate(lines):
                    ET.SubElement(node,'{'+SVG+'}tspan',{'x':str(x),'y':str(y+index*line_height)}).text=line
        elif o['kind']=='image':
            file=Path(asset_root or manifest_path.parent)/o['path'];b=o['box'];ext=file.suffix.lower()[1:];ext='jpeg' if ext=='jpg' else ext
            uri=f"data:image/{ext};base64,"+base64.b64encode(file.read_bytes()).decode()
            with Image.open(file) as image: iw,ih=image.size
            crop=o.get('crop',{'left':0,'top':0,'right':0,'bottom':0})
            attrs.update(x=str(b['x']),y=str(b['y']),width=str(b['width']),height=str(b['height']),viewBox=f"{iw*crop['left']} {ih*crop['top']} {iw*(1-crop['left']-crop['right'])} {ih*(1-crop['top']-crop['bottom'])}",preserveAspectRatio='xMidYMid meet')
            panel=ET.SubElement(root,'{'+SVG+'}svg',attrs)
            ET.SubElement(panel,'{'+SVG+'}image',{'width':str(iw),'height':str(ih),'href':uri})
    ET.ElementTree(root).write(output,encoding='utf-8',xml_declaration=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);p.add_argument('--font',required=True);p.add_argument('--bold-font');p.add_argument('--asset-root');p.add_argument('--family',required=True);a=p.parse_args();export(a.manifest,a.output,a.font,a.asset_root,a.bold_font,a.family)
