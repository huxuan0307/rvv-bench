#!/usr/bin/env python3
"""Run one frozen P1 binary and audit both process termination and sample coverage."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from p1_extract import extract
import csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--emu', required=True, type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--nemu', action='store_true', help='use NEMU -b instead of RTL -i/--diff interface')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('timeout must be positive')
    binary = (args.build / 'p1.bin').resolve()
    if args.nemu:
        command = [str(args.emu.resolve()), '-b', str(binary)]
    else:
        command = [str(args.emu.resolve()), '-i', str(binary)]
        if args.reference:
            command += ['--diff', str(args.reference.resolve())]
    paths = {k: args.build / k for k in ('run.log', 'run.err', 'run-status.json', 'results.csv', 'coverage.json')}
    if any(p.exists() for p in paths.values()):
        parser.error('run outputs already exist; select a fresh build directory')
    manifest = json.loads((args.build / 'manifest.json').read_text())
    started = time.time()
    status = {'command': command, 'started_unix': started, 'timeout_seconds': args.timeout}
    with paths['run.log'].open('w') as stdout, paths['run.err'].open('w') as stderr:
        try:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=args.timeout)
            status.update(returncode=result.returncode, timed_out=False)
        except subprocess.TimeoutExpired:
            status.update(returncode=None, timed_out=True)
    status['elapsed_seconds'] = time.time() - started
    # NEMU emits guest UART on stderr, while RTL runners commonly use stdout.
    data_logs = [p for p in (paths['run.log'], paths['run.err'])
                 if 'case_id,kind,opcode,' in p.read_text(errors='replace')]
    fields, rows, coverage = extract(manifest, data_logs or [paths['run.log']])
    error_text = paths['run.err'].read_text(errors='replace') + paths['run.log'].read_text(errors='replace')
    for marker in ('HIT CRITICAL ERROR', 'HIT BAD TRAP', 'ABORT', 'Assertion failed', 'DIFFTEST FAILED', 'Segmentation fault'):
        if marker in error_text:
            coverage['errors'].append(f'stderr: {marker}')
    if status['returncode'] != 0 or status['timed_out']:
        coverage['errors'].append(f'process failed: returncode={status["returncode"]}, timed_out={status["timed_out"]}')
    coverage['complete'] = not coverage['errors']
    status['passed'] = coverage['complete']
    with paths['results.csv'].open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=(fields or []) + [
            'measurement_valid', 'cpi', 'active_elements_per_cycle', 'active_bytes_per_cycle', 'source_log', 'source_line'])
        writer.writeheader()
        writer.writerows(rows)
    paths['coverage.json'].write_text(json.dumps(coverage, indent=2) + '\n')
    paths['run-status.json'].write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps({'process': status, 'coverage': coverage}, indent=2))
    return 0 if status['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
