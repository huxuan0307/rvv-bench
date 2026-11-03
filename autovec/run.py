#!/usr/bin/env python3
"""Execute built bare-metal images with NEMU; require successful trap."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--build', type=Path, default=Path(__file__).resolve().parent / 'build/O3')
    p.add_argument('--nemu', default='/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--timeout', type=int, default=180)
    args = p.parse_args()
    manifest = json.loads((args.build / 'manifest.json').read_text())
    def execute(case):
        dest = Path(case['directory'])
        if case['status'] != 'built':
            return {'suite': case['suite'], 'name': case['name'], 'status': 'not-built'}
        cmd = [args.nemu, '-b', str(dest / (case['name'] + '.bin'))]
        status = 'failed'
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=args.timeout, text=True, errors='replace')
            output = r.stdout
            if r.returncode == 0 and 'HIT GOOD TRAP' in output and not re.search(r'ERROR|HIT BAD TRAP|HIT CRITICAL ERROR', output):
                status = 'passed'
        except subprocess.TimeoutExpired as e:
            output = (e.stdout or b'').decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
            status = 'timeout'
        (dest / 'run.log').write_text(output)
        print(f"{status.upper()} {case['suite']}/{case['name']}", flush=True)
        return {'suite': case['suite'], 'name': case['name'], 'status': status, 'command': cmd}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(execute, manifest['cases']))
    (args.build / 'runs.json').write_text(json.dumps(results, indent=2))
    return int(any(r['status'] != 'passed' for r in results))

if __name__ == '__main__':
    sys.exit(main())
