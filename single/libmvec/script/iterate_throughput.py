#!/usr/bin/env python3
"""吞吐的快速迭代流水线：每个测量模式一个镜像，R 自动挑选并可自动重试。

一个 (函数, 模式) 建一个只含该模式的镜像，``R`` 按「窗口目标 ÷ (N·K·预期周期
每元素)」算出，所以每个镜像的稳态窗都落在同一量级（默认约 30,000 原始周期）。
预期周期来自三处，优先级由高到低：

1. ``--r-cache`` 上一次运行实测值（跑一次就自动记住，实现变快变慢都会跟随）；
2. 内置的已验收实测表（首次运行、没有缓存时用）；
3. 兜底常数。

若某个镜像的稳态窗仍低于协议下限，``--retry-short-window`` 会按实测窗口把 R
放大后自动重建重跑（默认 1 次），不需要人工调参。

用途：实现迭代时判断「这一版吞吐是否退化」；对外数值仍以 ``--profile full``
（R=32、正式档）为准。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
STAMP = '%Y%m%d-%H%M%S'

WINDOW_MIN = 20_000
MIN_STEADY = 7
CV_LIMIT = 1e-2

PROFILES = {'fast': 'adaptive', 'full': 32}
TARGET_WINDOW = 30_000

DEFAULT_MODES = ('scalar_strict', 'scalar_fast',
                 'auto_m1', 'auto_m2', 'auto_m4', 'auto_m8',
                 'direct_m1', 'direct_m2', 'direct_m4', 'direct_m8')

# 已安装库的周期/元素实测值（data/latest-gcc-split-modes），缓存缺失时使用。
CPE = {
    ('sin', 'scalar_strict'): 92.85, ('sin', 'scalar_fast'): 92.85,
    ('sin', 'auto_m1'): 66.12, ('sin', 'auto_m2'): 40.25,
    ('sin', 'auto_m4'): 20.62, ('sin', 'auto_m8'): 20.49,
    ('sin', 'direct_m1'): 66.27, ('sin', 'direct_m2'): 40.06,
    ('sin', 'direct_m4'): 20.68, ('sin', 'direct_m8'): 20.18,
    ('exp', 'scalar_strict'): 9.32, ('exp', 'scalar_fast'): 9.32,
    ('exp', 'auto_m1'): 44.43, ('exp', 'auto_m2'): 24.34,
    ('exp', 'auto_m4'): 17.35, ('exp', 'auto_m8'): 30.53,
    ('exp', 'direct_m1'): 44.36, ('exp', 'direct_m2'): 24.40,
    ('exp', 'direct_m4'): 17.43, ('exp', 'direct_m8'): 30.47,
}
DEFAULT_CPE = 45.0


def log(message: str) -> None:
    print(message, flush=True)


def run(cmd: list[str], log_path: Path, label: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = log_path.with_suffix('.stdout')
    err = log_path.with_suffix('.stderr')
    started = time.time()
    with out.open('w') as fo, err.open('w') as fe:
        rc = subprocess.call([str(part) for part in cmd], stdout=fo, stderr=fe)
    elapsed = time.time() - started
    if rc != 0:
        tail = err.read_text(errors='replace').strip().splitlines()[-6:]
        raise RuntimeError(f'{label}: 退出码 {rc}，日志 {err}\n  ' + '\n  '.join(tail))
    log(f'  {label}: {elapsed:.0f}s')


def pick_r(n: int, k: int, cpe: float, target: int = TARGET_WINDOW) -> int:
    """按窗口目标反推 R：window ≈ n·k·R·cpe。"""
    return max(1, min(64, math.ceil(target / max(n * k * cpe, 1e-9))))


def bump_r(current: int, window: int, target: int = TARGET_WINDOW) -> int:
    """窗口不足时按实测比例放大 R，留 20% 余量。"""
    if window <= 0:
        return min(64, current * 2)
    return min(64, max(current + 1, math.ceil(current * target / window * 1.2)))


def load_cache(path: Path | None) -> dict[str, float]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {str(key): float(value) for key, value in data.items()
            if isinstance(value, (int, float)) and value > 0}


def save_cache(path: Path | None, cache: dict[str, float]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(cache.items())), indent=2) + '\n')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description='吞吐快速迭代：逐模式建镜像 → NEMU 预检 → GSIM → 汇总。',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prefix', required=True, help='工具链安装前缀')
    ap.add_argument('--data-dir', required=True, help='冻结输入目录')
    ap.add_argument('--case', default='sin,exp', help='逗号分隔的函数名')
    ap.add_argument('--modes', default=','.join(DEFAULT_MODES),
                    help='逗号分隔的测量模式')
    ap.add_argument('--emu', required=True, help='动态链接的 gsim 可执行文件')
    ap.add_argument('--reference', required=True, help='DiffTest 参考共享库')
    ap.add_argument('--dramsim3-ini', required=True, help='DRAMSim3 配置')
    ap.add_argument('--profile', choices=sorted(PROFILES), default='fast',
                    help='fast=自适应 R（默认）；full=R32 正式档')
    ap.add_argument('--target-window', type=int, default=TARGET_WINDOW,
                    help=f'fast 档的窗口目标原始周期（默认 {TARGET_WINDOW}）')
    ap.add_argument('--r-cache', default=None,
                    help='R 反馈缓存文件：存在则读实测周期，运行结束回写')
    ap.add_argument('--retry-short-window', type=int, default=1,
                    help='窗口不足时自动放大 R 重跑的次数（默认 1）')
    ap.add_argument('--jobs', type=int, default=10, help='并行镜像数（默认 10）')
    ap.add_argument('--lengths', default='128', help='N（单值，默认 128）')
    ap.add_argument('--batches', default='1', help='K（单值，默认 1）')
    ap.add_argument('--max-lmul', choices=('2', '8'), default='8')
    ap.add_argument('--output', required=True,
                    help='输出目录（必须不存在；建议带日期时间）')
    ap.add_argument('--precheck', choices=('nemu', 'none'), default='nemu')
    ap.add_argument('--pair-check', action='store_true',
                    help='打印自动向量化相对直接调用的吞吐差')
    ap.add_argument('--timeout', default='24h', help='单个镜像的 GSIM 时限')
    ap.add_argument('--seed', default='123456789')
    return ap.parse_args(argv)


def build_unit(args, out: Path, unit: dict, attempt: int) -> dict:
    """建一个只含该模式的镜像，返回 {manifest, base, case, r}。"""
    name = f'{unit["function"]}-{unit["mode"]}'
    target = out / 'build' / f'{name}-a{attempt}'
    argv = [PY, '-B', str(HERE / 'build.py'),
            '--prefix', args.prefix, '--output', str(target),
            '--data-dir', args.data_dir, '--case', unit['function'],
            '--profile', 'grid', '--lengths', args.lengths,
            '--batches', args.batches, '--max-lmul', args.max_lmul,
            '--timing-repeat', str(unit['r']), '--split-modes',
            '--modes', unit['mode'], '--lto']
    run(argv, out / 'log' / f'{name}-a{attempt}-build', f'build {name}')
    manifest = json.loads((target / 'manifest.json').read_text())
    if len(manifest['cases']) != 1:
        raise RuntimeError(f'{name}: 期望一个用例，得到 {len(manifest["cases"])}')
    return {**unit, 'name': name, 'attempt': attempt, 'base': target,
            'manifest': target / 'manifest.json', 'case': manifest['cases'][0]}


def nemu_precheck(args, out: Path, unit: dict) -> None:
    binary = Path(unit['case']['bin'])
    if not binary.is_absolute():
        binary = unit['base'] / binary
    label = f'{unit["name"]}-a{unit["attempt"]}'
    wire = out / 'log' / f'{label}-nemu.wire'
    argv = [PY, '-B', str(Path(__file__).resolve()), '--precheck-one',
            str(binary), '--reference', args.reference, '--wire-out', str(wire)]
    for marker in unit['case'].get('required_markers', []):
        argv += ['--require', marker]
    run(argv, out / 'log' / f'{label}-nemu', f'nemu {label}')


def precheck_inprocess(image_path: Path, reference: str, wire_path: Path,
                       required: list[str]) -> None:
    """在独立进程内跑一遍镜像：正常退出、必需标记齐全、失败检查为 0。"""
    import ctypes as c

    image = image_path.read_bytes()

    class State(c.Structure):
        _fields_ = [('state', c.c_int), ('pc', c.c_uint64), ('ret', c.c_uint32)]

    ref = c.CDLL(reference)
    ref.difftest_memcpy_init.argtypes = [c.c_uint64, c.c_void_p, c.c_size_t, c.c_bool]
    ref.difftest_exec.argtypes = [c.c_uint64]
    st = State.in_dll(ref, 'nemu_state')
    cpu = (c.c_uint64 * 83).in_dll(ref, 'cpu')
    buf = c.create_string_buffer(image)
    ref.difftest_init()
    ref.difftest_memcpy_init(0x80000000, buf, len(image), True)
    if cpu[82] != 0x10000000:
        raise RuntimeError('入口 PC 不是 0x10000000')
    cpu[82] = 0x80000000
    uart = (c.c_ubyte * 16)()
    wire = bytearray()
    callback = c.CFUNCTYPE(None, c.c_uint32, c.c_int, c.c_bool)

    @callback
    def transmit(offset, _size, write):
        if write and offset == 4:
            wire.append(uart[4])

    ref.add_mmio_map.argtypes = [c.c_char_p, c.c_uint64, c.c_void_p, c.c_uint32,
                                 callback]
    ref.add_mmio_map(b'tx', 0x40600000, uart, len(uart), transmit)
    for _ in range(200_000_000):
        ref.difftest_exec(1)
        if st.state in (2, 3, 5):
            break
    text = wire.decode('ascii', 'replace')
    wire_path.write_text(text)
    if st.state != 2 or st.ret != 0:
        raise RuntimeError(f'NEMU 未正常退出 (state={st.state}, ret={st.ret})')
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(f'缺少标记: {missing[0]}')
    for line in text.splitlines():
        if line.startswith('LIBMVEC_CHECK') and ',failed=' in line:
            failed = line.split(',failed=')[1].split(',')[0]
            if failed != '0':
                raise RuntimeError(f'失败检查 {failed}: {line}')
    log('  ok')


def run_gsim(args, out: Path, unit: dict) -> Path:
    label = f'{unit["name"]}-a{unit["attempt"]}'
    rundir = out / 'runs' / f'{label}-{time.strftime(STAMP)}'
    argv = [PY, '-B', str(HERE / 'run.py'), 'gsim',
            '--manifest', str(unit['manifest']), '--output', str(rundir),
            '--jobs', '1', '--timeout', args.timeout,
            '--emu', args.emu, '--reference', args.reference,
            '--dramsim3-ini', args.dramsim3_ini, '--seed', args.seed,
            '--no-smoke']
    run(argv, out / 'log' / f'{label}-gsim', f'gsim {label}')
    return rundir


def collect_reports(args, out: Path, unit: dict, rundir: Path) -> Path:
    label = f'{unit["name"]}-a{unit["attempt"]}'
    argv = [PY, '-B', str(HERE / 'collect.py'), '--run', str(rundir),
            '--require-complete']
    run(argv, out / 'log' / f'{label}-collect', f'collect {label}')
    return rundir / 'reports' / 'samples.csv'


def phase_steady(round_index: int) -> bool:
    """与正式解析一致：前 3 轮是预热，之后 7 轮为 steady。"""
    return round_index >= 3


def aggregate(samples: Path, function: str) -> list[dict]:
    """按模式归并 7 个 steady 窗口，给出周期/元素与质量。"""
    rows = [row for row in csv.DictReader(samples.open())
            if row['function'] == function]
    result = []
    for mode in sorted({row['mode'] for row in rows}):
        cells = [row for row in rows if row['mode'] == mode]
        if not cells or cells[0]['record_type'] != 'sample':
            continue
        steady = [row for row in cells if phase_steady(int(row['r']))]
        n, k = int(cells[0]['n']), int(cells[0]['k'])
        repeat = int(cells[0]['timing_repeat'] or '1')
        cycles = [int(row['cycles']) for row in steady]
        mean = statistics.fmean(cycles) if cycles else 0.0
        median = statistics.median(cycles) if cycles else 0.0
        cv = statistics.stdev(cycles) / mean if len(cycles) > 1 and mean else 0.0
        result.append(dict(
            function=function, mode=mode, n=n, k=k, r=repeat,
            steady_windows=len(cycles),
            min_window_cycles=min(cycles) if cycles else 0,
            median_window_cycles=median,
            cycles_per_element=median / repeat / (n * k) if cycles else 0.0,
            sample_cv=cv,
            passed=(all(row['run_status'] == 'passed' and
                        row['case_status'] == 'passed' and
                        row['measurement_valid'].lower() == 'true'
                        for row in cells))))
    return result


def gate(row: dict) -> str:
    problems = []
    if row['steady_windows'] < MIN_STEADY:
        problems.append(f'稳态窗 {row["steady_windows"]} < {MIN_STEADY}')
    if row['min_window_cycles'] < WINDOW_MIN:
        problems.append(f'窗口 {row["min_window_cycles"]} < {WINDOW_MIN}')
    if row['sample_cv'] > CV_LIMIT:
        problems.append(f'CV {row["sample_cv"]:.3%} > {CV_LIMIT:.0%}')
    if not row['passed']:
        problems.append('运行或测量未通过')
    return '; '.join(problems) if problems else 'ok'


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--precheck-one' in argv:
        ap = argparse.ArgumentParser(description='单镜像 NEMU 预检（内部使用）')
        ap.add_argument('--precheck-one', required=True)
        ap.add_argument('--reference', required=True)
        ap.add_argument('--wire-out', required=True)
        ap.add_argument('--require', action='append', default=[])
        single = ap.parse_args(argv)
        precheck_inprocess(Path(single.precheck_one), single.reference,
                           Path(single.wire_out), single.require)
        return 0

    args = parse_args(argv)
    functions = [name for name in args.case.split(',') if name.strip()]
    modes = [name for name in args.modes.split(',') if name.strip()]
    n, k = int(args.lengths), int(args.batches)
    out = Path(args.output)
    if out.exists():
        raise SystemExit(f'--output 已存在: {out}')
    for sub in ('build', 'log', 'runs'):
        (out / sub).mkdir(parents=True)

    cache_path = Path(args.r_cache) if args.r_cache else None
    cache = load_cache(cache_path)

    def r_of(function: str, mode: str) -> int:
        if PROFILES[args.profile] != 'adaptive':
            return int(PROFILES[args.profile])
        cpe = cache.get(f'{function}|{mode}',
                        CPE.get((function, mode), DEFAULT_CPE))
        return pick_r(n, k, cpe, args.target_window)

    plan = [dict(function=fn, mode=mode, r=r_of(fn, mode))
            for fn in functions for mode in modes]
    log(f'profile={args.profile} 单元={len(plan)} jobs={args.jobs} '
        f'缓存={"有" if cache else "无"}')

    rows: dict[tuple[str, str], dict] = {}
    attempt = 0
    while True:
        started = time.time()
        with futures.ThreadPoolExecutor(max_workers=min(args.jobs, len(plan))) as pool:
            built = list(pool.map(lambda u: build_unit(args, out, u, attempt), plan))
        log(f'建镜像完成 {time.time() - started:.0f}s（第 {attempt + 1} 轮）')

        if args.precheck == 'nemu':
            with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                list(pool.map(lambda unit: nemu_precheck(args, out, unit), built))

        started = time.time()
        with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            runs = list(pool.map(lambda unit: run_gsim(args, out, unit), built))
        log(f'GSIM 完成 {time.time() - started:.0f}s')

        with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            samples = list(pool.map(
                lambda pair: collect_reports(args, out, *pair), zip(built, runs)))
        for unit, path in zip(built, samples):
            for row in aggregate(path, unit['function']):
                row['r_planned'] = unit['r']
                rows[(row['function'], row['mode'])] = row
                if row['cycles_per_element'] > 0:
                    cache[f'{row["function"]}|{row["mode"]}'] = \
                        row['cycles_per_element']

        short = [(fn, mode) for (fn, mode), row in rows.items()
                 if row['min_window_cycles'] < WINDOW_MIN]
        if not short or attempt >= args.retry_short_window:
            break
        attempt += 1
        plan = [dict(function=fn, mode=mode,
                     r=bump_r(rows[(fn, mode)]['r'],
                              rows[(fn, mode)]['min_window_cycles'],
                              args.target_window))
                for fn, mode in short]
        log(f'窗口不足 {len(plan)} 个单元，放大 R 后重跑：'
            + ', '.join(f'{u["function"]}-{u["mode"]}→R{u["r"]}' for u in plan))

    save_cache(cache_path, cache)

    ordered = sorted(rows.values(), key=lambda r: (r['function'], r['mode']))
    summary = out / 'summary.csv'
    fields = ['function', 'mode', 'n', 'k', 'r', 'r_planned', 'steady_windows',
              'min_window_cycles', 'median_window_cycles',
              'cycles_per_element', 'sample_cv', 'gate']
    with summary.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in ordered:
            writer.writerow({**{key: row[key] for key in fields[:-1]},
                             'gate': gate(row)})

    log('')
    log(f'{"函数":<6}{"模式":<16}{"R":>4}{"稳态窗":>7}{"最短窗":>10}'
        f'{"周期/元素":>12}{"CV":>9}  门限')
    for row in ordered:
        log(f'{row["function"]:<6}{row["mode"]:<16}{row["r"]:>4}'
            f'{row["steady_windows"]:>7}{row["min_window_cycles"]:>10}'
            f'{row["cycles_per_element"]:>12.4f}{row["sample_cv"]:>9.3%}  '
            f'{gate(row)}')

    if args.pair_check:
        log('')
        by = {(row['function'], row['mode']): row['cycles_per_element']
              for row in ordered}
        for fn in functions:
            for lmul in ('1', '2', '4', '8'):
                auto = by.get((fn, f'auto_m{lmul}'))
                direct = by.get((fn, f'direct_m{lmul}'))
                if auto and direct:
                    log(f'{fn} m{lmul}: 自动/直接 = '
                        f'{100 * (auto - direct) / direct:+.2f}% '
                        f'({auto:.4f} vs {direct:.4f})')

    failures = [f'{row["function"]}-{row["mode"]}: {gate(row)}' for row in ordered
                if gate(row) != 'ok']
    log('')
    log(f'汇总: {summary}')
    if failures:
        log('门限未过:')
        for item in failures:
            log(f'  - {item}')
        return 1
    log('门限: 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
