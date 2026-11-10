#!/usr/bin/env python3
"""Stage all calibrated veclibm images and run them through run_emu.sh.

The external runner owns scheduling and each case's timeout. Input images and
manifests are copied before launch; no previous build or run output is deleted.
Use collect_veclibm_gsim.py --run RUN to inspect progress without restarting.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_RUNNER = Path('/nfs/home/huxuan/opt/bin/run_emu.sh')
DEFAULT_REF = Path('/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum')
DEFAULT_EMUS = Path('/nfs/share/huxuan/emus')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(path):
    path = Path(path).absolute()
    stat = path.stat()
    return dict(path=str(path), resolved_path=str(path.resolve()), size=stat.st_size,
                mtime_ns=stat.st_mtime_ns, sha256=sha256(path))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=64)
    parser.add_argument('--timeout', default='48h')
    parser.add_argument('--runner', type=Path, default=DEFAULT_RUNNER)
    parser.add_argument('--reference', type=Path, default=DEFAULT_REF)
    parser.add_argument('--emu-dir', type=Path, default=DEFAULT_EMUS)
    parser.add_argument('--emu', type=Path, help='explicit emulator; otherwise newest by mtime/name')
    parser.add_argument('--performance-manifest', type=Path, default=HERE / 'build/veclibm-quick/manifest.json')
    parser.add_argument('--accuracy-manifest', type=Path, default=HERE / 'veclibm-accuracy/build/manifest.json')
    parser.add_argument('--output', type=Path, help='new directory; defaults to unique build/veclibm-gsim run')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error('jobs must be between 1 and 64')
    if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)[smhd]?', args.timeout):
        parser.error('invalid GNU timeout duration')
    if args.emu:
        emulator = args.emu.resolve()
    else:
        candidates = [p for p in args.emu_dir.glob('emu-gsim-*') if p.is_file()]
        if not candidates:
            parser.error('no gsim emulator found')
        emulator = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name)).resolve()
    runner, reference = args.runner.absolute(), args.reference.absolute()
    for path in (emulator, runner, reference):
        if not path.is_file() or not os.access(path, os.R_OK):
            parser.error(f'required file missing or unreadable: {path}')
    if not os.access(emulator, os.X_OK) or not os.access(runner, os.X_OK):
        parser.error('emulator and runner must be executable')
    # Validate every manifest item before creating a run directory or executing.
    cases, manifests = [], []
    for suite, manifest_path, expected in [('performance', args.performance_manifest, 25),
                                           ('accuracy', args.accuracy_manifest, 141)]:
        manifest_path = manifest_path.resolve()
        document = json.loads(manifest_path.read_text())
        entries = document['cases']
        if len(entries) != expected:
            parser.error(f'{suite}: expected {expected} cases, got {len(entries)}')
        manifests.append((suite, manifest_path, document))
        for entry in entries:
            original = entry['name']
            name = suite + '-' + original
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
                parser.error(f'unsafe test name: {name}')
            binary = Path(entry['bin'])
            if not binary.is_absolute():
                binary = manifest_path.parent / binary
            binary = binary.resolve()
            if entry.get('status') != 'built' or not binary.is_file():
                parser.error(f'{name}: test is not built')
            digest = sha256(binary)
            if digest != entry.get('sha256', entry.get('bin_sha256')):
                parser.error(f'{name}: binary hash does not match manifest')
            cases.append(dict(name=name, original_name=original, suite=suite,
                              bin=str(binary), sha256=digest, manifest_entry=entry))
    if len({c['name'] for c in cases}) != 166:
        parser.error('expected 166 unique case names')
    if args.output:
        requested = args.output.expanduser()
        if requested.exists() or requested.is_symlink():
            parser.error(f'output already exists: {requested}')
        run = requested.resolve()
        run.mkdir(parents=True, exist_ok=False)
    else:
        base = HERE / 'build/veclibm-gsim'
        base.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=base))
    staging, logs, snapshots = run / 'bin', run / 'logs', run / 'manifests'
    for directory in (staging, logs, snapshots):
        directory.mkdir()
    for suite, path, document in manifests:
        shutil.copy2(path, snapshots / (suite + '.json'))
    for case in cases:
        target = staging / (case['name'] + '.bin')
        shutil.copy2(case['bin'], target)
        if sha256(target) != case['sha256']:
            raise RuntimeError(f'input changed while staging: {case["name"]}')
        case['staged_bin'] = str(target)
    command = [str(runner), '-e', str(emulator), '-r', str(reference), '-t', str(staging),
               '-j', str(args.jobs), '--timeout', args.timeout, '-o', str(logs), '--show-cmd']
    metadata = dict(schema_version=1, started_at=stamp(), jobs=args.jobs, timeout=args.timeout,
                    command=command, emulator=identity(emulator), reference=identity(reference),
                    runner=identity(runner), run_directory=str(run),
                    working_directory=str(run), diff_enabled=True, cases=cases,
                    manifests=[dict(suite=s, **identity(p)) for s, p, d in manifests],
                    input_policy='copied immutable-by-convention images, hash-verified against manifest',
                    scope='all 25 performance + 141 upstream accuracy/diagnostic quick-profile tests',
                    limits='per-case wall timeout only; no added max-instr/max-cycle; default no-fork')
    (run / 'run-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'RUN_DIRECTORY={run}', flush=True)
    print(shlex.join(command), flush=True)
    with (run / 'runner.log').open('w') as stdout, (run / 'runner.err').open('w') as stderr:
        try:
            process = subprocess.Popen(command, cwd=run, stdout=stdout, stderr=stderr)
            (run / 'runner-pid.json').write_text(json.dumps(dict(pid=process.pid, launcher_pid=os.getpid(), started_at=stamp())) + '\n')
            returncode = process.wait()
            result = dict(returncode=returncode, finished_at=stamp())
        except OSError as exc:
            returncode = 127
            result = dict(returncode=returncode, finished_at=stamp(), error=str(exc))
            stderr.write(str(exc) + '\n')
    (run / 'runner-exit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'RUNNER_EXIT={returncode}', flush=True)
    collector = HERE / 'collect_veclibm_gsim.py'
    if collector.is_file():
        collected = subprocess.run([sys.executable, str(collector), '--run', str(run)], check=False)
        return int(returncode != 0 or collected.returncode != 0)
    print('Collector not present; inspect logs before claiming success.', flush=True)
    return 2


if __name__ == '__main__':
    sys.exit(main())
