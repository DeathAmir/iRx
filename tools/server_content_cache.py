#!/usr/bin/env python3
"""Download server-advertised iRx content into an isolated per-server cache.

Expected manifest:
{"files":[{"path":"maps/ac_example.cgz","url":"https://...","sha256":"...","size":1234}]}
Downloaded .cfg files are DATA ONLY and are never executed by this tool.
"""
import argparse
import hashlib
import json
import os
import re
import ssl
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

ALLOWED_EXT = {'.cgz', '.cfg', '.wpt', '.png', '.jpg', '.jpeg', '.ogg', '.wav', '.md3', '.md2', '.txt', '.json'}
MAX_FILE = 64 * 1024 * 1024
MAX_MANIFEST = 1024 * 1024
MAX_FILES = 1024
USER_AGENT = 'iRx-content-cache/1.0'


def safe_server_name(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', value)[:160] or 'server'


def safe_relpath(value: str) -> Path:
    p = PurePosixPath(value.replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f'unsafe path: {value!r}')
    suffix = Path(p.name).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise ValueError(f'disallowed extension: {suffix}')
    return Path(*p.parts)


def read_url(url: str, limit: int) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('only HTTPS URLs are accepted')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        length = r.headers.get('Content-Length')
        if length and int(length) > limit:
            raise ValueError('download exceeds size limit')
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError('download exceeds size limit')
        return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server', required=True, help='server label, e.g. example.org_28763')
    ap.add_argument('--manifest', required=True, help='HTTPS JSON manifest URL')
    ap.add_argument('--root', type=Path, default=Path('saved/server-content'))
    args = ap.parse_args()

    manifest_data = read_url(args.manifest, MAX_MANIFEST)
    manifest = json.loads(manifest_data.decode('utf-8'))
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError('invalid manifest files list')

    base = args.root / safe_server_name(args.server)
    base.mkdir(parents=True, exist_ok=True)
    report = {'server': args.server, 'manifest': args.manifest, 'files': []}

    for item in files:
        if not isinstance(item, dict):
            raise ValueError('invalid manifest entry')
        rel = safe_relpath(str(item.get('path', '')))
        url = str(item.get('url', ''))
        declared = int(item.get('size', 0) or 0)
        if declared < 0 or declared > MAX_FILE:
            raise ValueError(f'invalid size for {rel}')
        data = read_url(url, min(MAX_FILE, declared if declared else MAX_FILE))
        if declared and len(data) != declared:
            raise ValueError(f'size mismatch for {rel}')
        digest = hashlib.sha256(data).hexdigest()
        expected = str(item.get('sha256', '')).lower()
        if expected and (len(expected) != 64 or digest != expected):
            raise ValueError(f'SHA-256 mismatch for {rel}')

        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmpname = tempfile.mkstemp(prefix='.irx-', dir=str(dst.parent))
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmpname, dst)
        finally:
            if os.path.exists(tmpname):
                os.unlink(tmpname)
        report['files'].append({'path': rel.as_posix(), 'size': len(data), 'sha256': digest})
        print(f'cached {rel} ({len(data)} bytes)')

    (base / 'manifest.lock.json').write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    print(f'cache: {base}')
    print('note: downloaded CubeScript files are not executed automatically')


if __name__ == '__main__':
    main()
