#!/usr/bin/env python3
"""Safe CubeScript minifier/obfuscator for scripts you own.

It removes comments/extra whitespace without adding anti-analysis, persistence,
or execution-bypass behavior. Optional private-alias renaming is deliberately
restricted to names beginning with a caller supplied prefix.
"""
import argparse
import hashlib
import json
import random
import re
from pathlib import Path


def strip_comments(src: str) -> str:
    out = []
    i = 0
    quote = False
    block_depth = 0
    while i < len(src):
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(src):
                i += 1
                out.append(src[i])
            elif c == '"':
                quote = False
            i += 1
            continue
        if c == '"':
            quote = True
            out.append(c)
            i += 1
            continue
        if c == '[':
            block_depth += 1
            out.append(c)
            i += 1
            continue
        if c == ']':
            block_depth = max(0, block_depth - 1)
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < len(src) and src[i + 1] == '/':
            i += 2
            while i < len(src) and src[i] not in '\r\n':
                i += 1
            if i < len(src):
                out.append('\n')
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def minify(src: str) -> str:
    src = strip_comments(src)
    out = []
    quote = False
    pending_space = False
    for c in src:
        if quote:
            out.append(c)
            if c == '"' and (len(out) < 2 or out[-2] != '\\'):
                quote = False
            continue
        if c == '"':
            if pending_space and out and out[-1] not in '[; ': out.append(' ')
            pending_space = False
            quote = True
            out.append(c)
            continue
        if c.isspace():
            pending_space = True
            continue
        if c in ';[]':
            while out and out[-1] == ' ': out.pop()
            out.append(c)
            pending_space = False
            continue
        if pending_space and out and out[-1] not in '[; ':
            out.append(' ')
        pending_space = False
        out.append(c)
    return ''.join(out).strip() + '\n'


def rename_private_aliases(src: str, prefix: str, seed: str):
    if not prefix or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', prefix):
        return src, {}
    names = sorted(set(re.findall(r'\b' + re.escape(prefix) + r'[A-Za-z0-9_]*\b', src)))
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    mapping = {}
    used = set()
    for name in names:
        while True:
            candidate = '_i' + ''.join(rng.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(10))
            if candidate not in used:
                break
        used.add(candidate)
        mapping[name] = candidate
    if mapping:
        pattern = re.compile(r'\b(' + '|'.join(map(re.escape, sorted(mapping, key=len, reverse=True))) + r')\b')
        src = pattern.sub(lambda m: mapping[m.group(1)], src)
    return src, mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input', type=Path)
    ap.add_argument('-o', '--output', type=Path)
    ap.add_argument('--rename-prefix', default='')
    ap.add_argument('--seed', default='irx')
    ap.add_argument('--mapping-out', type=Path)
    args = ap.parse_args()

    src = args.input.read_text(encoding='utf-8')
    src, mapping = rename_private_aliases(src, args.rename_prefix, args.seed)
    result = minify(src)
    out = args.output or args.input.with_suffix(args.input.suffix + '.min.cfg')
    out.write_text(result, encoding='utf-8', newline='\n')
    if args.mapping_out:
        args.mapping_out.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding='utf-8')
    print(f'wrote {out} ({len(src)} -> {len(result)} chars)')


if __name__ == '__main__':
    main()
