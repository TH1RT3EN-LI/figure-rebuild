"""Derive a PDF by changing RGB only where a decoded soft mask is exactly zero.

Partial alpha and every positive-alpha RGB sample are preserved. This proves
pointwise premultiplied equivalence, not filtered appearance or source pixels.
The raw native export must remain separately available.
"""
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import re

from .pdf_binary_alpha import (_binding, _decode, _dimensions, _load,
                               _plain_image, _same_data, _sha, _snapshot, _stream)


POLICY = 'pdf-zero-alpha-rgb-white-v1'
LIMITS = {'max_input_bytes': 64_000_000, 'max_objects': 10_000,
          'max_images': 2048, 'max_image_pixels': 8_000_000, 'max_image_axis': 32768,
          'max_decoded_work_bytes': 256_000_000}


def _limits(value):
    if value is None:
        return dict(LIMITS)
    if type(value) is not dict or set(value) - set(LIMITS):
        raise ValueError('Invalid zero-alpha PDF limits')
    result = dict(LIMITS)
    for key, number in value.items():
        if type(number) is not int or not 0 < number <= LIMITS[key]:
            raise ValueError('Zero-alpha PDF limits must be positive bounded integers')
        result[key] = number
    return result


def _plan(pdf, limits):
    if (not pdf.is_pdf or pdf.is_encrypted or pdf.is_repaired or
            pdf.xref_length() > limits['max_objects']):
        raise ValueError('PDF identity or object count is outside zero-alpha policy')
    if pdf.xref_get_key(pdf.pdf_catalog(), 'AcroForm')[0] != 'null':
        raise ValueError('PDF forms/signatures are outside zero-alpha policy')
    owners, images = defaultdict(list), set()
    for xref in range(1, pdf.xref_length()):
        if pdf.xref_get_key(xref, 'Subtype') == ('name', '/Image'):
            images.add(xref)
        kind, value = pdf.xref_get_key(xref, 'SMask')
        if kind == 'xref':
            owners[int(value.split()[0])].append(xref)
    if len(images) > limits['max_images']:
        raise ValueError('PDF image count exceeds bounded zero-alpha policy')
    plans, skipped, work = [], [], 0
    for mask, parents in sorted(owners.items()):
        reason = None
        if (mask not in images or not _plain_image(pdf, mask, '/DeviceGray') or
                pdf.xref_get_key(mask, 'SMask')[0] != 'null'):
            reason = 'unsupported_soft_mask'
        elif pdf.xref_get_key(mask, 'Matte')[0] != 'null':
            reason = 'existing_matte_retained'
        decode = _decode(pdf.xref_get_key(mask, 'Decode'), 1)
        if decode not in ([Fraction(0), Fraction(1)], [Fraction(1), Fraction(0)]):
            reason = reason or 'unsupported_mask_decode'
        size = _dimensions(pdf, mask, limits) if reason is None else None
        if reason is None and size is None:
            reason = 'unsupported_dimensions'
        if reason is None and any(
                parent not in images or not _plain_image(pdf, parent, '/DeviceRGB') or
                pdf.xref_get_key(parent, 'Matte')[0] != 'null' or
                _dimensions(pdf, parent, limits) != size or
                _decode(pdf.xref_get_key(parent, 'Decode'), 3) != [Fraction(x) for x in [0, 1] * 3]
                for parent in parents):
            reason = 'unsupported_or_shared_parent_context'
        if reason:
            skipped.append({'mask_xref': mask, 'parent_xrefs': parents, 'reason': reason})
            continue
        width, height = size
        if max(size) > limits['max_image_axis']:
            raise ValueError('PDF image exceeds bounded zero-alpha axis')
        # Charge mask, source RGB, mutable RGB, immutable candidate and a
        # temporary transparent-run slice before any stream is decoded.
        cost = width * height * (1 + 12 * len(parents))
        if work + cost > limits['max_decoded_work_bytes']:
            raise ValueError('Zero-alpha PDF decoded work budget exceeded')
        work += cost
        samples = _stream(pdf, mask, width * height)
        transparent = 0 if decode[0] == 0 else 255
        opaque = 255 - transparent
        count = samples.count(transparent)
        if count in (0, width * height):
            skipped.append({'mask_xref': mask, 'parent_xrefs': parents, 'reason': 'constant_alpha_retained'})
            continue
        transformed = []
        pattern = re.compile(re.escape(bytes([transparent])) + b'+')
        for parent in parents:
            original = _stream(pdf, parent, width * height * 3)
            candidate = bytearray(original)
            changed_count = 0
            for match in pattern.finditer(samples):
                start, end = match.span()
                segment = original[start * 3:end * 3]
                changed_count += sum(segment[i:i + 3] != b'\xff\xff\xff'
                                     for i in range(0, len(segment), 3))
                candidate[start * 3:end * 3] = b'\xff' * ((end - start) * 3)
            if changed_count:
                transformed.append({'xref': parent, 'source': original,
                                    'candidate': bytes(candidate), 'changed_pixels': changed_count})
        if not transformed:
            skipped.append({'mask_xref': mask, 'parent_xrefs': parents, 'reason': 'hidden_rgb_already_white'})
            continue
        opaque_count = samples.count(opaque)
        plans.append({'mask_xref': mask, 'parent_xrefs': parents, 'size': size,
                      'mask_samples_sha256': _sha(samples), 'transparent_pixels': count,
                      'opaque_pixels': opaque_count, 'partial_alpha_pixels': width * height - count - opaque_count,
                      'mask_decode': [int(x) for x in decode], 'parents': transformed})
    return plans, skipped, work


