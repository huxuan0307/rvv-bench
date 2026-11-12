#!/usr/bin/env python3
"""在指定节点按ABI、七函数和LOG/EXP复跑顺序运行外部脚本后端。"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import runner_common as common
import campaign

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--nemu-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--emu', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--runner', type=Path, default=Path('/nfs/home/huxuan/opt/bin/run_emu.sh'))
    parser.add_argument('--jobs', type=int, default=32)
    parser.add_argument('--timeout', default='48h')
    parser.add_argument('--host', default='node033')
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] != args.host:
        parser.error('campaign must execute on ' + args.host)
    if not 1 <= args.jobs <= 32:
        parser.error('jobs must be 1..32')
    manifest = common.load_manifest(args.manifest)
    funcs = ['log', 'exp', 'sin', 'cos', 'pow', 'logf', 'expf']
    names = ['abi-public'] + ['perf-' + f for f in funcs]
    campaign.require_gate(args.nemu_run, 'nemu', manifest['sha256'], set(names))
    tools = {key: common.identity(path) for key, path in
             [('gsim', args.emu), ('reference', args.reference), ('runner', args.runner)]}
    if 'static' in args.emu.name.lower():
        parser.error('non-static gsim required')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    frozen = output / 'runner-source'; frozen.mkdir()
    # 冻结运行与验收代码，后续编辑不影响本轮。
    for src in HERE.glob('*.py'):
        if not src.name.startswith('test_'):
            (frozen / src.name).write_bytes(src.read_bytes())
    state = dict(schema_version=1, state='running', hostname=socket.gethostname(),
                 started_at=common.utc_now(), manifest=str(args.manifest.resolve()),
                 manifest_sha256=manifest['sha256'], tools=tools, jobs=args.jobs,
                 stages=[], runner_sources={p.name: common.identity(p) for p in frozen.glob('*.py')})
    def save(): common.write_json(output / 'campaign.json', state)
    save(); reports = []
    try:
        for label, cases in [('abi', ['abi-public']), ('performance', ['perf-' + f for f in funcs]),
                             ('replay', ['perf-log', 'perf-exp'])]:
            for identity in tools.values():
                if common.sha256(identity['realpath']) != identity['sha256']:
                    raise ValueError('tool identity changed before stage ' + label)
            run = output / label
            command = [sys.executable, '-B', str(frozen / 'run.py'), 'gsim', '--backend', 'run-emu',
                       '--runner', str(args.runner.resolve()), '--manifest', str(args.manifest.resolve()),
                       '--output', str(run), '--emu', str(args.emu.resolve()),
                       '--reference', str(args.reference.resolve()), '--jobs', str(args.jobs),
                       '--timeout', args.timeout, '--no-smoke']
            for case in cases: command += ['--case', case]
            entry = dict(stage=label, state='running', command=command, output=str(run), started_at=common.utc_now())
            state['stages'].append(entry); save()
            print('STAGE_START=' + label, flush=True)
            with (output / (label + '.stdout.log')).open('w') as stdout, (output / (label + '.stderr.log')).open('w') as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr)
            entry['returncode'] = result.returncode
            audit = [sys.executable, '-B', str(frozen / 'collect.py'), '--run', str(run), '--require-complete']
            with (output / (label + '.audit.stdout.log')).open('w') as stdout, (output / (label + '.audit.stderr.log')).open('w') as stderr:
                check = subprocess.run(audit, stdout=stdout, stderr=stderr)
            entry.update(audit_returncode=check.returncode, finished_at=common.utc_now())
            document = json.loads((run / 'reports/results.json').read_text())
            entry['state'] = 'passed' if result.returncode == check.returncode == 0 and document['state'] == 'passed' else 'failed'
            save()
            if entry['state'] != 'passed':
                raise RuntimeError('strict stage gate failed: ' + label)
            reports.append(run / 'reports/results.json')
            print('STAGE_PASS=' + label, flush=True)
        command = [sys.executable, '-B', str(frozen / 'analyze.py'), '--output', str(output / 'analysis'), '--require-full']
        for report in reports: command += ['--report', str(report)]
        with (output / 'analysis.stdout.log').open('w') as stdout, (output / 'analysis.stderr.log').open('w') as stderr:
            result = subprocess.run(command, stdout=stdout, stderr=stderr)
        state['analysis_returncode'] = result.returncode
        if result.returncode:
            raise RuntimeError('full analysis rejected incomplete evidence')
        state.update(state='passed', finished_at=common.utc_now()); save(); return 0
    except BaseException as exc:
        state.update(state='failed', error=repr(exc), finished_at=common.utc_now()); save(); raise


if __name__ == '__main__':
    raise SystemExit(main())
