"""Source-bound selected native glyph context facts from a trusted local worker.

This explicit API neither replaces v1 whole-ROI defaults nor authorizes geometry,
image reconstruction, source coverage, sourceCanvas edits, or RGB equivalence.
Native work always runs in an isolated worker; importing this module does not
load the optional PDF library. The worker requires POSIX process resource limits.
"""
from dataclasses import asdict, dataclass, fields
from contextlib import contextmanager
import base64
import hashlib
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile


class SelectedGlyphContextError(ValueError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(code + (": " + detail[:400] if detail else ""))


@dataclass(frozen=True)
class SelectedGlyphContextLimits:
    max_pdf_bytes: int = 8_388_608
    max_svg_bytes: int = 2_097_152
    max_selected: int = 64
    max_paints: int = 64
    max_glyphs: int = 8192
    max_font_resources: int = 64
    max_pdf_objects: int = 16384
    max_fonts: int = 16
    max_font_bytes: int = 2_097_152
    max_resources: int = 256
    max_controls: int = 65536
    max_context_events: int = 256
    max_context_depth: int = 8
    max_svg_nodes: int = 16384
    max_operations: int = 2_000_000
    max_integer_bits: int = 32
    max_numeric_lexeme_bytes: int = 64
    max_output_bytes: int = 262144
    worker_seconds: int = 12
    worker_memory_bytes: int = 536_870_912


def _limits(value):
    if type(value) is not SelectedGlyphContextLimits:
        raise SelectedGlyphContextError("invalid_limits_type")
    for f in fields(value):
        x = getattr(value, f.name)
        if type(x) is not int or x < 1 or x.bit_length() > 32:
            raise SelectedGlyphContextError("invalid_budget", f.name)
    if value.max_integer_bits > 64:
        raise SelectedGlyphContextError("integer_limit_ceiling")
    if value.worker_seconds > 60 or value.worker_memory_bytes > 1_073_741_824:
        raise SelectedGlyphContextError("worker_limit_ceiling")
    if value.max_pdf_bytes > 33_554_432 or value.max_svg_bytes > 8_388_608 or value.max_output_bytes > 1_048_576:
        raise SelectedGlyphContextError("byte_limit_ceiling")
    return value


SCHEMA = "selected-native-glyph-context-v1"
GROUP_CLAIM = "Captured whole-page isolated DeviceRGB Normal/unit-alpha/non-knockout context only. No group-removal, sourceCanvas, or pixel-equivalence authorization."
FALSE_FLAGS = ("whole_roi_scope_proved", "image_reconstruction_proved", "geometry_or_style_proved",
               "source_canvas_clip_authorized", "rgb_pixel_equivalence_proved", "existing_v1_whole_roi_default_replaced")


def _protocol_fail(detail=""):
    raise SelectedGlyphContextError("worker_protocol", detail)


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _protocol_fail("duplicate JSON key")
        value[key] = item
    return value


def _validate_proof(proof, pdf_hash, svg_hash, page, selected, limits):
    """Validate trusted-local-worker transport, not authenticate a hostile worker.

    Hashes bind requests and detect inconsistent serialization. The digest alone
    cannot establish that a worker executed source replay; this process/worker
    pair remains the trust boundary. Geometry and image support stay unproved.
    """
    def obj(value, keys):
        if type(value) is not dict or set(value) != set(keys):
            _protocol_fail("closed object schema")
        return value
    def integer(value, low=0, high=None):
        if type(value) is not int or value < low or value.bit_length() > limits.max_integer_bits or (high is not None and value > high):
            _protocol_fail("integer range/type")
        return value
    def string(value, cap=256):
        if type(value) is not str or not 1 <= len(value) <= cap:
            _protocol_fail("string range/type")
        return value
    def digest(value):
        if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
            _protocol_fail("SHA256 syntax")
        return value
    def array(value, cap, minimum=0):
        if type(value) is not list or not minimum <= len(value) <= cap:
            _protocol_fail("list range/type")
        return value
    def numbers(value, size):
        array(value, size, size)
        for x in value:
            if type(x) not in (int, float) or not math.isfinite(x) or abs(x) >= 2 ** limits.max_integer_bits:
                _protocol_fail("finite numeric vector")
        return value
    fields = ("schema proof_scope source_pdf_sha256 source_svg_sha256 page pymupdf selected complete_native_paints "
              "complete_native_glyphs complete_order_sha256 font_count outline_resource_count groups source_group_declaration "
              "native_events used image_paints_with_support_unproved group_claim proof_sha256 native_replay").split() + list(FALSE_FLAGS)
    obj(proof, fields)
    if proof["schema"] != SCHEMA or proof["proof_scope"] != "selected_context_facts_only" or proof["group_claim"] != GROUP_CLAIM:
        _protocol_fail("scope/version")
    if any(proof[key] is not False for key in FALSE_FLAGS):
        _protocol_fail("authorization flags")
    if digest(proof["source_pdf_sha256"]) != pdf_hash or digest(proof["source_svg_sha256"]) != svg_hash:
        raise SelectedGlyphContextError("worker_source_binding")
    if integer(proof["page"], 1) != page:
        _protocol_fail("request page")
    string(proof["pymupdf"], 32)
    digest(proof["complete_order_sha256"])
    replay = obj(proof["native_replay"], ("errors", "incomplete", "aborted"))
    if integer(replay["errors"]) != 0 or integer(replay["incomplete"]) != 0 or replay["aborted"] is not False:
        _protocol_fail("native replay status")
    paints = integer(proof["complete_native_paints"], 1, limits.max_paints)
    glyphs = integer(proof["complete_native_glyphs"], 1, limits.max_glyphs)
    fonts = integer(proof["font_count"], 1, limits.max_fonts)
    outlines = integer(proof["outline_resource_count"], 1, limits.max_resources)
    if fonts > glyphs or outlines > glyphs:
        _protocol_fail("resource count consistency")
    images = array(proof["image_paints_with_support_unproved"], paints)
    for x in images: integer(x, 0, paints - 1)
    if images != sorted(set(images)) or paints > glyphs + len(images) or len(images) >= paints:
        _protocol_fail("paint count consistency")
    used = proof["used"]
    required = set("pdf_objects operations font_resources context_events paints fonts glyphs resources svg_nodes".split())
    if type(used) is not dict or not required <= set(used) <= required | {"controls"}:
        _protocol_fail("budget fields")
    for name, count in used.items(): integer(count, 0, getattr(limits, "max_" + name))
    if any(used[key] != val for key, val in (("paints", paints), ("fonts", fonts), ("glyphs", glyphs), ("resources", outlines))):
        _protocol_fail("budget count consistency")
    if used["font_resources"] < fonts or used["pdf_objects"] < 1 or used["svg_nodes"] < glyphs or used["operations"] < sum(v for k, v in used.items() if k != "operations"):
        _protocol_fail("budget accounting")
    groups = array(proof["groups"], 1)
    if groups:
        group = obj(groups[0], "group_id bbox colorspace isolated knockout blendmode alpha begin_paint_seqno end_paint_seqno".split())
        if integer(group["group_id"], 1, 1) != 1 or group["colorspace"] != "DeviceRGB" or group["isolated"] is not True or group["knockout"] is not False or integer(group["blendmode"]) != 0:
            _protocol_fail("group context")
        numbers([group["alpha"]], 1)
        if group["alpha"] != 1 or integer(group["begin_paint_seqno"]) != 0 or integer(group["end_paint_seqno"]) != paints:
            _protocol_fail("group range")
        bounds = numbers(group["bbox"], 4)
        if not bounds[0] < bounds[2] or not bounds[1] < bounds[3]: _protocol_fail("group rectangle")
        declaration = obj(proof["source_group_declaration"], ("xref", "properties"))
        integer(declaration["xref"], 1, used["pdf_objects"] - 1)
        props = obj(declaration["properties"], ("Type", "S", "CS", "I", "K"))
        for v in props.values():
            array(v, 2, 2)
            for x in v: string(x, 32)
        if (props["Type"] not in (["null", "null"], ["name", "/Group"]) or props["S"] != ["name", "/Transparency"] or props["CS"] != ["name", "/DeviceRGB"] or props["I"] != ["bool", "true"] or props["K"] not in (["null", "null"], ["bool", "false"])):
            _protocol_fail("source group declaration")
    elif proof["source_group_declaration"] is not None:
        _protocol_fail("unexpected source group")
    events = array(proof["native_events"], limits.max_context_events, 1)
    if len(events) != used["context_events"]: _protocol_fail("event count")
    group_open, group_seen, mask_at, last_paint = False, False, None, -1
    for index, event in enumerate(events):
        obj(event, ("kind", "paint_seqno"))
        kind, seq = string(event["kind"], 32), integer(event["paint_seqno"], 0, paints)
        if seq < last_paint: _protocol_fail("event order")
        last_paint = seq
        if index == 0:
            if kind != "set_default_colorspaces" or seq != 0: _protocol_fail("default event")
        elif kind == "begin_group":
            if not groups or group_seen or mask_at is not None or seq != 0: _protocol_fail("group begin event")
            group_seen = group_open = True
        elif kind == "end_group":
            if not group_open or mask_at is not None or seq != paints or index != len(events) - 1: _protocol_fail("group end event")
            group_open = False
        elif kind == "clip_image_mask":
            if mask_at is not None or seq not in images or (groups and not group_open): _protocol_fail("image mask event")
            mask_at = seq
            if 1 + int(group_open) > limits.max_context_depth: _protocol_fail("context depth budget")
        elif kind == "pop_clip":
            if mask_at is None or seq != mask_at + 1: _protocol_fail("image mask close event")
            mask_at = None
        else: _protocol_fail("unknown context event")
    if group_open or mask_at is not None or group_seen != bool(groups): _protocol_fail("unclosed event context")
    rows = array(proof["selected"], limits.max_selected, 1)
    if len(rows) != len(selected): _protocol_fail("selection count")
    previous_order, previous_native = -1, (-1, -1, -1)
    font_records = {}
    for wanted, row in zip(selected, rows):
        obj(row, "occurrence_id complete_order native_paint_seqno native_span native_item gid ucs matrix font outline_sha256 group_ids clip_ids mask_depth tile_depth svg_occurrence_sha256".split())
        if string(row["occurrence_id"]) != wanted: _protocol_fail("exact request selection")
        order = integer(row["complete_order"], 0, glyphs + len(images) - 1)
        seq = integer(row["native_paint_seqno"], 0, paints - 1)
        native_id = (seq, integer(row["native_span"], 0, glyphs - 1), integer(row["native_item"], 0, glyphs - 1))
        if order <= previous_order or native_id <= previous_native or seq in images: _protocol_fail("selected identity order")
        previous_order, previous_native = order, native_id
        array(row["clip_ids"], 0)
        array(row["group_ids"], 1)
        for x in row["group_ids"]: integer(x, 1, 1)
        if row["group_ids"] != ([1] if groups else []) or integer(row["mask_depth"]) or integer(row["tile_depth"]): _protocol_fail("selected context")
        numbers(row["matrix"], 6)
        ucs = integer(row["ucs"], 0, 0x10ffff)
        if 0xd800 <= ucs <= 0xdfff: _protocol_fail("Unicode scalar")
        digest(row["outline_sha256"]); digest(row["svg_occurrence_sha256"])
        font = obj(row["font"], "font_index name program_sha256 program_bytes glyph_count source_embedding".split())
        font_index = integer(font["font_index"], 0, fonts - 1)
        string(font["name"]); digest(font["program_sha256"])
        integer(font["program_bytes"], 1, limits.max_font_bytes)
        glyph_count = integer(font["glyph_count"], 1)
        integer(row["gid"], 0, glyph_count - 1)
        embeddings = array(font["source_embedding"], used["font_resources"], 1)
        seen_embeddings = set()
        for embedding in embeddings:
            obj(embedding, "font_xref descendant_xref descriptor_xref stream_xref stream_key".split())
            for key in ("font_xref", "descriptor_xref", "stream_xref"): integer(embedding[key], 1, used["pdf_objects"] - 1)
            integer(embedding["descendant_xref"], 0, used["pdf_objects"] - 1)
            if embedding["stream_key"] not in ("FontFile", "FontFile2", "FontFile3"): _protocol_fail("embedding stream kind")
            identity = tuple(embedding[k] for k in ("font_xref", "descendant_xref", "descriptor_xref", "stream_xref", "stream_key"))
            if identity in seen_embeddings: _protocol_fail("duplicate embedding")
            seen_embeddings.add(identity)
        if font_index in font_records and font_records[font_index] != font: _protocol_fail("inconsistent selected font")
        font_records[font_index] = font
    wanted_digest = digest(proof["proof_sha256"])
    without_digest = {k: v for k, v in proof.items() if k != "proof_sha256"}
    actual_digest = hashlib.sha256(json.dumps(without_digest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if wanted_digest != actual_digest: _protocol_fail("proof digest mismatch")
    return proof


@contextmanager
def _worker_storage(factory, *args, **kwargs):
    """Control storage failures, including entry/exit, without hiding prior errors."""
    body_error = None
    try:
        with factory(*args, **kwargs) as value:
            try:
                yield value
            except BaseException as error:
                body_error = error
                raise
    except OSError as error:
        # An exit failure must not replace an already specific worker error or
        # a process interrupt. Storage-only failures never return a proof.
        if body_error is not None and not isinstance(body_error, OSError):
            raise body_error from error
        raise SelectedGlyphContextError("worker_storage_failed") from (body_error or error)


def prove_selected_glyph_context(source_pdf_bytes, source_svg_bytes,
                                 selected_occurrence_ids, *, page=1,
                                 limits=SelectedGlyphContextLimits()):
    """Return source-bound facts, only after independent full native/SVG replay.

    Sources must be exact bytes and selections an exact tuple/list of unique
    canonical XML occurrence IDs in original paint order. There is intentionally
    no caller-supplied native trace, group bool, context receipt, or proof input.
    """
    limits = _limits(limits)
    if type(source_pdf_bytes) is not bytes or type(source_svg_bytes) is not bytes:
        raise SelectedGlyphContextError("source_type")
    if not source_pdf_bytes or len(source_pdf_bytes) > limits.max_pdf_bytes:
        raise SelectedGlyphContextError("pdf_byte_budget")
    if not source_svg_bytes or len(source_svg_bytes) > limits.max_svg_bytes:
        raise SelectedGlyphContextError("svg_byte_budget")
    if type(page) is not int or page < 1 or page.bit_length() > limits.max_integer_bits:
        raise SelectedGlyphContextError("page_type_or_range")
    if type(selected_occurrence_ids) not in (tuple, list):
        raise SelectedGlyphContextError("selection_container")
    if not 1 <= len(selected_occurrence_ids) <= limits.max_selected:
        raise SelectedGlyphContextError("selection_budget")
    import re
    for x in selected_occurrence_ids:
        if type(x) is not str or len(x) > 256 or not re.fullmatch(r"svg-paint-0(?:-(?:0|[1-9][0-9]*))+", x):
            raise SelectedGlyphContextError("selection_id")
        if any(len(p) > 10 or int(p).bit_length() > limits.max_integer_bits for p in x.split("-")[2:]):
            raise SelectedGlyphContextError("selection_integer_budget")
    if len(set(selected_occurrence_ids)) != len(selected_occurrence_ids):
        raise SelectedGlyphContextError("selection_duplicate")
    request = dict(pdf=base64.b64encode(source_pdf_bytes).decode("ascii"),
                   svg=base64.b64encode(source_svg_bytes).decode("ascii"),
                   selected=list(selected_occurrence_ids), page=page,
                   limits=asdict(limits))
    if os.name != "posix":
        raise SelectedGlyphContextError("worker_platform_unsupported")
    worker = Path(__file__).with_name("_pdf_selected_glyph_context_worker.py")
    try:
        worker_available = worker.is_file()
    except OSError as error:
        raise SelectedGlyphContextError("worker_unavailable") from error
    if not worker_available:
        raise SelectedGlyphContextError("worker_unavailable")
    with _worker_storage(tempfile.TemporaryDirectory, prefix="selected-glyph-context-") as temp:
        temp = Path(temp)
        request_path, result_path = temp / "request.json", temp / "result.json"
        request_path.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
        with _worker_storage((temp / "stdout").open, "wb") as stdout, _worker_storage((temp / "stderr").open, "wb") as stderr:
            try:
                completed = subprocess.run([sys.executable, "-I", str(worker), str(request_path), str(result_path)],
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                           timeout=limits.worker_seconds + 2, check=False)
            except subprocess.TimeoutExpired as exc:
                raise SelectedGlyphContextError("worker_timeout") from exc
            except OSError as exc:
                raise SelectedGlyphContextError("worker_launch_failed") from exc
        if not result_path.exists():
            raise SelectedGlyphContextError("worker_failed", "exit=" + str(completed.returncode))
        try:
            with result_path.open("rb") as stream:
                data = stream.read(limits.max_output_bytes + 1)
        except OSError as exc:
            raise SelectedGlyphContextError("worker_protocol", "unreadable response") from exc
        if len(data) > limits.max_output_bytes:
            raise SelectedGlyphContextError("output_byte_budget")
        try:
            result = json.loads(data, object_pairs_hook=_json_object, parse_constant=lambda _: _protocol_fail("nonfinite JSON constant"))
        except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
            raise SelectedGlyphContextError("worker_protocol") from exc
        if type(result) is not dict or type(result.get("status")) is not str:
            _protocol_fail("response envelope")
        if result["status"] == "error":
            if set(result) != {"status", "error"} or type(result["error"]) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", result["error"]):
                _protocol_fail("error envelope")
            raise SelectedGlyphContextError(result["error"])
        if set(result) != {"status", "proof"} or result["status"] != "ok":
            _protocol_fail("success envelope")
        if completed.returncode:
            raise SelectedGlyphContextError("worker_failed", "success response from failed worker")
        try:
            return _validate_proof(result["proof"], hashlib.sha256(source_pdf_bytes).hexdigest(),
                                   hashlib.sha256(source_svg_bytes).hexdigest(), page, selected_occurrence_ids, limits)
        except SelectedGlyphContextError:
            raise
        except (TypeError, ValueError, KeyError, OverflowError, RecursionError) as exc:
            raise SelectedGlyphContextError("worker_protocol", "malformed proof") from exc
