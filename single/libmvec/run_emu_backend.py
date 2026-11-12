#!/usr/bin/env python3
"""通过run_emu.sh排队，同时记录真实gsim命令、逐例隔离与退出证据。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import runner_common as common


def prepare(run, metadata, runner):
    run = Path(run)
    frozen = run / 'backend-source'
    frozen.mkdir()
    for name in ('run_emu_backend.py', 'runner_common.py'):
        shutil.copy2(Path(__file__).with_name(name), frozen / name)
    proxy = run / 'run-emu-proxy'
    proxy.write_text('#!' + sys.executable + '\nimport sys\n'
                     + 'sys.path.insert(0, ' + repr(str(frozen)) + ')\n'
                     + 'from run_emu_backend import proxy_main\n'
                     + 'raise SystemExit(proxy_main(' + repr(str(run)) + ', sys.argv[1:]))\n')
    proxy.chmod(0o755)
    logs = run / 'external-logs'
    logs.mkdir()
    command = [str(runner), '-e', str(proxy), '-r', metadata['tools']['reference']['realpath'],
               '-t', str(run / 'staging'), '-j', str(metadata['policy']['jobs']),
               '--timeout', metadata['policy']['timeout'], '-o', str(logs), '--show-cmd']
    info = {'runner': metadata['tools']['runner'], 'proxy': common.identity(proxy),
            'source': common.identity(frozen / 'run_emu_backend.py'),
            'common_source': common.identity(frozen / 'runner_common.py'),
            'hostname': socket.gethostname(), 'command': command,
            'stdout': str(run / 'external.stdout.log'), 'stderr': str(run / 'external.stderr.log'),
            'log_root': str(logs)}
    metadata['external_runner'] = info
    return info


def _record(case, **updates):
    result = {'schema_version': common.SCHEMA_VERSION, 'name': case['name'],
              'suite': case['suite'], 'state': 'completed', 'backend': 'run-emu',
              'command': case['command'], 'cwd': case['workdir'],
              'stdout': case['stdout'], 'stderr': case['stderr'],
              'started_at': common.utc_now(), 'finished_at': common.utc_now(),
              'returncode': None, 'timed_out': False, 'launch_error': None,
              'elapsed_seconds': 0.0, 'aborted': False}
    result.update(updates)
    common.write_json(case['result'], result)
    return result


def proxy_main(run, argv):
    """外部脚本调用的入口；只接受精确--diff REF -i STAGED_BIN。"""
    metadata = json.loads((Path(run) / 'run-metadata.json').read_text())
    if len(argv) != 4 or argv[0] != '--diff' or argv[2] != '-i':
        print('proxy: expected --diff REF -i STAGED_BIN', file=sys.stderr)
        return 125
    matching = [c for c in metadata['cases'] if c['staged_bin'] == argv[3]]
    if len(matching) != 1:
        print('proxy: unknown or ambiguous staged image', file=sys.stderr)
        return 125
    case = matching[0]
    started = time.monotonic()
    start_stamp = common.utc_now()
    process = None
    interrupted = []
    previous = {}

    def stop(signum, frame):
        interrupted.append(signum)
        if process is not None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, stop)
    try:
        expected = common.gsim_command(metadata['tools']['gsim']['realpath'],
            metadata['tools']['reference']['realpath'], case['staged_bin'],
            metadata['tools']['dram_config']['realpath'], case['dram_outdir'], metadata['policy']['seed'])
        if argv[1] != metadata['tools']['reference']['realpath']:
            raise ValueError('proxy differential reference differs from metadata')
        if expected != case['command']:
            raise ValueError('proxy actual command differs from canonical gsim command')
        if common.sha256(case['staged_bin']) != case['sha256']:
            raise ValueError('proxy staged BIN identity changed')
        for key in ('gsim', 'reference', 'dram_config'):
            tool = metadata['tools'][key]
            if common.sha256(tool['realpath']) != tool['sha256']:
                raise ValueError('proxy tool/config identity changed: ' + key)
        # 不创建新session：外部timeout/中断的进程组信号同时到达proxy和真实emu。
        with Path(case['stdout']).open('w') as stdout, Path(case['stderr']).open('w') as stderr:
            process = subprocess.Popen(expected, cwd=case['workdir'], stdout=stdout, stderr=stderr)
            code = process.wait()
        record = _record(case, started_at=start_stamp, returncode=code,
                         elapsed_seconds=time.monotonic()-started,
                         state='aborted' if interrupted else 'completed', aborted=bool(interrupted),
                         proxy_command=list(argv), signals=interrupted)
        return 128 + interrupted[0] if interrupted else (code if code >= 0 else 128-code)
    except (OSError, ValueError, KeyError) as exc:
        _record(case, started_at=start_stamp, launch_error=str(exc),
                elapsed_seconds=time.monotonic()-started, proxy_command=list(argv),
                state='aborted' if interrupted else 'completed', aborted=bool(interrupted))
        print('proxy: ' + str(exc), file=sys.stderr)
        return 125
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def list_statuses(info, cases):
    logs = Path(info['log_root'])
    directories = sorted(p for p in logs.iterdir() if p.is_dir())
    known = {c['name'] for c in cases}
    statuses = {name: [] for name in known}
    issues = []
    for directory in directories:
        for status in ('pass', 'fail', 'timeout'):
            path = directory / ('.' + status + '_list')
            if path.is_file():
                for name in path.read_text().splitlines():
                    if name not in known:
                        issues.append('unknown external list case: ' + name)
                    else:
                        statuses[name].append(status)
    result = {}
    for name, values in statuses.items():
        result[name] = values[0] if len(values) == 1 else ('conflict' if values else 'missing')
        if len(values) != 1:
            issues.append(name + ': external status ' + result[name])
    return result, issues, [str(p) for p in directories]


def execute(config):
    """外部总退出码不代替逐例结果与清单验收。"""
    run = Path(config['run'])
    info = config['metadata']['external_runner']
    registry = common.ProcessRegistry()
    previous = {}

    def stop(signum, frame):
        registry.abort.set()
        registry.terminate(signal.SIGTERM)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, stop)
    started = common.utc_now()
    try:
        # 总预算覆盖每个排队用例；单例时限由run_emu.sh的timeout负责。
        result = common.launch_process(info['command'], run, info['stdout'], info['stderr'],
            config['timeout_seconds'] * len(config['cases']) + 300, registry)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        registry.terminate(signal.SIGKILL)
    statuses, issues, directories = list_statuses(info, config['cases'])
    records = []
    for case in config['cases']:
        path = Path(case['result'])
        status = statuses[case['name']]
        try:
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or record.get('command') != case['command'] or record.get('backend') != 'run-emu':
                raise ValueError('proxy result does not match recorded case')
        except (OSError, ValueError, TypeError):
            record = _record(case, launch_error='external runner produced no valid proxy result',
                             state='aborted' if result.get('aborted') else 'completed',
                             aborted=bool(result.get('aborted')))
        record['external_status'] = status
        if status == 'timeout':
            record['timed_out'] = True
        if status != 'pass' or record.get('returncode') != 0 or record.get('aborted') or record.get('launch_error'):
            issues.append(case['name'] + ': external or actual process did not complete successfully')
        common.write_json(path, record)
        records.append(record)
    state = 'aborted' if result.get('aborted') else 'terminated'
    ok = not issues and result.get('returncode') == 0 and not result.get('launch_error') and not result.get('timed_out')
    receipt = {'schema_version': common.SCHEMA_VERSION, 'state': 'aborted' if result.get('aborted') else 'completed',
               'command': info['command'], 'started_at': started, 'finished_at': common.utc_now(),
               **result, 'issues': issues, 'list_statuses': statuses, 'log_directories': directories}
    common.write_json(run / 'external-result.json', receipt)
    counts = {}
    for record in records:
        key = record.get('state', 'unknown')
        counts[key] = counts.get(key, 0) + 1
    common.write_json(run / 'runner-state.json', {
        'schema_version': common.SCHEMA_VERSION, 'state': state, 'backend': 'run-emu',
        'started_at': config['metadata']['created_at'], 'finished_at': common.utc_now(),
        'returncode': 0 if ok else 1, 'mode': 'gsim', 'selected_cases': len(records),
        'launched_cases': len(records), 'result_states': counts, 'all_processes_successful': ok,
        'external_returncode': result.get('returncode'), 'external_issues': issues})
    print('RUN_DIRECTORY=' + str(run), flush=True)
    print('RUNNER_STATE=' + state + ' RUNNER_EXIT=' + ('0' if ok else '1'), flush=True)
    return 0 if ok else 1
