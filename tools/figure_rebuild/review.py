"""Bind visual review to content and reserve immutable build snapshots."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time


REVIEW_METADATA = {
    'status', 'reviewed_at', 'acceptance', 'reviewed_digest',
    'reviewed_revision', 'review_note',
}


def content_digest(manifest):
    """Hash canonical content, including revision, but not review bookkeeping."""
    if not isinstance(manifest, dict):
        raise ValueError('Manifest must be a record')
    content = dict(manifest)
    recognition = manifest.get('recognition')
    if not isinstance(recognition, dict):
        raise ValueError('Recognition must be a record')
    content['recognition'] = {
        key: value for key, value in recognition.items() if key not in REVIEW_METADATA
    }
    try:
        encoded = json.dumps(content, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError('Manifest content is not canonical JSON: ' + str(exc)) from exc
    return hashlib.sha256(encoded).hexdigest()


def verify_review(manifest):
    recognition = manifest.get('recognition', {})
    if not isinstance(recognition, dict) or recognition.get('status') != 'reviewed':
        raise ValueError('Recognition needs visual review before authoring')
    expected = content_digest(manifest)
    if recognition.get('reviewed_digest') != expected:
        raise ValueError('Review is missing or stale: content changed; run review again')
    if recognition.get('reviewed_revision') != manifest.get('revision'):
        raise ValueError('Review revision is stale; run review again')
    return expected


def check_revision_history(build_root, manifest):
    """Same immutable revision may be reproduced; changed content needs a new one."""
    build_root = Path(build_root)
    current = content_digest(manifest)
    revision = manifest.get('revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('revision must be a positive integer')
    previous = []
    for snapshot in sorted(build_root.glob('run-*/manifest-snapshot.json')):
        try:
            old = json.loads(snapshot.read_text(encoding='utf-8'))
            old_revision = old.get('revision')
            if type(old_revision) is not int or old_revision < 1:
                raise ValueError('invalid saved revision')
            if old.get('id') != manifest.get('id'):
                raise ValueError('Job identity changed; prepare a new job')
            # Legacy snapshots have no binding.  Once a bound snapshot exists,
            # later changes to its contents must not silently rewrite history.
            if isinstance(old.get('recognition'), dict) and 'reviewed_digest' in old['recognition']:
                verify_review(old)
            previous.append((old_revision, content_digest(old)))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f'Cannot trust build snapshot {snapshot}: {exc}') from exc
    same_revision = [checksum for old_revision, checksum in previous if old_revision == revision]
    if same_revision:
        if any(checksum != current for checksum in same_revision):
            raise ValueError('This revision already has different content; increment revision and review again')
        return current
    if previous and revision <= max(old_revision for old_revision, _ in previous):
        raise ValueError('Changed content needs a revision greater than previous build snapshots')
    return current


def allocate_run(build_root):
    """Exclusive mkdir is the reservation; concurrent callers cannot share a run."""
    build_root = Path(build_root)
    build_root.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        run = build_root / f'run-{number:03d}'
        try:
            run.mkdir()
            return run
        except FileExistsError:
            number += 1


@contextmanager
def allocation_lock(build_root, timeout=15):
    """OS locks release on process exit; retain the lock inode to avoid races."""
    build_root = Path(build_root)
    build_root.mkdir(parents=True, exist_ok=True)
    lock_file = (build_root / '.allocation.lock').open('a+b')
    if lock_file.seek(0, os.SEEK_END) == 0:
        lock_file.write(b'0'); lock_file.flush()
    start = time.monotonic()
    locked = False
    try:
        while not locked:
            try:
                if os.name == 'nt':
                    import msvcrt
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (BlockingIOError, PermissionError):
                if time.monotonic() - start >= timeout:
                    raise ValueError('Timed out waiting for build snapshot allocation; another build is reserving a run')
                time.sleep(.05)
        yield
    finally:
        if locked:
            if os.name == 'nt':
                import msvcrt
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
