#!/usr/bin/python3
"""Root-owned forced SSH command: receive bytes, never unpack or execute them."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


def receive(stream, root):
    header = stream.readline(1025)
    if len(header) > 1024 or not header.endswith(b'\n'):
        raise ValueError('invalid header')
    meta = json.loads(header)
    if not isinstance(meta, dict) or set(meta) != {'sha', 'sha256', 'size'}:
        raise ValueError('unexpected metadata fields')
    sha, digest, size = meta['sha'], meta['sha256'], meta['size']
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('invalid commit')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('invalid digest')
    if type(size) is not int or not 0 < size <= 64 * 1024 * 1024:
        raise ValueError('size limit')
    target = Path(tempfile.mkdtemp(prefix='github.', dir=root))
    h = hashlib.sha256()
    partial = target / 'archive.partial'
    with partial.open('xb') as out:
        remaining = size
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block:
                raise ValueError('incomplete upload; partial retained')
            out.write(block)
            h.update(block)
            remaining -= len(block)
        out.flush()
        os.fsync(out.fileno())
    if stream.read(1) or h.hexdigest() != digest:
        raise ValueError('digest/trailing bytes mismatch; partial retained')
    name = f'store-{sha}.tar.gz'
    partial.rename(target / name)
    (target / (name + '.sha256')).write_text(f'{digest}  {name}\n')
    (target / 'receipt.json').write_text(json.dumps(meta, sort_keys=True) + '\n')
    return {'status':'UPLOADED_NOT_INSTALLED', 'directory':str(target), **meta}


if __name__ == '__main__':
    os.umask(0o077)
    if os.environ.get('SSH_ORIGINAL_COMMAND') != 'upload-v1':
        sys.exit('Only upload-v1 is allowed')
    print(json.dumps(receive(sys.stdin.buffer, '/srv/nap-store/incoming')))
