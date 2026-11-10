#!/usr/bin/env python3
"""Run libmvec cases under NEMU or XiangShan gsim with strict, auditable setup.

Examples (from the repository root)::

  python3 single/libmvec/run.py nemu --manifest single/libmvec/build/manifest.json
  python3 single/libmvec/run.py gsim --manifest single/libmvec/build/manifest.json \\
      --case perf-log --case abi-public

``run.py`` never writes into an existing output directory.  Every input BIN is
copied to ``staging/`` and its SHA256 is checked against the manifest before and
after the copy.  gsim launches the emulator directly (not through run_emu.sh) in
a per-case working directory with a per-case DRAMSim3 output directory.  NEMU
runs in batch mode with the requested instruction budget.  A per-case timeout or
SIGINT/SIGTERM interrupts the whole process group and records ``aborted``.

Runner state lives in ``runner-state.json`` and moves from ``in_progress`` to
``terminated`` or ``aborted``.  The runner only reports launch/process results;
collect.py performs the strict completion, trap, record and diversity audit.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import signal
import tempfile

import runner_common as common

HERE = Path(__file__).resolve().parent
BUILD_DIR = HERE / 'build'
DEFAULT_EMU_DIR = Path('/nfs/share/huxuan/emus')
DEFAULT_EMU = Path('/nfs/share/huxuan/emus/emu-gsim-20261105-155351-409f429572')
DEFAULT_REF_DIR = Path('/nfs/share/huxuan/refmodel')
DEFAULT_NEMU = Path('/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter')
DEFAULT_DRAM_INI = Path('/nfs/home/share/ci-workloads/DRAMsim3/configs/XiangShan.ini')
DEFAULT_SEED = 123456789
DEFAULT_JOBS = 4
DEFAULT_GSIM_TIMEOUT = '48h'
DEFAULT_NEMU_TIMEOUT = '120s'
DEFAULT_MAX_INSTR = 50_000_000


def utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)

    def add_common(target, default_timeout):
        target.add_argument('--manifest', required=True, type=Path,
                            help='strict libmvec manifest (required)')
        target.add_argument('--output', type=Path,
                            help='fresh output directory; must not exist')
        target.add_argument('--case', action='append', metavar='NAME',
                            help='case name from the manifest; repeatable')
        target.add_argument('--jobs', type=int, default=DEFAULT_JOBS)
        target.add_argument('--timeout', default=default_timeout,
                            help='per-case GNU-style duration, e.g. 120s or 48h')

    nemu = sub.add_parser('nemu', help='functional NEMU run')
    add_common(nemu, DEFAULT_NEMU_TIMEOUT)
    nemu.add_argument('--nemu', type=Path, default=DEFAULT_NEMU)
    nemu.add_argument('--max-instr', type=int, default=DEFAULT_MAX_INSTR)

    gsim = sub.add_parser('gsim', help='XiangShan gsim cycle run')
    add_common(gsim, DEFAULT_GSIM_TIMEOUT)
    gsim.add_argument('--emu', type=Path,
                      help='explicit non-static gsim; default is the pinned '
                           '20261105 image, or newest non-static image if absent')
    gsim.add_argument('--emu-dir', type=Path, default=DEFAULT_EMU_DIR)
    gsim.add_argument('--reference', type=Path,
                      help='explicit differential reference; default is newest valid .so')
    gsim.add_argument('--ref-dir', type=Path, default=DEFAULT_REF_DIR)
    gsim.add_argument('--dramsim3-ini', type=Path, default=DEFAULT_DRAM_INI)
    gsim.add_argument('--seed', type=int, default=DEFAULT_SEED)
    gsim.add_argument('--no-smoke', action='store_true',
                      help='skip the initial serial smoke launch')
    return parser


def validate_arguments(parser, args):
    if args.mode == 'gsim':
        if not 1 <= args.jobs <= 4:
            parser.error('gsim jobs must be between 1 and 4')
        if not 0 <= args.seed <= common.U64_MAX:
            parser.error('seed must be an unsigned 64-bit integer')
    else:
        if not 1 <= args.jobs <= 64:
            parser.error('nemu jobs must be between 1 and 64')
        if args.max_instr < 1:
            parser.error('max-instr must be positive')
    try:
        args.timeout_seconds = common.parse_duration(args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    if args.case is not None:
        requested = []
        for name in args.case:
            if not common.case_name(name):
                parser.error(f'invalid case name: {name!r}')
            if name in requested:
                parser.error(f'duplicate --case selection: {name}')
            requested.append(name)
        args.case = requested
    return args


def select_cases(manifest, requested):
    all_cases = manifest['cases']
    if not requested:
        return list(all_cases)
    by_name = {case['name']: case for case in all_cases}
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise common.ManifestError(
            'requested case(s) not in manifest: ' + ', '.join(unknown))
    wanted = set(requested)
    selected = [case for case in all_cases if case['name'] in wanted]
    if len(selected) != len(wanted):
        raise common.ManifestError('duplicate or unresolved --case selection')
    return selected


def make_output(args):
    if args.output is not None:
        requested = args.output.expanduser()
        if requested.exists() or requested.is_symlink():
            raise common.ManifestError(
                f'output already exists: {requested}; select a fresh directory')
        run = requested.resolve()
        run.mkdir(parents=True, exist_ok=False)
        return run
    base = BUILD_DIR / f'{args.mode}-runs'
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=utc_stamp() + '-', dir=base))


def prepare(args):
    """Validate everything, create a fresh run, stage inputs and metadata."""
    manifest = common.load_manifest(args.manifest)
    selected = select_cases(manifest, args.case)
    if not selected:
        raise common.ManifestError('no cases selected')

    tools = {}
    seed = None
    dram = None
    if args.mode == 'gsim':
        if args.emu is not None:
            emu = args.emu.expanduser().resolve()
            if 'static' in emu.name.lower():
                raise common.ManifestError(
                    f'gsim must be a non-static build: {emu}')
        else:
            emu = common.select_gsim(args.emu_dir)
        reference = (args.reference.expanduser().resolve()
                     if args.reference is not None
                     else common.select_reference(args.ref_dir))
        dram_source = args.dramsim3_ini.expanduser()
        if not dram_source.is_file():
            raise common.ManifestError(f'DRAMSim3 Ini is missing: {dram_source}')
        tools['gsim'] = common.tool_identity(emu, kind='gsim')
        tools['reference'] = common.tool_identity(reference, kind='reference')
        dram = common.identity(dram_source)
        if not Path(dram['realpath']).is_file():
            raise common.ManifestError(
                f'DRAMSim3 Ini realpath is missing: {dram["realpath"]}')
        tools['dram_config'] = dram
        seed = args.seed
    else:
        nemu = args.nemu.expanduser().resolve()
        tools['nemu'] = common.tool_identity(nemu, kind='nemu')

    run = make_output(args)
    manifests = run / 'manifests'
    manifests.mkdir()
    snapshot = manifests / 'manifest.json'
    source_manifest = Path(manifest['path'])
    shutil.copy2(source_manifest, snapshot)
    if common.sha256(snapshot) != manifest['sha256']:
        raise common.ManifestError('manifest changed while taking its snapshot')

    common.stage_binaries(selected, run)

    cases = []
    for case in selected:
        case_dir = run / 'cases' / case['name']
        work = case_dir / 'work'
        work.mkdir(parents=True)
        if args.mode == 'gsim':
            dram_outdir = case_dir / 'dram'
            dram_outdir.mkdir()
            command = common.gsim_command(
                tools['gsim']['realpath'], tools['reference']['realpath'],
                case['staged_bin'], tools['dram_config']['realpath'],
                str(dram_outdir), seed)
            dram_value = str(dram_outdir)
        else:
            command = common.nemu_command(
                tools['nemu']['realpath'], case['staged_bin'], args.max_instr)
            dram_value = None
        cases.append({
            'name': case['name'], 'suite': case['suite'],
            'sha256': case['sha256'], 'staged_bin': case['staged_bin'],
            'staged_sha256': case['staged_sha256'],
            'workdir': str(work), 'dram_outdir': dram_value,
            'stdout': str(case_dir / 'stdout.log'),
            'stderr': str(case_dir / 'stderr.log'),
            'result': str(case_dir / 'result.json'),
            'command': command,
            'manifest_entry': case['entry'],
        })

    manifest_identity = common.identity(source_manifest)
    manifest_identity['snapshot'] = 'manifests/manifest.json'
    manifest_identity['snapshot_sha256'] = manifest['sha256']
    metadata = {
        'schema_version': common.SCHEMA_VERSION,
        'suite': common.SUITE_NAME,
        'mode': args.mode,
        'created_at': common.utc_now(),
        'run_directory': str(run),
        'working_directory': str(Path.cwd()),
        'selection': {
            'requested': list(args.case or []),
            'selected_cases': [case['name'] for case in selected],
            'manifest_cases': len(manifest['cases']),
        },
        'manifest': manifest_identity,
        'tools': tools,
        'runner_sources': {p.name: common.identity(p) for p in
                           (HERE/'run.py', HERE/'runner_common.py', HERE/'collect.py', HERE/'compact_csv.py')},
        'policy': {
            'jobs': args.jobs,
            'timeout': args.timeout,
            'timeout_seconds': args.timeout_seconds,
            'seed': seed,
            'max_instr': args.max_instr if args.mode == 'nemu' else None,
            'smoke': args.mode == 'gsim' and not args.no_smoke,
            'smoke_gate': 'strict-collector' if args.mode == 'gsim' and not args.no_smoke else None,
            'diff': args.mode == 'gsim',
            'per_case_cwd': True,
            'per_case_dram_outdir': args.mode == 'gsim',
        },
        'cases': cases,
    }
    common.write_json(run / 'run-metadata.json', metadata)
    common.write_json(run / 'runner-state.json', {
        'schema_version': common.SCHEMA_VERSION, 'state': 'in_progress',
        'started_at': metadata['created_at'], 'mode': args.mode,
        'launcher_pid': os.getpid(), 'selected_cases': len(cases),
    })
    return {
        'mode': args.mode, 'run': str(run), 'cases': cases,
        'jobs': args.jobs, 'timeout_seconds': args.timeout_seconds,
        'timeout': args.timeout, 'smoke': metadata['policy']['smoke'],
        'metadata': metadata,
    }


def _case_text(case):
    text = ''
    for key in ('stdout', 'stderr'):
        path = Path(case[key])
        if path.is_file():
            text += path.read_text(errors='replace') + '\n'
    return common.clean_log(text)


def _strict_smoke_gate(case, run):
    """Run the real strict collector audit over the first completed case.

    The runner deliberately does not reimplement acceptance.  A smoke success
    means collect.py's exact command, staging, trap, COMPLETE, CHECK and full
    performance/accuracy evidence checks all passed before any other gsim case
    is allowed to start.
    """
    try:
        import collect
        run, metadata, manifest = collect.load_run(run)
        issues = collect.audit_run(run, metadata, manifest)
        snapshot_case = next(
            item for item in manifest['cases'] if item['name'] == case['name'])
        meta_case = next(
            item for item in metadata['cases'] if item['name'] == case['name'])
        audit = collect.audit_case(
            snapshot_case, meta_case, metadata, run, 'in_progress')
    except (ImportError, OSError, ValueError, KeyError, TypeError,
            StopIteration) as exc:
        return False, [f'strict smoke gate could not audit the first case: {exc!r}']
    reasons = list(issues)
    if audit.get('status') != 'passed':
        reasons.append(f'strict smoke status is {audit.get("status")!r}')
        reasons.extend(audit.get('reasons', []))
    return not reasons, reasons


def _write_result(case, result):
    common.write_json(case['result'], result)
    return result


def _skipped_result(case, reason, *, state='skipped'):
    result = {
        'schema_version': common.SCHEMA_VERSION, 'name': case['name'],
        'suite': case['suite'], 'state': state, 'reason': reason,
        'command': case['command'], 'cwd': case['workdir'],
        'stdout': case['stdout'], 'stderr': case['stderr'],
        'returncode': None, 'timed_out': False, 'launch_error': None,
        'aborted': state == 'aborted', 'elapsed_seconds': 0.0,
        'started_at': common.utc_now(), 'finished_at': common.utc_now(),
    }
    for key in ('stdout', 'stderr'):
        path = Path(case[key])
        if not path.exists():
            path.write_text(f'runner: {state}: {reason}\n')
    return _write_result(case, result)


def execute_case(case, config, registry):
    if registry.abort.is_set():
        return _skipped_result(case, 'interrupted before launch', state='aborted')
    result = common.launch_process(
        case['command'], case['workdir'], case['stdout'], case['stderr'],
        config['timeout_seconds'], registry)
    state = 'aborted' if result.get('aborted') else 'completed'
    record = {
        'schema_version': common.SCHEMA_VERSION, 'name': case['name'],
        'suite': case['suite'], 'state': state,
        'command': case['command'], 'cwd': case['workdir'],
        'stdout': case['stdout'], 'stderr': case['stderr'],
        'started_at': common.utc_now(),
        'finished_at': common.utc_now(),
        **result,
    }
    _write_result(case, record)
    print(f"{state.upper()} {case['name']} rc={record.get('returncode')} "
          f"elapsed={record.get('elapsed_seconds', 0):.3f}s", flush=True)
    return record


def _install_signal_handlers(registry):
    previous = {}

    def handler(signum, frame):
        if registry.abort.is_set():
            registry.terminate(signal.SIGKILL)
        else:
            registry.abort.set()
            registry.terminate(signal.SIGTERM)

    if hasattr(signal, 'SIGHUP'):
        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    else:
        signals = (signal.SIGINT, signal.SIGTERM)
    for signum in signals:
        try:
            previous[signum] = signal.signal(signum, handler)
        except (ValueError, OSError):
            pass
    return previous


def _restore_signal_handlers(previous):
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            pass


def execute(config):
    registry = common.ProcessRegistry()
    previous = _install_signal_handlers(registry)
    selected = config['cases']
    results = []
    run = Path(config['run'])
    try:
        if config['smoke'] and len(selected) > 1:
            first = selected[0]
            first_result = execute_case(first, config, registry)
            results.append(first_result)
            ok, reasons = _strict_smoke_gate(first, run)
            if not ok:
                reason = 'strict serial smoke failed: ' + '; '.join(reasons)
                for case in selected[1:]:
                    results.append(_skipped_result(case, reason))
            else:
                with ThreadPoolExecutor(max_workers=config['jobs']) as pool:
                    results.extend(pool.map(
                        lambda case: execute_case(case, config, registry),
                        selected[1:]))
        else:
            with ThreadPoolExecutor(max_workers=config['jobs']) as pool:
                results = list(pool.map(
                    lambda case: execute_case(case, config, registry), selected))
    except BaseException as exc:
        registry.abort.set()
        registry.terminate(signal.SIGKILL)
        try:
            common.write_json(run / 'runner-state.json', {
                'schema_version': common.SCHEMA_VERSION, 'state': 'aborted',
                'started_at': config['metadata']['created_at'],
                'finished_at': common.utc_now(), 'error': repr(exc),
                'mode': config['mode'], 'selected_cases': len(selected),
                'launched_cases': len(results), 'returncode': None,
            })
        except OSError:
            pass
        raise
    finally:
        _restore_signal_handlers(previous)
        registry.terminate(signal.SIGKILL)

    final_state = 'aborted' if registry.abort.is_set() else 'terminated'
    all_ok = (len(results) == len(selected) and all(
        result.get('state') == 'completed' and result.get('returncode') == 0
        and not result.get('timed_out') and not result.get('launch_error')
        for result in results))
    returncode = 0 if final_state == 'terminated' and all_ok else 1
    counts = {}
    for result in results:
        key = result.get('state', 'unknown')
        counts[key] = counts.get(key, 0) + 1
    common.write_json(run / 'runner-state.json', {
        'schema_version': common.SCHEMA_VERSION, 'state': final_state,
        'started_at': config['metadata']['created_at'],
        'finished_at': common.utc_now(), 'returncode': returncode,
        'mode': config['mode'], 'selected_cases': len(selected),
        'launched_cases': len(results), 'result_states': counts,
        'all_processes_successful': all_ok,
    })
    print(f'RUN_DIRECTORY={run}', flush=True)
    print(f'RUNNER_STATE={final_state} RUNNER_EXIT={returncode}', flush=True)
    return returncode


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_arguments(parser, args)
    try:
        config = prepare(args)
    except (common.ManifestError, OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    return execute(config)


if __name__ == '__main__':
    raise SystemExit(main())
