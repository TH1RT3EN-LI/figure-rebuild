"""Derive a PDF with exact white-matte encoding of binary image alpha.

The raw PDF and PPT media remain untouched. Only decoded binary samples are
proved equivalent; finite filtering and application appearance need review.
"""
from collections import defaultdict
from fractions import Fraction
import hashlib
from pathlib import Path
import re
import zlib


POLICY = 'binary-alpha-white-matte-v1'
LIMITS = {'max_input_bytes': 64_000_000, 'max_objects': 10_000,
          'max_images': 2048, 'max_image_pixels': 32_000_000,
          'max_decoded_work_bytes': 256_000_000}


def _limits(value):
    if value is None:
        return dict(LIMITS)
    if type(value) is not dict or set(value) - set(LIMITS):
        raise ValueError('Invalid binary-alpha PDF limits')
    result = dict(LIMITS)
    for key, number in value.items():
        if type(number) is not int or not 0 < number <= LIMITS[key]:
            raise ValueError('Binary-alpha PDF limits must be positive bounded integers')
        result[key] = number
    return result


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _binding(path, data):
    return {'path': str(path), 'sha256': _sha(data)}


def _decode(value, count):
    if value[0] == 'null':
        return [Fraction(x) for x in [0, 1] * count]
    if value[0] != 'array' or len(value[1]) > 256 or not re.fullmatch(r'\[\s*[-+0-9.\s]+\]', value[1]):
        return None
    tokens = value[1][1:-1].split()
    if len(tokens) != count * 2 or any(not re.fullmatch(r'[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', x) for x in tokens):
        return None
    return [Fraction(x) for x in tokens]


def _stream(pdf, xref, expected):
    """Decode only unpredicted, single Flate streams with bounded expansion."""
    if pdf.xref_get_key(xref, 'DecodeParms')[0] != 'null':
        raise ValueError('Predicted PDF image streams are outside binary-alpha policy')
    filter_value = pdf.xref_get_key(xref, 'Filter')
    raw = pdf.xref_stream_raw(xref)
    if not isinstance(raw, bytes):
        raise ValueError('PDF image is not a stream')
    if filter_value[0] == 'null':
        result = raw
    elif filter_value == ('name', '/FlateDecode'):
        decoder = zlib.decompressobj()
        try:
            result = decoder.decompress(raw, expected + 1)
        except zlib.error as exc:
            raise ValueError('Invalid PDF image Flate stream') from exc
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('PDF image stream exceeds its bounded decoded extent or has trailing data')
    else:
        raise ValueError('Unsupported PDF image encoding')
    if len(result) != expected:
        raise ValueError('PDF image stream size disagrees with declared dimensions')
    return result


def _dimensions(pdf, xref, limits):
    values = [pdf.xref_get_key(xref, key) for key in ('Width', 'Height')]
    if any(kind != 'int' or len(value) > 12 or not re.fullmatch(r'[0-9]+', value) for kind, value in values):
        return None
    width, height = [int(value) for _, value in values]
    if not width or not height or width * height > limits['max_image_pixels']:
        raise ValueError('PDF image exceeds bounded pixel extent')
    return width, height


def _plain_image(pdf, xref, colorspace):
    return (pdf.xref_is_stream(xref) and
            pdf.xref_get_key(xref, 'Type') == ('name', '/XObject') and
            pdf.xref_get_key(xref, 'ColorSpace') == ('name', colorspace) and
            pdf.xref_get_key(xref, 'BitsPerComponent') == ('int', '8') and
            pdf.xref_get_key(xref, 'ImageMask') in [('null', 'null'), ('bool', 'false')] and
            pdf.xref_get_key(xref, 'Mask')[0] == 'null' and
            pdf.xref_get_key(xref, 'Filter') in [('null', 'null'), ('name', '/FlateDecode')] and
            pdf.xref_get_key(xref, 'DecodeParms')[0] == 'null')


