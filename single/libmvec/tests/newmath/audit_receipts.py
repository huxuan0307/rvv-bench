#!/usr/bin/env python3
"""只读审计显式指定的门禁目录，写独立receipt；不仿真、不覆盖。"""
from pathlib import Path
import argparse
import hashlib
import json
import re


def ident(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def unchanged(record):
    assert ident(record['path'])['sha256'] == record['sha256'], record['path']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gate-dir', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('拒绝覆盖已有receipt')
    rows = []
    for directory in args.gate_dir:
        directory = directory.resolve()
        gate_path = directory / 'gate-results.json'
        gate = json.loads(gate_path.read_text())
        commands_path = Path(gate['commands'])
        commands = json.loads(commands_path.read_text())
        assert gate['all_passed'] and gate['tests']
        unchanged(gate['new_archive'])
        unchanged(gate['new_archive_post'])
        unchanged(gate['nemu'])
        for command in commands:
            assert command['returncode'] == 0
            argv = command['command']
            if '-c' in argv or '-nostdlib' in argv:
                assert '-march=rva23u64' in argv
                assert all(not x.startswith('-march=') or x == '-march=rva23u64' for x in argv)
        for test in gate['tests']:
            assert test['status'] == 'pass'
            unchanged(test['binary'])
            unchanged(test['elf'])
            stdout = Path(test['stdout']).read_text()
            stderr = Path(test['stderr']).read_text()
            assert 'HIT GOOD TRAP' in stdout and re.search(test['marker'], stderr)
            for bad in ['HIT BAD TRAP', 'HIT CRITICAL ERROR', 'DIFFTEST ERROR', 'difftest check failed']:
                assert bad not in stdout and bad not in stderr
            assert '_ERROR ' not in stderr
        rows.append(dict(family=gate['family'], gate=ident(gate_path), commands=ident(commands_path),
                         abi_only=gate.get('abi_only', False), functions=gate['functions'],
                         tests=len(gate['tests']), all_passed=True))
    receipt = dict(schema=2, rows=rows, all_passed=True,
                   identity_boundary='Checks invocation-time identities recorded by each gate; no task-local historical claims.',
                   abi_scope='Only recorded functions and entries; not full36ABI', simulator_started=False)
    with args.output.open('x') as stream:
        stream.write(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
