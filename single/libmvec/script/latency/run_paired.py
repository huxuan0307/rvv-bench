#!/usr/bin/env python3
"""用指定的 RTL 模拟器和 DiffTest 参考执行一份冻结的配对镜像。"""

import argparse
import csv
from fractions import Fraction
from io import StringIO
import math
from pathlib import Path
import re
import shutil
import signal
from statistics import median
import sys
import uuid

from paired_parse import (MIN_STEADY, RECORD, WINDOW_MIN, ProtocolError,
                          common, file_matches, integer, load_plan, parse_wire,
                          require, strict_json)

STAMP = re.compile(r'\d{8}[-T]\d{6}')
RTL_TRAP = re.compile(
    r'^[ \t]*Core[ \t]+0[ \t]*:[ \t]*HIT[ \t]+GOOD[ \t]+TRAP'
    r'(?:[ \t]+at[ \t]+pc[ \t]*=[ \t]*0x[0-9a-f]+)?[ \t]*$', re.I)
DIFF_DISABLED = re.compile(
    r'\b(?:no[-_ ]?diff(?:test)?|without[ \t]+diff(?:test)?|'
    r'(?:disable[ds]?|skip(?:ped)?)[ \t]+diff(?:test)?)\b|'
    r'\bdiff(?:[-_ ]?test)?\b[^\r\n]{0,72}'
    r'(?:\b(?:disabled?|not[ \t]+(?:enabled|supported|available|used|compiled)|'
    r'turned[ \t]+off)\b|(?:[=:]|\bis\b)[ \t]*(?:off|false|0)\b)', re.I)
FATAL = re.compile(
    r'\b(?:LAT_ERROR|PAIR_ERROR|panic|core dumped)\b|\bcause[ \t]*=[ \t]*[0-9]|'
    r'\b(?:diff[-_ ]?test|ref(?:erence)? model)\b[^\r\n]*(?:mismatch|divergence|failed)',
    re.I)
RUNTIME_FILES = {'image': 'image.bin', 'reference': 'reference.so', 'dram': 'dram.ini',
                 'stdout': 'stdout.log', 'stderr': 'stderr.log'}


def new_output(requested, *, runtime=False):
    requested = requested.expanduser()
    require(not requested.exists() and not requested.is_symlink(), f'输出已存在：{requested}')
    target = requested.resolve()
    require(not any(p.lower() == 'build' or p.lower().startswith('build-') for p in target.parts),
            f'输出不得位于构建路径：{target}')
    if runtime:
        require(STAMP.search(target.name), '运行目录名必须含 YYYYMMDD-HHMMSS')
    target.mkdir(parents=True, exist_ok=False)
    return target


def local_file(run, name):
    path = run / name
    require(path.is_file() and not path.is_symlink() and path.resolve().parent == run,
            f'未保存独立的运行文件：{name}')
    return path


def rtl_arguments(parser):
    parser.add_argument('--output', required=True, type=Path, help='全新、带日期时间的运行目录')
    parser.add_argument('--kind', required=True, choices=('gsim', 'verilator'))
    parser.add_argument('--emu', required=True, type=Path, help='显式指定动态链接的 RTL 模拟器')
    parser.add_argument('--reference', required=True, type=Path, help='DiffTest 参考共享库')
    parser.add_argument('--dramsim3-ini', required=True, type=Path, help='DRAMSim3 配置')
    parser.add_argument('--seed', type=int, default=123456789)
    parser.add_argument('--timeout', default='2h', help='整体运行时限，如 120s 或 2h')


def stage(source, target, expected):
    file_matches(source, expected, 'staging 来源')
    with source.open('rb') as inp, target.open('xb') as out:
        shutil.copyfileobj(inp, out)
    file_matches(target, expected, 'staging 快照')


