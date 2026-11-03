#!/usr/bin/env python3
"""Compare observable benchmark output, excluding timing/simulator text."""
import argparse
import json
import math
from pathlib import Path
import re


def normalize(text, suite):
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    if suite == 'tsvc':
        return re.findall(r'(s\d+|v\w+)\s+AV cycles=\d+ checksum=([^\n]+)', text)
    if suite == 'polybench':
        match = re.search(r'==BEGIN DUMP_ARRAYS==(.*?)==END   DUMP_ARRAYS==', text, re.S)
        return match.group(1).split() if match else []
    return [line for line in text.splitlines() if not line.startswith(('AV ', '[', 'Using ', 'NEMU')) and line.strip()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--vector', type=Path, default=Path(__file__).resolve().parent / 'build/O3')
    p.add_argument('--scalar', type=Path, default=Path(__file__).resolve().parent / 'build/O3-scalar')
    a = p.parse_args()
    manifest = json.loads((a.vector / 'manifest.json').read_text())
    result = []
    scalar_manifest = json.loads((a.scalar / 'manifest.json').read_text())
    scalar_dirs = {(x['suite'], x['name']): Path(x['directory']) for x in scalar_manifest['cases']}
    for c in manifest['cases']:
        v = Path(c['directory']) / 'run.log'
        s = scalar_dirs[(c['suite'], c['name'])] / 'run.log'
        if c['suite'] == 'llvm':
            pattern = rf"{re.escape(c['name'])} checksum=(\d+) observations=(\d+) PASS"
            vm = re.search(pattern, v.read_text()) if v.exists() else None
            sm = re.search(pattern, s.read_text()) if s.exists() else None
            left = vm.groups() if vm else []
            right = sm.groups() if sm else []
        else:
            left = normalize(v.read_text(), c['suite']) if v.exists() else []
            right = normalize(s.read_text(), c['suite']) if s.exists() else []
        exact = bool(left) and left == right
        ok = bool(left) and len(left) == len(right)
        max_rel = 0.0
        if ok and not exact:
            for x, y in zip(left, right):
                if x == y:
                    continue
                try:
                    u, v = float(x), float(y)
                    max_rel = max(max_rel, abs(u-v) / max(abs(u), abs(v), 1e-300))
                    if not math.isclose(u, v, rel_tol=1e-10, abs_tol=1e-12):
                        ok = False
                except (ValueError, TypeError):
                    ok = False
        result.append({'suite': c['suite'], 'name': c['name'], 'status': ('exact-match' if exact else 'tolerance-match') if ok else 'different-or-missing', 'values': len(left), 'max_relative_error': max_rel})
        print(result[-1])
    (a.vector / 'comparison.json').write_text(json.dumps(result, indent=2))
    return int(any(r['status'] not in ('exact-match', 'tolerance-match') for r in result))

if __name__ == '__main__':
    raise SystemExit(main())
