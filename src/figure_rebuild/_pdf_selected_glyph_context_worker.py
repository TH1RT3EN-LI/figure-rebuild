"""Internal isolated replay worker for selected native glyph context facts.

Run by pdf_selected_glyph_context using this installed package file under -I.
"""
from pathlib import Path
import base64
from contextlib import ExitStack
import hashlib
import json
import math
import os
import re
import resource
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from figure_rebuild.pdf_selected_glyph_context import (
    SelectedGlyphContextLimits as Limits, SelectedGlyphContextError as SelectedContextError,
    SCHEMA, GROUP_CLAIM, _limits,
)

sha = lambda data: hashlib.sha256(data).hexdigest()


class Budget:
    def __init__(self, limits):
        self.limits, self.used = limits, {}

    def take(self, kind, count=1):
        value = self.used.get(kind, 0) + count
        if value > getattr(self.limits, "max_" + kind):
            raise SelectedContextError("budget_" + kind)
        self.used[kind] = value
        if kind != "operations":
            self.take("operations", count)

    def number(self, value):
        self.take("operations")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise SelectedContextError("nonfinite_native_number")
        if abs(value) >= 2 ** self.limits.max_integer_bits:
            raise SelectedContextError("numeric_integer_bits")
        return float(value)

    def integer(self, value):
        self.take("operations")
        if type(value) is not int or value.bit_length() > self.limits.max_integer_bits:
            raise SelectedContextError("native_integer_bits")
        return value

    def matrix(self, matrix):
        return [self.number(getattr(matrix, k)) for k in "abcdef"]

    def rectangle(self, rectangle):
        return [self.number(getattr(rectangle, k)) for k in ("x0", "y0", "x1", "y1")]