def launch_rtl(args, image, expected, *, build=None):
    """保存执行输入和真实进程回执。唯一启动 RTL 的位置。"""
    integer(args.seed, 'seed')
    timeout = common.parse_duration(args.timeout)
    require(math.isfinite(timeout), 'timeout 必须有限')
    emu, ref, dram = (p.expanduser().resolve(strict=True)
                      for p in (args.emu, args.reference, args.dramsim3_ini))
    # gsim 和 Verilator 都使用同一直接执行命令和动态 ELF 约束。
    emulator = common.tool_identity(emu, kind='gsim')
    reference = common.tool_identity(ref, kind='reference')
    require(dram.is_file(), 'DRAM 配置必须是普通文件')
    files = {'image': expected['sha256'], 'reference': reference['sha256'],
             'dram': common.sha256(dram)}
    file_matches(image, expected, 'BIN')
    run = new_output(args.output, runtime=True)
    if build is not None:
        manifest_path, manifest_sha, headers = build
        stage(manifest_path, run / 'build-manifest.json', {'sha256': manifest_sha})
        files['manifest'] = manifest_sha
        corpus = run / 'corpus'
        corpus.mkdir()
        for name, info in headers.items():
            stage(Path(info['snapshot']), corpus / name, info)
    for name, source in [('image', image), ('reference', ref), ('dram', dram)]:
        stage(source, run / RUNTIME_FILES[name], {'sha256': files[name]})
    common.shared_object_info(run / 'reference.so')
    common.validate_reference(run / 'reference.so')
    file_matches(emu, emulator, '模拟器')
    work, dram_out = run / 'work', run / 'dram'
    work.mkdir()
    dram_out.mkdir()
    command = common.gsim_command(emu, run / 'reference.so', run / 'image.bin', run / 'dram.ini',
                                  dram_out, args.seed)
    receipt = {'schema_version': 1, 'suite': 'libmvec-paired' if build else 'libmvec-lat',
               'launch_id': str(uuid.uuid4()), 'kind': args.kind, 'seed': args.seed,
               'timeout_seconds': timeout, 'command': command, 'cwd': str(work),
               'emulator': emulator, 'reference': reference, 'files': files,
               'started_at': common.utc_now(), 'state': 'running'}
    common.write_json(run / 'execution.json', receipt)
    registry = common.ProcessRegistry()

    def interrupted(signum, frame):
        # 由 launch_process 在等待周期内回收进程组，避免从信号处理器获取 registry 锁。
        registry.abort.set()

    handlers = {sig: signal.signal(sig, interrupted)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        result = common.launch_process(command, work, run / 'stdout.log', run / 'stderr.log',
                                       timeout, registry)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    for name in ('stdout', 'stderr'):
        files[name] = common.sha256(run / RUNTIME_FILES[name])
    # 哈希在调用前后都检查；异常保留状态，不写成功标记。
    file_matches(emu, emulator, '模拟器')
    for name in ('image', 'reference', 'dram'):
        file_matches(run / RUNTIME_FILES[name], {'sha256': files[name]}, name)
    receipt.update(state='terminated', finished_at=common.utc_now(), result=result)
    common.write_json(run / 'execution.json', receipt)
    return run


def audit_rtl(run, suite, *, bin_sha=None):
    """重新核对运行文件、实际参数与结束状态；不执行镜像。"""
    receipt = strict_json(local_file(run, 'execution.json'))
    try:
        result = receipt['result']
        require(receipt['schema_version'] == 1 and receipt['suite'] == suite
                and receipt['kind'] in ('gsim', 'verilator') and receipt['state'] == 'terminated'
                and type(result['returncode']) is int and result['returncode'] == 0
                and result['timed_out'] is False and result['aborted'] is False
                and result['launch_error'] is None and receipt['finished_at'],
                '进程未成功退出')
        require(str(uuid.UUID(receipt['launch_id'])) == receipt['launch_id'], '无效的 launch ID')
        files = receipt['files']
        require(set(files) == set(RUNTIME_FILES) | ({'manifest'} if bin_sha is not None else set()),
                '运行文件身份不完整')
        for name, filename in RUNTIME_FILES.items():
            file_matches(local_file(run, filename), {'sha256': files[name]}, name)
        if bin_sha is not None:
            require(files['image'] == bin_sha, '镜像与 manifest 不符')
            file_matches(local_file(run, 'build-manifest.json'), {'sha256': files['manifest']},
                         '构建清单')
        emulator = receipt['emulator']
        emu = Path(emulator['realpath'])
        require(emu.is_absolute() and emu == emu.resolve() and emu.is_file(), '无效的 emulator 路径')
        common.dynamic_executable_info(emu)
        file_matches(emu, emulator, '模拟器')
        reference = local_file(run, 'reference.so')
        require(receipt['reference']['sha256'] == files['reference'], 'DiffTest 快照身份不符')
        common.shared_object_info(reference)
        common.validate_reference(reference)
        require(receipt['command'] == common.gsim_command(
            emu, reference, run / 'image.bin', run / 'dram.ini', run / 'dram',
            integer(receipt['seed'], 'seed')) and receipt['cwd'] == str(run / 'work'),
            '命令未绑定该模拟器、DiffTest 参考、配置和镜像')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError(f'运行回执字段缺失或无效：{exc}') from exc
    return receipt


def audit_diagnostics(stdout, stderr, footer):
    """只将 stdout 中 footer 后独立一行的 Core 0 trap 视作 RTL 结束。"""
    stdout, stderr = (common.ANSI.sub('', text) for text in (stdout, stderr))
    lines = stdout.split('\n')
    traps = [i for i, line in enumerate(lines[:-1]) if RTL_TRAP.fullmatch(line.removesuffix('\r'))]
    endings = [i for i, line in enumerate(lines) if line.startswith(footer)]
    require(len(traps) == 1 and len(endings) == 1 and endings[0] < traps[0],
            '缺少唯一、位于 guest footer 之后的 RTL Core 0 GOOD TRAP')
    for text in (stdout, stderr):
        # Guest 字段已由各自协议验证，不把候选或条目名当成宿主诊断。
        diagnostics = '\n'.join(line for line in text.split('\n') if not line.startswith((
            'P,', 'PAIR_HEADER,', 'PAIR_CASE,', 'PAIR_SEED,', 'PAIR_SKIP,', 'PAIR_WARN,',
            'PAIR_CHECK,', 'PAIR_END,', 'LAT,', 'LAT_HEADER,', 'LAT_CHECK,', 'LAT_CHAIN,', 'LAT_END,')))
        require(not DIFF_DISABLED.search(diagnostics), '日志显示禁用了 DiffTest')
        require(not FATAL.search(diagnostics), '日志包含 guest/simulator 失败')
        errors = common.error_markers(diagnostics)
        require(not errors, '日志包含失败：' + ', '.join(errors[:4]))


def audit_run(run):
    run = run.expanduser().resolve(strict=True)
    manifest_path = local_file(run, 'build-manifest.json')
    corpus = run / 'corpus'
    require(corpus.is_dir() and not corpus.is_symlink(), '缺少独立语料快照目录')
    manifest = strict_json(manifest_path)
    plan = load_plan(manifest, headers=corpus)
    receipt = audit_rtl(run, 'libmvec-paired', bin_sha=plan.bin_info['sha256'])
    require((run / 'image.bin').stat().st_size == plan.bin_info['bytes'], '镜像长度不符')
    texts = []
    for name in ('stdout.log', 'stderr.log'):
        with local_file(run, name).open(encoding='utf-8', errors='replace', newline='') as stream:
            texts.append(stream.read())
    stdout, stderr = texts
    require(not any(RECORD.match(line) for line in common.ANSI.sub('', stderr).split('\n')),
            'stderr 中出现重复或错流的 guest 记录')
    points, warns, checks = parse_wire(StringIO(stdout, newline='\n'), plan)
    audit_diagnostics(stdout, stderr, 'PAIR_END,')
    return plan, points, warns, checks, receipt


def phase(round_):
    return 'first' if round_ == 0 else 'warmup' if round_ < 3 else 'steady'


def quality(cycles, k):
    """用整数等价式比较样本 CV 与 1%，避免边界被舍入误判。"""
    n = len(cycles)
    if not k:
        return 'control', None
    if n < MIN_STEADY:
        return 'insufficient-steady', None
    total = sum(cycles)
    numerator = n * sum(v * v for v in cycles) - total * total
    denominator = (n - 1) * total * total
    cv = math.sqrt(Fraction(n * numerator, denominator)) if denominator else None
    if min(cycles) < WINDOW_MIN:
        return 'short-window', cv
    if not denominator or 10000 * n * numerator > denominator:
        return 'unstable', cv
    return 'eligible', cv


def fit(values):
    # 分别归一化纵坐标和横坐标；同一点 R 固定，不同 K 的 R 可不同。
    n = len(values)
    sx = sum(x for x, _ in values)
    sy = sum(y for _, y in values)
    denom = n * sum(x * x for x, _ in values) - sx * sx
    slope = (n * sum(x * y for x, y in values) - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    residual = max(abs(y - (slope * x + intercept)) / y for x, y in values)
    return map(float, (slope, intercept, residual))


def report(output, plan, points, warns, checks, receipt):
    """写每轮、每点和拟合结果；只对单次已审计运行判定计时质量。"""
    out = new_output(output)
    groups = {}
    with (out / 'samples.csv').open('x', newline='') as s, (out / 'cells.csv').open('x', newline='') as c:
        samples, cells = csv.writer(s), csv.writer(c)
        columns = ('launch_id', 'case', 'function', 'mode', 'N', 'L', 'B', 'K', 'S', 'R')
        samples.writerow((*columns, 'round', 'phase', 'calls', 'window_cycles'))
        cells.writerow((*columns, 'steady_windows', 'min_window_cycles', 'median_window_cycles',
                        'median_cycles_per_call', 'sample_cv', 'quality', 'auto_estimated_cycles'))
        for key in plan.points:
            point = points[key]
            idx, mode, n, batch, k, stream = key
            case = plan.cases[idx]
            base = (receipt['launch_id'], case['case'], case['function'], mode, n, case['lanes'], batch)
            row = (*base, k, stream, point.repeat)
            calls = plan.points[key] * point.repeat
            steady = []
            for round_, cycles in sorted(point.cycles.items()):
                samples.writerow((*row, round_, phase(round_), calls, cycles))
                if round_ >= 3:
                    steady.append(cycles)
            status, cv = quality(steady, k)
            med = median([Fraction(v) for v in steady]) if steady else None
            if status == 'eligible':
                group = groups.setdefault((*base, stream), [])
                group.append((plan.points[key], med / point.repeat))
            cells.writerow((*row, len(steady), min(steady) if steady else None,
                            float(med) if med is not None else None,
                            float(med / calls) if med is not None and calls else None,
                            cv, status, warns.get(key)))
    with (out / 'fit.csv').open('x', newline='') as f:
        writer = csv.writer(f)
        writer.writerow((*columns[:7], 'S', 'points', 'cycles_per_call_slope',
                         'unit_cycles_intercept', 'worst_relative_residual'))
        for key, values in groups.items():
            if len(values) >= 2:
                writer.writerow((*key, len(values), *fit(values)))
    with (out / 'acceptance.csv').open('x', newline='') as a:
        writer = csv.writer(a)
        writer.writerow(('launch_id', 'kind', 'status', 'checks', 'windows', 'scope',
                         'manifest_sha256', 'image_sha256', 'emulator_sha256',
                         'reference_sha256', 'dram_sha256', 'seed'))
        files = receipt['files']
        writer.writerow((receipt['launch_id'], receipt['kind'], 'complete', checks,
                         sum(len(p.cycles) for p in points.values()), 'single-launch',
                         files['manifest'], files['image'], receipt['emulator']['sha256'],
                         files['reference'], files['dram'], receipt['seed']))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', required=True, type=Path, help='构建器的冻结配对清单')
    rtl_arguments(ap)
    args = ap.parse_args(argv)
    try:
        manifest_path = args.manifest.expanduser().resolve(strict=True)
        original_sha = common.sha256(manifest_path)
        manifest = strict_json(manifest_path)
        plan = load_plan(manifest)
        image = Path(plan.bin_info['path']).resolve(strict=True)
        require(image.stat().st_size == plan.bin_info['bytes'], 'BIN 长度与 manifest 不符')
        run = launch_rtl(args, image, plan.bin_info,
                         build=(manifest_path, original_sha, manifest['data']['headers']))
        _, points, _, checks, _ = audit_run(run)
        print(f'run=complete windows={sum(len(p.cycles) for p in points.values())} checks={checks}')
    except (OSError, ValueError) as exc:
        print(f'PAIR_REJECTED {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