def _records(plans):
    return [{key: list(value) if key == 'size' else value
             for key, value in plan.items() if key != 'parents'} | {
        'parents': [{'xref': item['xref'], 'original_rgb_sha256': _sha(item['source']),
                     'derived_rgb_sha256': _sha(item['candidate']),
                     'changed_zero_alpha_rgb_pixels': item['changed_pixels']} for item in plan['parents']]
    } for plan in plans]


def _verify_documents(original, derived, plans):
    if (not derived.is_pdf or derived.is_encrypted or derived.is_repaired or
            original.xref_length() != derived.xref_length()):
        raise ValueError('Derived PDF changed object identities')
    before, after = _snapshot(original), _snapshot(derived)
    parents = {item['xref']: (plan, item) for plan in plans for item in plan['parents']}
    for xref, source in before.items():
        candidate = after[xref]
        if xref in parents:
            plan, item = parents[xref]
            keys_a = {k: v for k, v in source['keys'].items() if k not in {'Length', 'Filter'}}
            keys_b = {k: v for k, v in candidate['keys'].items() if k not in {'Length', 'Filter'}}
            if (keys_a != keys_b or
                    _stream(derived, xref, plan['size'][0] * plan['size'][1] * 3) != item['candidate']):
                raise ValueError('Derived RGB stream or image dictionary failed exact replay')
        elif source != candidate:
            raise ValueError('Derived PDF changed an alpha mask or unrelated object/stream')
    trailer_a = {k: original.xref_get_key(-1, k) for k in original.xref_get_keys(-1)
                 if k not in {'ID', 'DocChecksum'}}
    trailer_b = {k: derived.xref_get_key(-1, k) for k in derived.xref_get_keys(-1)
                 if k not in {'ID', 'DocChecksum'}}
    if trailer_a != trailer_b:
        raise ValueError('Derived PDF changed unrelated trailer fields')


def _receipt(source, original, derived, candidate, limits, plans, skipped, work):
    return {'schema_version': 1, 'policy': POLICY,
            'source_pdf': _binding(source, original), 'derived_pdf': _binding(derived, candidate),
            'limits': limits, 'decoded_work_bytes': work,
            'transformed_masks': _records(plans), 'retained_masks': skipped,
            'source_pdf_bytes_unchanged': True,
            'alpha_and_positive_alpha_rgb_samples_unchanged': True,
            'pointwise_premultiplied_samples_equivalent': True,
            'proof': 'For every decoded alpha a, (C_derived-C_original)*a=0: RGB changes only at exactly zero alpha. All partial/opaque RGB, every mask sample and all image dimensions remain unchanged.',
            'unrelated_pdf_object_semantics_and_encoded_streams_unchanged': True,
            'stream_length_references_may_be_inlined': True,
            'volatile_trailer_fields': ['ID', 'DocChecksum'],
            'rgb_alpha_filtering_error_bound_proved': False, 'visual_verification_required': True}


def derive_zero_alpha_rgb_pdf(source, output, *, limits=None):
    """Write a new, separate PDF; preserve raw export and all mask dictionaries."""
    limits = _limits(limits)
    source, data = _load(source, limits)
    output = Path(output).resolve()
    if output == source or output.exists():
        raise ValueError('Zero-alpha PDF destination must be new')
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError('Zero-alpha PDF derivation requires optional PyMuPDF') from exc
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        plans, skipped, work = _plan(pdf, limits)
        for plan in plans:
            for item in plan['parents']:
                explicit_null = 'DecodeParms' in pdf.xref_get_keys(item['xref'])
                pdf.update_stream(item['xref'], item['candidate'], compress=True)
                if explicit_null:
                    pdf.xref_set_key(item['xref'], 'DecodeParms', 'null')
        if plans:
            pdf.xref_set_key(-1, 'DocChecksum', 'null')
            candidate = pdf.tobytes(garbage=0, deflate=False)
        else:
            candidate = data
    if len(candidate) > limits['max_input_bytes']:
        raise ValueError('Derived PDF exceeds bounded file size')
    with output.open('xb') as stream:
        stream.write(candidate)
    if source.read_bytes() != data:
        raise ValueError('Raw PDF bytes changed during derivation')
    receipt = _receipt(source, data, output, candidate, limits, plans, skipped, work)
    verify_zero_alpha_rgb_pdf(source, output, receipt)
    return receipt


def verify_zero_alpha_rgb_pdf(source, derived, receipt):
    """Independently replay samples and bind every PDF object, mask and stream."""
    if (type(receipt) is not dict or type(receipt.get('schema_version')) is not int or
            receipt.get('schema_version') != 1 or receipt.get('policy') != POLICY):
        raise ValueError('Unsupported zero-alpha PDF receipt')
    limits = _limits(receipt.get('limits'))
    source, original_bytes = _load(source, limits)
    derived, derived_bytes = _load(derived, limits)
    import pymupdf
    with pymupdf.open(stream=original_bytes, filetype='pdf') as original, pymupdf.open(stream=derived_bytes, filetype='pdf') as candidate:
        plans, skipped, work = _plan(original, limits)
        expected = _receipt(source, original_bytes, derived, derived_bytes, limits, plans, skipped, work)
        if not _same_data(receipt, expected):
            raise ValueError('Zero-alpha PDF receipt disagrees with independently replayed samples and bindings')
        _verify_documents(original, candidate, plans)
        if not plans and original_bytes != derived_bytes:
            raise ValueError('A no-op zero-alpha derivation must preserve every raw PDF byte')
    return {'status': 'PASS', 'policy': POLICY, 'transformed_masks': len(plans)}