def capture_native(pdf, page_number, budget):
    try:
        import pymupdf as F
    except ImportError as error:
        raise SelectedContextError("source_dependency_unavailable") from error
    M = F.mupdf
    cookie = M.FzCookie()
    with F.open(stream=pdf, filetype="pdf") as document, ExitStack() as font_lifetime:
        if document.is_repaired:
            raise SelectedContextError("repaired_source_pdf")
        budget.take("pdf_objects", document.xref_length())
        if page_number > len(document):
            raise SelectedContextError("page_out_of_range")
        page = document[page_number - 1]
        if page.rotation:
            raise SelectedContextError("rotated_page")
        page_rect = budget.rectangle(page.rect)
        # Page groups can be normalized by the interpreter (notably /K true).
        # Require the actual source declaration as well as neutral callbacks.
        group_kind, group_value = document.xref_get_key(page.xref, "Group")
        source_group = None
        if group_kind != "null":
            if group_kind != "xref" or not re.fullmatch(r"[1-9][0-9]* 0 R", group_value):
                raise SelectedContextError("source_group_dictionary_unsupported")
            group_xref = int(group_value.split()[0])
            budget.integer(group_xref)
            keys = set(document.xref_get_keys(group_xref))
            if not keys <= {"Type", "S", "CS", "I", "K"}:
                raise SelectedContextError("unknown_source_group_key")
            props = {key: document.xref_get_key(group_xref, key) for key in ("Type", "S", "CS", "I", "K")}
            if (props["Type"] not in (("null", "null"), ("name", "/Group")) or
                    props["S"] != ("name", "/Transparency") or
                    props["CS"] != ("name", "/DeviceRGB") or
                    props["I"] != ("bool", "true") or
                    props["K"] not in (("null", "null"), ("bool", "false"))):
                raise SelectedContextError("nonneutral_source_group_declaration")
            source_group = dict(xref=group_xref, properties=props)
        # Match actual page-resource font handles to original FontFile streams.
        # A populated MuPDF font.buffer is insufficient: Base14 uses built-ins.
        source_fonts = {}
        pdf_document = M.pdf_document_from_fz_document(document.this)
        font_rows = page.get_fonts(full=True)
        budget.take("font_resources", len(font_rows))
        for row in font_rows:
            font_xref = budget.integer(row[0])
            if font_xref <= 0:
                raise SelectedContextError("font_source_xref_unsupported")
            descendant_xref = 0
            subtype = document.xref_get_key(font_xref, "Subtype")
            if subtype == ("name", "/Type0"):
                kind, value = document.xref_get_key(font_xref, "DescendantFonts")
                match = re.fullmatch(r"\[\s*([1-9][0-9]*) 0 R\s*\]", value) if kind == "array" else None
                if not match:
                    raise SelectedContextError("font_descendants_unsupported")
                descendant_xref = budget.integer(int(match.group(1)))
            elif subtype not in (("name", "/Type1"), ("name", "/TrueType")):
                raise SelectedContextError("font_source_subtype_unsupported")
            def required_xref(owner, key):
                kind, value = document.xref_get_key(owner, key)
                if kind != "xref" or not re.fullmatch(r"[1-9][0-9]* 0 R", value):
                    raise SelectedContextError("font_not_source_embedded")
                return budget.integer(int(value.split()[0]))
            descriptor_xref = required_xref(descendant_xref or font_xref, "FontDescriptor")
            keys = [key for key in ("FontFile", "FontFile2", "FontFile3")
                    if document.xref_get_key(descriptor_xref, key)[0] != "null"]
            if len(keys) != 1:
                raise SelectedContextError("font_not_source_embedded")
            stream_xref = required_xref(descriptor_xref, keys[0])
            if not document.xref_is_stream(stream_xref):
                raise SelectedContextError("font_source_stream_unsupported")
            program = document.xref_stream(stream_xref)
            if not program or len(program) > budget.limits.max_font_bytes:
                raise SelectedContextError("budget_font_bytes")
            budget.take("operations", 1 + len(program) // 256)
            font_object = M.pdf_new_indirect(pdf_document, font_xref, 0)
            descriptor = M.ll_pdf_load_font(pdf_document.m_internal, None, font_object.m_internal)
            font_lifetime.callback(M.ll_pdf_drop_font, descriptor)
            if not descriptor or not descriptor.font or not descriptor.font.buffer:
                raise SelectedContextError("font_program_unsupported")
            raw = descriptor.font
            if raw.buffer.len != len(program):
                raise SelectedContextError("font_source_program_mismatch")
            native_program = bytes(M.ll_fz_buffer_storage_memoryview(raw.buffer, False))
            if native_program != program:
                raise SelectedContextError("font_source_program_mismatch")
            pointer = int(raw.this)
            binding = dict(font_xref=font_xref, descendant_xref=descendant_xref,
                           descriptor_xref=descriptor_xref, stream_xref=stream_xref,
                           stream_key=keys[0])
            item = source_fonts.setdefault(pointer, dict(program_sha256=sha(program),
                                                        program_bytes=len(program), source_embedding=[]))
            if item["program_sha256"] != sha(program):
                raise SelectedContextError("font_source_pointer_mismatch")
            if binding not in item["source_embedding"]:
                item["source_embedding"].append(binding)
        canonical = {n: int(cs.this.m_internal.this) for n, cs in
                     (("gray", F.csGRAY), ("rgb", F.csRGB), ("cmyk", F.csCMYK))}

        class Walker(M.FzPathWalker2):
            def __init__(self):
                super().__init__()
                self.commands = []
                for name in ("moveto", "lineto", "curveto", "closepath"):
                    getattr(self, "use_virtual_" + name)()

            def append(self, kind, *values):
                budget.take("controls", max(1, len(values) // 2))
                numbers = [budget.number(v) for v in values]
                self.commands.append([kind] + [numbers[i:i + 2] for i in range(0, len(numbers), 2)])

            def moveto(self, arg, x, y): self.append("M", x, y)
            def lineto(self, arg, x, y): self.append("L", x, y)
            def curveto(self, arg, *values): self.append("C", *values)
            def closepath(self, arg): self.append("Z")

        class Device(M.FzDevice2):
            def __init__(self):
                super().__init__()
                self.error = None
                self.paints, self.glyphs, self.groups, self.events = [], [], [], []
                self.group_stack, self.clip_stack = [], []
                self.fonts, self.outlines = {}, {}
                self.defaults_seen = False
                for name in callback_names:
                    getattr(self, "use_virtual_" + name)()

            def depth(self):
                if len(self.group_stack) + len(self.clip_stack) > budget.limits.max_context_depth:
                    raise SelectedContextError("budget_context_depth")

            def event(self, name, **values):
                budget.take("context_events")
                self.events.append(dict(kind=name, paint_seqno=len(self.paints), **values))

            def set_default_colorspaces(self, ctx, spaces):
                self.event("set_default_colorspaces")
                if self.defaults_seen or self.paints:
                    raise SelectedContextError("changed_default_colorspaces")
                self.defaults_seen = True
                for name, pointer in canonical.items():
                    cs = getattr(M, "ll_fz_default_" + name)(spaces)
                    if not cs or int(cs.this) != pointer:
                        raise SelectedContextError("noncanonical_default_colorspace")
                if M.ll_fz_default_output_intent(spaces):
                    raise SelectedContextError("output_intent_unsupported")

            def begin_group(self, ctx, area, cs, isolated, knockout, blend, alpha):
                self.event("begin_group")
                bounds = budget.rectangle(area)
                opacity = budget.number(alpha)
                if self.group_stack or self.groups or self.clip_stack or self.paints or source_group is None:
                    raise SelectedContextError("child_or_nonpage_group")
                if (bounds != page_rect or not cs or int(cs.this) != canonical["rgb"] or
                        isolated != 1 or knockout != 0 or blend != M.FZ_BLEND_NORMAL or opacity != 1):
                    raise SelectedContextError("nonneutral_or_unknown_group")
                self.groups.append(dict(group_id=1, bbox=bounds, colorspace="DeviceRGB",
                                        isolated=True, knockout=False, blendmode=0, alpha=1.0,
                                        begin_paint_seqno=len(self.paints)))
                self.group_stack.append(1)
                self.depth()

            def end_group(self, ctx):
                self.event("end_group")
                if self.group_stack != [1] or self.clip_stack:
                    raise SelectedContextError("unbalanced_group")
                self.groups[0]["end_paint_seqno"] = len(self.paints)
                self.group_stack.pop()

            def clip_image_mask(self, ctx, image, matrix, scissor):
                self.event("clip_image_mask")
                if self.clip_stack:
                    raise SelectedContextError("nested_image_mask")
                self.clip_stack.append(dict(mask_pointer=int(image.this), owner=None,
                                            matrix=budget.matrix(matrix), scissor=budget.rectangle(scissor)))
                self.depth()

            def pop_clip(self, ctx):
                self.event("pop_clip")
                if len(self.clip_stack) != 1 or self.clip_stack[0]["owner"] is None:
                    raise SelectedContextError("unowned_or_unbalanced_clip")
                self.clip_stack.pop()

            def font(self, font):
                raw = font.m_internal
                pointer = int(raw.this)
                if pointer not in self.fonts:
                    budget.take("fonts")
                    if pointer not in source_fonts:
                        raise SelectedContextError("native_font_not_source_embedded")
                    if raw.t3procs or not raw.buffer or raw.flags.fake_bold or raw.flags.fake_italic or raw.flags.ft_substitute:
                        raise SelectedContextError("font_program_unsupported")
                    if raw.buffer.len > budget.limits.max_font_bytes:
                        raise SelectedContextError("budget_font_bytes")
                    data = bytes(M.ll_fz_buffer_storage_memoryview(raw.buffer, False))
                    budget.take("operations", 1 + len(data) // 256)
                    source = source_fonts[pointer]
                    if len(data) != source["program_bytes"] or sha(data) != source["program_sha256"]:
                        raise SelectedContextError("native_font_source_program_mismatch")
                    self.fonts[pointer] = dict(font_index=len(self.fonts), name=M.fz_font_name(font),
                                              source_embedding=source["source_embedding"],
                                              program_sha256=sha(data), program_bytes=len(data),
                                              glyph_count=budget.integer(raw.glyph_count))
                return self.fonts[pointer]

            def outline(self, font, gid):
                index = self.font(font)["font_index"]
                key = str(index) + ":" + str(gid)
                if key not in self.outlines:
                    budget.take("resources")
                    path = M.fz_outline_glyph(font, gid, M.FzMatrix())
                    walker = Walker()
                    if path and path.m_internal:
                        M.fz_walk_path(path, walker, walker.m_internal)
                    self.outlines[key] = walker.commands
                return key

            def paint(self, name, args):
                budget.take("paints")
                seq = len(self.paints)
                if self.groups and not self.group_stack:
                    raise SelectedContextError("paint_after_page_group")
                record = dict(kind=name, seqno=seq, group_ids=self.group_stack[:], clip_ids=[])
                if name == "fill_image":
                    image, matrix, alpha, params = args
                    if budget.number(alpha) != 1 or params.op or params.opm:
                        raise SelectedContextError("image_paint_effect")
                    for value in (image.w, image.h, image.n, image.bpc):
                        budget.integer(value)
                    record.update(matrix=budget.matrix(matrix), has_native_mask=bool(image.mask),
                                  image_reconstruction_proved=False)
                    if image.mask:
                        if len(self.clip_stack) != 1 or self.clip_stack[0]["mask_pointer"] != int(image.mask.this) or self.clip_stack[0]["owner"] is not None:
                            raise SelectedContextError("image_mask_ownership")
                        self.clip_stack[0]["owner"] = seq
                        record["clip_ids"] = [seq]
                    elif self.clip_stack:
                        raise SelectedContextError("unmatched_image_clip")
                elif name == "fill_text":
                    if self.clip_stack:
                        raise SelectedContextError("text_has_active_clip_or_mask")
                    text, matrix, cs, color, alpha, params = args
                    transform = budget.matrix(matrix)
                    if not cs or int(cs.this) != canonical["gray"] or budget.number(alpha) != 1 or params.op or params.opm:
                        raise SelectedContextError("text_paint_effect")
                    converted = M.ll_fz_convert_color(cs, color, cs, None, params)
                    if budget.number(converted[0]) != 0:
                        raise SelectedContextError("nonblack_text")
                    record.update(matrix=transform, color=[0, 0, 0], alpha=1.0)
                    span, span_index = text.head, 0
                    while span:
                        if span.len < 1 or span.len > budget.limits.max_glyphs - budget.used.get("glyphs", 0):
                            raise SelectedContextError("budget_glyphs")
                        budget.integer(span_index)
                        wrapped = M.FzTextSpan(span)
                        font = wrapped.font()
                        info = self.font(font)
                        if span.wmode != 0:
                            raise SelectedContextError("vertical_text_unsupported")
                        budget.matrix(wrapped.trm())
                        for index in range(span.len):
                            budget.take("glyphs")
                            item = wrapped.items(index)
                            gid, ucs = budget.integer(item.gid), budget.integer(item.ucs)
                            if not 0 <= gid < info["glyph_count"] or not 0 <= ucs <= 0x10ffff or 0xd800 <= ucs <= 0xdfff:
                                raise SelectedContextError("unmapped_glyph")
                            x, y = budget.number(item.x), budget.number(item.y)
                            trm = wrapped.trm()
                            trm.e, trm.f = x, y
                            composed = budget.matrix(M.fz_concat(trm, M.FzMatrix(matrix)))
                            key = self.outline(font, gid)
                            self.glyphs.append(dict(seqno=seq, span=span_index, item=index,
                                                   font_index=info["font_index"], gid=gid, ucs=ucs,
                                                   matrix=composed, outline_key=key,
                                                   group_ids=self.group_stack[:], clip_ids=[]))
                        span, span_index = span.next, span_index + 1
                else:
                    raise SelectedContextError("unknown_native_paint_" + name)
                self.paints.append(record)

        callback_names = [n[12:] for n in dir(M.FzDevice2) if n.startswith("use_virtual_") and n not in ("use_virtual_close_device", "use_virtual_drop_device")]
        known = {"set_default_colorspaces", "begin_group", "end_group", "clip_image_mask", "pop_clip"}
        for name in callback_names:
            original = getattr(Device, name, None) if name in known else None
            def guarded(self, ctx, *args, _name=name, _original=original):
                if self.error:
                    return 0 if _name == "begin_tile" else None
                try:
                    if _original:
                        return _original(self, ctx, *args)
                    if _name in ("fill_text", "fill_image"):
                        return self.paint(_name, args)
                    budget.take("context_events")
                    raise SelectedContextError("unknown_native_callback_" + _name)
                except Exception as error:
                    self.error = error.code if isinstance(error, SelectedContextError) else "native_callback_failure"
                    cookie.set_abort()
                    return 0 if _name == "begin_tile" else None
            setattr(Device, name, guarded)
        device = Device()
        try:
            M.fz_run_page(page.this, device, M.FzMatrix(), cookie)
            M.fz_close_device(device)
        except Exception as error:
            raise SelectedContextError(device.error or "native_replay_failure") from error
        if device.error:
            raise SelectedContextError(device.error)
        if document.is_repaired:
            raise SelectedContextError("repaired_source_pdf")
        if cookie.errors() or cookie.incomplete() or cookie.abort():
            raise SelectedContextError("native_replay_not_clean")
        if device.group_stack or device.clip_stack:
            raise SelectedContextError("unbalanced_final_context")
        if bool(source_group) != bool(device.groups):
            raise SelectedContextError("source_group_callback_mismatch")
        if not device.glyphs or not device.defaults_seen:
            raise SelectedContextError("incomplete_native_identity")
        return dict(paints=device.paints, glyphs=device.glyphs, fonts=list(device.fonts.values()),
                    outlines=device.outlines, groups=device.groups, events=device.events,
                    page_rect=page_rect, source_group=source_group, pymupdf=F.VersionBind,
                    replay=dict(errors=0, incomplete=0, aborted=False))


NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
TOKEN = re.compile("[A-Za-z]|" + NUMBER)


def number32(text, budget):
    if len(text) > budget.limits.max_numeric_lexeme_bytes:
        raise SelectedContextError("numeric_lexeme_budget")
    budget.take("operations")
    try:
        value = budget.number(float(text))
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except (ValueError, OverflowError, struct.error) as error:
        raise SelectedContextError("invalid_svg_number") from error


def path32(data, budget):
    tokens, end = [], 0
    for match in TOKEN.finditer(data):
        if data[end:match.start()].strip(" ,\t\r\n"):
            raise SelectedContextError("svg_path_syntax")
        tokens.append(match.group())
        budget.take("operations")
        end = match.end()
    if data[end:].strip(" ,\t\r\n"):
        raise SelectedContextError("svg_path_syntax")
    result, i, op, current, start = [], 0, None, (0., 0.), None
    while i < len(tokens):
        if tokens[i].isalpha():
            op, i = tokens[i], i + 1
        if op not in ("M", "L", "H", "V", "C", "Z"):
            raise SelectedContextError("svg_path_operator")
        if op == "Z":
            if start is None:
                raise SelectedContextError("svg_path_close")
            result.append(["Z"])
            current, op = start, None
            continue
        size = dict(M=2, L=2, H=1, V=1, C=6)[op]
        if i + size > len(tokens) or any(not re.fullmatch(NUMBER, s) for s in tokens[i:i + size]):
            raise SelectedContextError("svg_path_arity")
        values = [number32(s, budget) for s in tokens[i:i + size]]
        i += size
        if op == "H":
            current = values[0], current[1]
            result.append(["L", list(current)])
        elif op == "V":
            current = current[0], values[0]
            result.append(["L", list(current)])
        elif op == "C":
            current = tuple(values[4:])
            result.append(["C", values[:2], values[2:4], values[4:]])
        else:
            current = tuple(values)
            result.append([op, values])
            if op == "M":
                start, op = current, "L"
    return result


def bind_svg(native, svg, selected, budget):
    from xml.etree import ElementTree as ET
    if b"<!DOCTYPE" in svg.upper() or b"<!ENTITY" in svg.upper():
        raise SelectedContextError("svg_entity_unsupported")
    root = ET.fromstring(svg)
    if set(root.attrib) != {"version", "width", "height", "viewBox"} or root.get("version") != "1.1":
        raise SelectedContextError("svg_root_effect_or_format")
    local = lambda node: node.tag.rsplit("}", 1)[-1]
    ids, paths, parents, visible, seen = {}, {root: [0]}, {}, [], set()
    stack = [(root, False)]
    while stack:
        node, in_defs = stack.pop()
        budget.take("svg_nodes")
        if node in seen:
            raise SelectedContextError("svg_shared_node")
        seen.add(node)
        kind = local(node)
        if kind not in ("svg", "defs", "g", "path", "mask", "image", "use"):
            raise SelectedContextError("unknown_svg_element")
        if len(node.attrib) > 32:
            raise SelectedContextError("svg_attribute_budget")
        budget.take("operations", len(node.attrib))
        if node.get("id"):
            if node.get("id") in ids:
                raise SelectedContextError("duplicate_svg_resource_id")
            ids[node.get("id")] = node
        in_defs = in_defs or kind == "defs"
        if not in_defs and kind in ("use", "image"):
            visible.append(node)
        for i in range(len(node) - 1, -1, -1):
            child = node[i]
            paths[child], parents[child] = paths[node] + [i], node
            if len(paths[child]) > 32:
                raise SelectedContextError("svg_depth_budget")
            stack.append((child, in_defs))
    if local(root) != "svg":
        raise SelectedContextError("svg_root")
    glyphs_by_paint = {}
    for glyph in native["glyphs"]:
        glyphs_by_paint.setdefault(glyph["seqno"], []).append(glyph)
    expanded = []
    for paint in native["paints"]:
        if paint["kind"] == "fill_text":
            expanded.extend(("glyph", g) for g in glyphs_by_paint[paint["seqno"]])
        else:
            expanded.append(("image", paint))
    if len(visible) != len(expanded):
        raise SelectedContextError("complete_paint_count_mismatch")
    font_map, reverse_font_map, resources, selected_rows, order_rows = {}, {}, {}, [], []
    selected_set = set(selected)
    for order, (node, (kind, record)) in enumerate(zip(visible, expanded)):
        budget.take("operations")
        occurrence = "svg-paint-" + "-".join(map(str, paths[node]))
        if kind == "image":
            if local(node) != "image":
                raise SelectedContextError("image_order_mismatch")
            if occurrence in selected_set:
                raise SelectedContextError("selected_image_is_not_glyph")
            order_rows.append([occurrence, "image", record["seqno"]])
            continue
        if local(node) != "use" or set(node.attrib) != {"data-text", "{http://www.w3.org/1999/xlink}href", "transform"}:
            raise SelectedContextError("glyph_style_or_kind_unsupported")
        href = node.get("{http://www.w3.org/1999/xlink}href", "")
        match = re.fullmatch(r"#font_([0-9]+)_([0-9]+)", href)
        if not match or any(len(v) > 10 for v in match.groups()):
            raise SelectedContextError("glyph_reference")
        svg_font, gid = map(int, match.groups())
        if gid != record["gid"] or node.get("data-text") != chr(record["ucs"]):
            raise SelectedContextError("glyph_gid_or_unicode_mismatch")
        index = record["font_index"]
        font_map.setdefault(index, svg_font)
        reverse_font_map.setdefault(svg_font, index)
        if font_map[index] != svg_font or reverse_font_map[svg_font] != index:
            raise SelectedContextError("glyph_font_identity_mismatch")
        resource_node = ids.get(href[1:])
        if resource_node is None or local(resource_node) != "path" or set(resource_node.attrib) != {"id", "d"}:
            raise SelectedContextError("glyph_outline_resource")
        key = record["outline_key"]
        if key not in resources:
            controls = path32(resource_node.get("d"), budget)
            if controls != native["outlines"][key]:
                raise SelectedContextError("glyph_outline_f32_mismatch")
            resources[key] = sha(json.dumps(controls, separators=(",", ":")).encode())
        ancestor = parents.get(node)
        while ancestor is not None:
            budget.take("operations")
            if local(ancestor) not in ("svg", "g") or (local(ancestor) == "g" and ancestor.attrib):
                raise SelectedContextError("glyph_ancestor_context")
            ancestor = parents.get(ancestor)
        transform = node.get("transform", "")
        if not re.fullmatch(r"matrix\([^)]*\)", transform):
            raise SelectedContextError("glyph_matrix_syntax")
        tokens = transform[7:-1].replace(",", " ").split()
        if len(tokens) != 6 or any(not re.fullmatch(NUMBER, token) for token in tokens):
            raise SelectedContextError("glyph_matrix_syntax")
        if [number32(v, budget) for v in tokens] != record["matrix"]:
            raise SelectedContextError("glyph_matrix_f32_mismatch")
        order_rows.append([occurrence, "glyph", record["seqno"], record["span"], record["item"], index, gid])
        if occurrence in selected_set:
            selected_rows.append(dict(occurrence_id=occurrence, complete_order=order,
                                      native_paint_seqno=record["seqno"], native_span=record["span"], native_item=record["item"],
                                      gid=gid, ucs=record["ucs"], matrix=record["matrix"],
                                      font=native["fonts"][index], outline_sha256=resources[key],
                                      group_ids=record["group_ids"], clip_ids=[], mask_depth=0, tile_depth=0,
                                      svg_occurrence_sha256=sha(ET.tostring(node))))
    if [r["occurrence_id"] for r in selected_rows] != selected:
        raise SelectedContextError("selection_missing_or_reordered")
    if len(font_map) != len(native["fonts"]) or len(resources) != len(native["outlines"]):
        raise SelectedContextError("incomplete_font_or_outline_binding")
    return selected_rows, sha(json.dumps(order_rows, separators=(",", ":")).encode()), len(resources)


def prove(request):
    limits = _limits(Limits(**request["limits"]))
    budget = Budget(limits)
    pdf, svg = base64.b64decode(request["pdf"], validate=True), base64.b64decode(request["svg"], validate=True)
    if len(pdf) > limits.max_pdf_bytes or len(svg) > limits.max_svg_bytes:
        raise SelectedContextError("worker_input_byte_budget")
    native = capture_native(pdf, request["page"], budget)
    import pymupdf as F
    # Fresh independent export binds SVG bytes to this source; not SVG metadata trust.
    with F.open(stream=pdf, filetype="pdf") as document:
        fresh = document[request["page"] - 1].get_svg_image(text_as_path=True).encode()
    if len(fresh) > limits.max_svg_bytes:
        raise SelectedContextError("fresh_svg_byte_budget")
    if fresh != svg:
        raise SelectedContextError("source_svg_bytes_mismatch")
    selected, mapping_hash, outline_count = bind_svg(native, svg, request["selected"], budget)
    proof = dict(schema=SCHEMA, proof_scope="selected_context_facts_only",
                 source_pdf_sha256=sha(pdf), source_svg_sha256=sha(svg), page=request["page"],
                 pymupdf=native["pymupdf"], native_replay=native["replay"], selected=selected, complete_native_paints=len(native["paints"]),
                 complete_native_glyphs=len(native["glyphs"]), complete_order_sha256=mapping_hash,
                 font_count=len(native["fonts"]), outline_resource_count=outline_count,
                 groups=native["groups"], source_group_declaration=native["source_group"], native_events=native["events"], used=budget.used,
                 image_paints_with_support_unproved=[p["seqno"] for p in native["paints"] if p["kind"] == "fill_image"],
                 whole_roi_scope_proved=False, image_reconstruction_proved=False, geometry_or_style_proved=False,
                 source_canvas_clip_authorized=False, rgb_pixel_equivalence_proved=False,
                 existing_v1_whole_roi_default_replaced=False,
                 group_claim=GROUP_CLAIM)
    proof["proof_sha256"] = sha(json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    return proof


def main():
    # Parent is bounded too. Apply hard safety caps before loading request/PyMuPDF.
    resource.setrlimit(resource.RLIMIT_AS, (1_073_741_824, 1_073_741_824))
    resource.setrlimit(resource.RLIMIT_CPU, (62, 62))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    result_path = Path(sys.argv[2])
    try:
        with Path(sys.argv[1]).open("rb") as stream:
            data = stream.read(60_000_001)
        if len(data) > 60_000_000:
            raise SelectedContextError("request_byte_budget")
        request = json.loads(data)
        limits = _limits(Limits(**request["limits"]))
        resource.setrlimit(resource.RLIMIT_AS, (limits.worker_memory_bytes, limits.worker_memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (limits.worker_seconds, limits.worker_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_output_bytes, limits.max_output_bytes))
        result = dict(status="ok", proof=prove(request))
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > limits.max_output_bytes:
            raise SelectedContextError("output_byte_budget")
        result_path.write_bytes(encoded)
    except Exception as error:
        code = error.code if isinstance(error, SelectedContextError) else "worker_exception"
        result_path.write_text(json.dumps(dict(status="error", error=code)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
