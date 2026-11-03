#!/usr/bin/env python3
"""Freeze an existing P1 build, its inputs and optional logs with SHA256 provenance."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--emu', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--command', default='')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; choose a new immutable batch directory')
    source_dir = Path(__file__).resolve().parent
    root = source_dir.parent.parent
    required = ['p1.S', 'p1_cases.h', 'manifest.json', 'p1.elf', 'p1.bin']
    for name in required:
        if not (args.build / name).is_file():
            parser.error(f'missing build artifact {name}')
    args.output.mkdir(parents=True)
    hashes = {}

    def save(src, relative):
        dest = args.output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        hashes[str(relative)] = hashlib.sha256(dest.read_bytes()).hexdigest()

    for name in required:
        save(args.build / name, Path('build') / name)
    for name in ('p1_gen.py', 'p1_main.c', 'p1.mk', 'p1_extract.py', 'p1_run.py', 'p1_archive.py', 'P1.md', 'Makefile', 'config.h'):
        save(source_dir / name, Path('source') / name)
    save(root / 'config.mk', Path('source/config.mk'))
    for name in ('results.csv', 'coverage.json', 'run-status.json'):
        if (args.build / name).is_file():
            save(args.build / name, Path(name))
    for path in args.build.glob('*.log'):
        save(path, Path('logs') / path.name)
    for path in args.build.glob('*.err'):
        save(path, Path('logs') / path.name)
    external = {}
    for label, path in (('emu', args.emu), ('reference', args.reference)):
        if path:
            external[label] = {'path': str(path.resolve()),
                               'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    status = subprocess.check_output(['git', 'status', '--short'], cwd=root, text=True)
    (args.output / 'provenance.json').write_text(json.dumps({
        'created': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'repository_head': revision, 'workspace_status': status,
        'command': args.command, 'artifacts_sha256': hashes, 'external': external,
        'source_snapshot_note': 'Sources sampled at archive time; generated assembly and tested ELF/BIN are authoritative artifacts.'
    }, indent=2) + '\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
