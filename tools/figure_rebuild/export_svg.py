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

def export(manifest_path, output, font_path, asset_root=None, bold_font_path=None, family=None, font_audit=None):
    if not isinstance(family, str) or not family.strip():
        raise ValueError('An explicit configured font family is required')
    manifest_path=Path(manifest_path); m=json.loads(manifest_path.read_text())
    audit = json.loads(Path(font_audit).read_text()) if isinstance(font_audit, (str, Path)) else font_audit
    if audit is not None and not isinstance(audit, list):
        raise ValueError('Font audit must contain an array of configured faces')
    faces = {}
    for face in audit or []:
        if not isinstance(face, dict) or face.get('role') not in ('regular', 'bold', 'italic', 'boldItalic') or not isinstance(face.get('renderer'), str):
            raise ValueError('Invalid configured font audit face')
        face_family = face.get('family', family)
        if not isinstance(face_family, str) or not face_family.strip():
            raise ValueError('Invalid configured font audit family')
        key = (face_family, face['role'])
        if key in faces:
            raise ValueError('Duplicate configured font audit face')
        faces[key] = face['renderer']
    def selected_face(obj):
        requested = obj.get('font_family', family)
        role = ('boldItalic' if obj.get('bold') else 'italic') if obj.get('italic') else ('bold' if obj.get('bold') else 'regular')
        if obj.get('italic') and not faces:
            raise ValueError('True italic requires an explicit audited font face')
        if requested == family and not faces:
            return requested, bold_font_path if obj.get('bold') and bold_font_path else font_path
        if (requested, role) not in faces:
            raise ValueError('No configured SVG font face for ' + requested + '/' + role)
        return requested, faces[(requested, role)]
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
                elif 'cubicTo' in command:
                    p=command['cubicTo'];parts.append(f"C {p['x1']} {p['y1']} {p['x2']} {p['y2']} {p['x']} {p['y']}")
                else:
                    key='moveTo' if 'moveTo' in command else 'lineTo'; p=command[key]
                    parts.append(('M' if key=='moveTo' else 'L')+f" {p['x']} {p['y']}")
            attrs.update(d=' '.join(parts),fill=s.get('fill','none'),stroke=s.get('stroke','none'))
            attrs['stroke-width']=str(s.get('stroke_width',0)); ET.SubElement(root,'{'+SVG+'}path',attrs)
        elif o['kind']=='text':
            size=o['font_size'];text_family,face=selected_face(o)
            font=ImageFont.truetype(str(face),max(1,round(size)));ascent,descent=font.getmetrics()
            inset={**dict(left=0,right=0,top=0,bottom=0),**o.get('insets',{})}
            lines=text_lines(o['text'],font,o['box']['width']-inset['left']-inset['right'] if o.get('wrap')=='square' and 'box' in o else None)
            line_height=o.get('line_height',size*1.2);block_height=ascent+descent+(len(lines)-1)*line_height
            if 'anchor' in o: x,y=o['anchor']['x'],o['anchor']['y']
            else:
                b=o['box'];align=o.get('alignment','left');w=b['width']-inset['left']-inset['right'];h=b['height']-inset['top']-inset['bottom']
                x=b['x']+inset['left']+(w/2 if align=='center' else w if align=='right' else 0)
                va=o.get('vertical_alignment','top');offset=(h-block_height)/2 if va=='middle' else h-block_height if va=='bottom' else 0
                y=b['y']+inset['top']+offset+o.get('baseline_offset',ascent)
            attrs.update(x=str(x),y=str(y),fill=s.get('fill','#000000'))
            attrs['font-family']=text_family;attrs['font-size']=str(size);attrs['font-weight']='bold' if o.get('bold') else 'normal';attrs['font-style']='italic' if o.get('italic') else 'normal';attrs['text-anchor']={'left':'start','center':'middle','right':'end'}[o.get('alignment','left')]
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
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);p.add_argument('--font',required=True);p.add_argument('--bold-font');p.add_argument('--asset-root');p.add_argument('--family',required=True);p.add_argument('--font-audit');a=p.parse_args();export(a.manifest,a.output,a.font,a.asset_root,a.bold_font,a.family,a.font_audit)
