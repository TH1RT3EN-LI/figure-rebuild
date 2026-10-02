"""Publish complete PPT + receipt, exclusively, with the PPT as commit point."""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


def stage(destination, content):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + destination.name + '-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Path(temporary)


def publish(source, output, receipt_path, receipt):
    """Stage both files; link receipt first and valid PPT last without overwrite.

    The receipt can survive a process kill before the final commit link. Readers
    must treat the receipt as delivered only when its PPT exists and hash matches.
    Ordinary failure removes links owned by this invocation. Hard links avoid
    both partial copying and the overwrite race of rename/replace.
    """
    source, output, receipt_path = map(Path, (source, output, receipt_path))
    if output.resolve() == receipt_path.resolve():
        raise ValueError('Output and receipt must be different paths')
    content = source.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if receipt.get('sha256') != checksum or receipt.get('output') != str(output.resolve()):
        raise ValueError('Delivery receipt does not identify the validated output bytes')
    staged, owned = [], []
    try:
        staged.append(stage(output, content))
        staged.append(stage(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
        for temporary, destination in ((staged[1], receipt_path), (staged[0], output)):
            os.link(temporary, destination)  # atomic and exclusive on same filesystem
            owned.append((temporary, destination))
    except BaseException:
        for temporary, destination in reversed(owned):
            if destination.exists() and os.path.samestat(temporary.stat(), destination.stat()):
                destination.unlink()
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
    return checksum


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('source', 'output', 'receipt', 'data'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    publish(args.source, args.output, args.receipt, json.loads(Path(args.data).read_text(encoding='utf-8')))