def _plan(pdf, limits):
    if pdf.xref_length() > limits['max_objects'] or pdf.is_encrypted or not pdf.is_pdf:
        raise ValueError('PDF identity or object count is outside binary-alpha policy')
    if pdf.xref_get_key(pdf.pdf_catalog(), 'AcroForm')[0] != 'null':
        raise ValueError('PDF forms/signatures are outside binary-alpha policy')
    owners = defaultdict(list)
    images = set()
    for xref in range(1, pdf.xref_length()):
        if pdf.xref_get_key(xref, 'Subtype') == ('name', '/Image'):
            images.add(xref)
        kind, value = pdf.xref_get_key(xref, 'SMask')
        if kind == 'xref':
            owners[int(value.split()[0])].append(xref)
    if len(images) > limits['max_images']:
        raise ValueError('PDF image count exceeds bounded policy')
    plans, skipped, work = [], [], 0
    for mask, parents in sorted(owners.items()):
        reason = None
        if mask not in images or not _plain_image(pdf, mask, '/DeviceGray') or pdf.xref_get_key(mask, 'SMask')[0] != 'null':
            reason = 'unsupported_soft_mask'
        elif pdf.xref_get_key(mask, 'Matte')[0] != 'null':
            reason = 'existing_matte_retained'
        decode = _decode(pdf.xref_get_key(mask, 'Decode'), 1)
        if decode not in ([Fraction(0), Fraction(1)], [Fraction(1), Fraction(0)]):
            reason = reason or 'unsupported_mask_decode'
        size = _dimensions(pdf, mask, limits) if reason is None else None
        if reason is None and size is None:
            reason = 'unsupported_dimensions'
        if reason is None and any(parent not in images or not _plain_image(pdf, parent, '/DeviceRGB') or
                                  _dimensions(pdf, parent, limits) != size or
                                  _decode(pdf.xref_get_key(parent, 'Decode'), 3) != [Fraction(x) for x in [0, 1] * 3]
                                  for parent in parents):
            reason = 'unsupported_or_shared_parent_context'
        if reason:
            skipped.append({'mask_xref': mask, 'parent_xrefs': parents, 'reason': reason})
            continue
        width, height = size
        # Charge mask, source RGB, mutable candidate and immutable copy before
        # decoding. This is work accounting, not native parser peak RSS.
        cost = width * height * (1 + 9 * len(parents))
        if work + cost > limits['max_decoded_work_bytes']:
            raise ValueError('Binary-alpha PDF decoded work budget exceeded')
        work += cost
        samples = _stream(pdf, mask, width * height)
        if any(value not in (0, 255) for value in samples):
            skipped.append({'mask_xref': mask, 'parent_xrefs': parents, 'reason': 'nonbinary_alpha_retained'})
            continue
        transparent = 0 if decode[0] == 0 else 255
        count = samples.count(transparent)
        if count in (0, width * height):
            skipped.append({'mask_xref': mask, 'parent_xrefs': parents, 'reason': 'constant_alpha_retained'})
            continue
        transformed = []
        pattern = re.compile(re.escape(bytes([transparent])) + b'+')
        for parent in parents:
            original = _stream(pdf, parent, width * height * 3)
            candidate = bytearray(original)
            for match in pattern.finditer(samples):
                start, end = match.span()
                candidate[start * 3:end * 3] = b'\xff' * ((end - start) * 3)
            changed = bytes(candidate)
            transformed.append({'xref': parent, 'source': original, 'candidate': changed})
        plans.append({'mask_xref': mask, 'parent_xrefs': parents, 'size': size,
                      'mask_samples_sha256': _sha(samples), 'transparent_pixels': count,
                      'opaque_pixels': width * height - count,
                      'mask_decode': [int(x) for x in decode], 'parents': transformed})
    return plans, skipped, work


def _records(plans):
    return [{key: list(value) if key == 'size' else value for key, value in plan.items() if key != 'parents'} | {
        'parents': [{'xref': item['xref'], 'original_rgb_sha256': _sha(item['source']),
                     'derived_rgb_sha256': _sha(item['candidate'])} for item in plan['parents']]
    } for plan in plans]


def _same_data(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same_data(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same_data(a, b) for a, b in zip(left, right))
    return left == right


def _snapshot(pdf):
    result = {}
    for xref in range(1, pdf.xref_length()):
        keys = {key: pdf.xref_get_key(xref, key) for key in pdf.xref_get_keys(xref)}
        raw = pdf.xref_stream_raw(xref) if pdf.xref_is_stream(xref) else None
        if raw is not None:
            kind, length = keys.get('Length', ('null', 'null'))
            if kind == 'xref':
                length = pdf.xref_object(int(length.split()[0])).strip()
            if not re.fullmatch(r'[0-9]+', length) or int(length) != len(raw):
                raise ValueError('PDF stream Length does not bind the actual encoded bytes')
            # MuPDF save may inline an indirect Length without changing bytes.
            keys['Length'] = ('int', str(len(raw)))
        result[xref] = {'keys': keys,
                        'scalar_or_array': pdf.xref_object(xref) if not keys else None,
                        'raw_stream_sha256': _sha(raw) if raw is not None else None}
    return result


def _verify_documents(original, derived, plans):
    if original.xref_length() != derived.xref_length():
        raise ValueError('Derived PDF changed object identities')
    before, after = _snapshot(original), _snapshot(derived)
    masks = {plan['mask_xref'] for plan in plans}
    parents = {item['xref']: (plan, item) for plan in plans for item in plan['parents']}
    for xref, source in before.items():
        candidate = after[xref]
        if xref in masks:
            keys = dict(candidate['keys'])
            matte = keys.pop('Matte', None)
            source_keys = {k: v for k, v in source['keys'].items() if k != 'Matte'}
            if matte != ('array', '[1 1 1]') or keys != source_keys or candidate['raw_stream_sha256'] != source['raw_stream_sha256']:
                raise ValueError('Derived soft mask changed beyond its exact Matte declaration')
        elif xref in parents:
            plan, item = parents[xref]
            keys_a = {k: v for k, v in source['keys'].items() if k not in {'Length', 'Filter'}}
            keys_b = {k: v for k, v in candidate['keys'].items() if k not in {'Length', 'Filter'}}
            if keys_a != keys_b or _stream(derived, xref, plan['size'][0] * plan['size'][1] * 3) != item['candidate']:
                raise ValueError('Derived RGB stream or parent image dictionary failed exact replay')
        elif source != candidate:
            raise ValueError('Derived PDF changed an unrelated object or stream')
    trailer_a = {k: original.xref_get_key(-1, k) for k in original.xref_get_keys(-1) if k not in {'ID', 'DocChecksum'}}
    trailer_b = {k: derived.xref_get_key(-1, k) for k in derived.xref_get_keys(-1) if k not in {'ID', 'DocChecksum'}}
    if trailer_a != trailer_b:
        raise ValueError('Derived PDF changed unrelated trailer fields')


def _load(path, limits):
    path = Path(path).resolve()
    if path.stat().st_size > limits['max_input_bytes']:
        raise ValueError('PDF input exceeds bounded file size')
    data = path.read_bytes()
    if not data or len(data) > limits['max_input_bytes']:
        raise ValueError('PDF input exceeds bounded file size or is empty')
    return path, data


def derive_binary_alpha_pdf(source, output, *, limits=None):
    """Create a separately named PDF; never overwrite raw export or source media."""
    limits = _limits(limits)
    source, data = _load(source, limits)
    output = Path(output).resolve()
    if output == source or output.exists():
        raise ValueError('Binary-alpha PDF destination must be new')
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError('Binary-alpha PDF derivation requires optional PyMuPDF') from exc
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        plans, skipped, work = _plan(pdf, limits)
        records = _records(plans)
        for plan in plans:
            for item in plan['parents']:
                explicit_null = 'DecodeParms' in pdf.xref_get_keys(item['xref'])
                pdf.update_stream(item['xref'], item['candidate'], compress=True)
                if explicit_null:
                    pdf.xref_set_key(item['xref'], 'DecodeParms', 'null')
            pdf.xref_set_key(plan['mask_xref'], 'Matte', '[1 1 1]')
        if plans:
            pdf.xref_set_key(-1, 'DocChecksum', 'null')
            pdf.save(output, garbage=0, deflate=False)
        else:
            with output.open('xb') as stream:
                stream.write(data)
    result = {'schema_version': 1, 'policy': POLICY,
              'source_pdf': _binding(source, data), 'derived_pdf': _binding(output, output.read_bytes()),
              'limits': limits, 'decoded_work_bytes': work,
              'transformed_masks': records, 'retained_masks': skipped,
              'source_pdf_bytes_unchanged': source.read_bytes() == data,
              'decoded_binary_rgba_samples_equivalent': True,
              'proof': 'At alpha=1, white-matte unblending returns the original RGB. At alpha=0, premultiplied color is zero; derived preblended RGB is exactly white. All mask samples and image dimensions remain unchanged.',
              'unrelated_pdf_object_semantics_and_encoded_streams_unchanged': True,
              'stream_length_references_may_be_inlined': True,
              'volatile_trailer_fields': ['ID', 'DocChecksum'],
              'rgb_alpha_filtering_error_bound_proved': False, 'visual_verification_required': True}
    verify_binary_alpha_pdf(source, output, result)
    return result


def verify_binary_alpha_pdf(source, derived, receipt):
    """Replay all sample changes and compare every unrelated PDF object/stream."""
    if (type(receipt) is not dict or type(receipt.get('policy')) is not str or receipt.get('policy') != POLICY or
            type(receipt.get('schema_version')) is not int or receipt.get('schema_version') != 1):
        raise ValueError('Unsupported binary-alpha PDF receipt')
    limits = _limits(receipt.get('limits'))
    source, original_bytes = _load(source, limits)
    derived, derived_bytes = _load(derived, limits)
    if receipt.get('source_pdf') != _binding(source, original_bytes) or receipt.get('derived_pdf') != _binding(derived, derived_bytes):
        raise ValueError('Binary-alpha PDF receipt file binding changed')
    import pymupdf
    with pymupdf.open(stream=original_bytes, filetype='pdf') as original, pymupdf.open(stream=derived_bytes, filetype='pdf') as candidate:
        plans, skipped, work = _plan(original, limits)
        if (not _same_data(receipt.get('transformed_masks'), _records(plans)) or
                not _same_data(receipt.get('retained_masks'), skipped) or
                type(receipt.get('decoded_work_bytes')) is not int or receipt.get('decoded_work_bytes') != work):
            raise ValueError('Binary-alpha PDF receipt disagrees with independently replayed plan')
        _verify_documents(original, candidate, plans)
    if (receipt.get('source_pdf_bytes_unchanged') is not True or
            receipt.get('decoded_binary_rgba_samples_equivalent') is not True or
            receipt.get('unrelated_pdf_object_semantics_and_encoded_streams_unchanged') is not True or
            receipt.get('rgb_alpha_filtering_error_bound_proved') is not False or
            receipt.get('visual_verification_required') is not True):
        raise ValueError('Binary-alpha PDF receipt has unsupported equivalence claims')
    return {'status': 'PASS', 'policy': POLICY, 'transformed_masks': len(plans)}
