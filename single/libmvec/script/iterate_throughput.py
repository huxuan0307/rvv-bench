#!/usr/bin/env python3
"""吞吐的快速迭代流水线：建单模式镜像 → NEMU 预检 → GSIM → 汇总表。

一个函数建一份 ``--split-modes`` 清单（每个测量模式一个镜像），镜像之间并行
仿真，因此墙钟由最慢的镜像决定。``--profile fast`` 把 ``R`` 从 32 降到 20
（窗口仍不少于 20,000 原始周期，仍是 10 轮中的 7 个 steady），``--profile full``
即正式档。

用途：实现迭代时判断「这一版吞吐是否退化」；对外数值仍以正式档为准。

最小命令：
  iterate_throughput.py --prefix /工具链 --data-dir DATA --case sin \\
    --emu GSIM --reference REF.SO --dramsim3-ini dram.ini \\
    --profile fast --jobs 10 --output OUT

限制：只覆盖已安装库的 grid 档与 sin/exp 单模式镜像；不改变协议口径。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PY = sys.executable

# R=32 是正式档；R=20 仍能让最慢的单元格（exp 标量约 9.3 周期/元素）
# 保住 128*20*9.3 ≈ 23,800 原始周期，高于 20,000 门限。
PROFILES = {'fast': 20, 'full': 32}
WINDOW_MIN = 20_000
MIN_STEADY = 7
CV_LIMIT = 1e-2
STAMP = '%Y%m%d-%H%M%S'


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description='吞吐快速迭代：建镜像 → NEMU 预检 → GSIM → 汇总。',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prefix', required=True, help='工具链安装前缀')
    ap.add_argument('--data-dir', required=True, help='冻结输入目录')
    ap.add_argument('--case', default='sin,exp', help='逗号分隔的函数名')
    ap.add_argument('--emu', required=True, help='动态链接的 gsim 可执行文件')
    ap.add_argument('--reference', required=True, help='DiffTest 参考共享库')
    ap.add_argument('--dramsim3-ini', required=True, help='DRAMSim3 配置')
    ap.add_argument('--profile', choices=sorted(PROFILES), default='fast',
                    help='fast=R20（默认，迭代用）；full=R32（正式档）')
    ap.add_argument('--jobs', type=int, default=10,
                    help='并行镜像数（默认 10）；单函数共 10 个镜像')
    ap.add_argument('--lengths', default='128', help='N 列表，默认 128')
    ap.add_argument('--batches', default='1', help='K 列表，默认 1')
    ap.add_argument('--max-lmul', choices=('2', '8'), default='8')
    ap.add_argument('--output', required=True,
                    help='输出目录（必须不存在；建议带日期时间）')
    ap.add_argument('--precheck', choices=('nemu', 'none'), default='nemu')
    ap.add_argument('--pair-check', action='store_true',
                    help='打印自动向量化相对直接调用的吞吐差')
    ap.add_argument('--timeout', default='24h', help='单个镜头的 GSIM 时限')
    ap.add_argument('--seed', default='123456789')
    return ap.parse_args(argv)


def build_manifest(args, out: Path, function: str) -> dict:
    """建一个函数的单模式清单：每个测量模式一个镜像。"""
    target = out / 'build' / function
    argv = [PY, '-B', str(HERE / 'build.py'),
            '--prefix', args.prefix, '--output', str(target),
            '--data-dir', args.data_dir, '--case', function,
            '--profile', 'grid', '--lengths', args.lengths,
            '--batches', args.batches, '--max-lmul', args.max_lmul,
            '--timing-repeat', str(PROFILES[args.profile]),
            '--split-modes', '--lto']
    run(argv, out / 'log' / f'{function}-build', f'build {function}')
    manifest = json.loads((target / 'manifest.json').read_text())
    return dict(function=function, dir=target,
                manifest=target / 'manifest.json',
                cases=manifest['cases'])


def nemu_precheck(args, out: Path, base: Path, case: dict) -> None:
    """每个镜像单开一个进程预检：参考模型在同一进程内不能重复执行。"""
    binary = Path(case['bin'])
    if not binary.is_absolute():
        binary = base / binary
    wire = out / 'log' / f'{case["name"]}-nemu.wire'
    argv = [PY, '-B', str(Path(__file__).resolve()), '--precheck-one',
            str(binary), '--reference', args.reference, '--wire-out', str(wire)]
    for marker in case.get('required_markers', []):
        argv += ['--require', marker]
    run(argv, out / 'log' / f'{case["name"]}-nemu', f'nemu {case["name"]}')


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


SHARD = 4  # run.py 的 --jobs 上限


def run_gsim(args, out: Path, unit: dict) -> list[Path]:
    """把一个函数的镜像按 4 个一片切开，各片并行仿真。"""
    names = [case['name'] for case in unit['cases']]
    shards = [names[i:i + SHARD] for i in range(0, len(names), SHARD)]
    stamp = time.strftime(STAMP)
    runs: list[Path] = []
    argv_list = []
    for index, shard in enumerate(shards):
        rundir = out / 'runs' / f'{unit["function"]}-{stamp}-{index + 1}'
        argv = [PY, '-B', str(HERE / 'run.py'), 'gsim',
                '--manifest', str(unit['manifest']), '--output', str(rundir),
                '--jobs', str(min(len(shard), SHARD)),
                '--timeout', args.timeout,
                '--emu', args.emu, '--reference', args.reference,
                '--dramsim3-ini', args.dramsim3_ini, '--seed', args.seed,
                '--no-smoke']
        for name in shard:
            argv += ['--case', name]
        argv_list.append((argv, rundir, index))
        runs.append(rundir)

    def one(item):
        argv, rundir, index = item
        run(argv, out / 'log' / f'{unit["function"]}-gsim-{index + 1}',
            f'gsim {unit["function"]}-{index + 1}')
        return rundir

    with futures.ThreadPoolExecutor(max_workers=len(argv_list)) as pool:
        list(pool.map(one, argv_list))
    return runs


def collect_reports(args, out: Path, unit: dict) -> list[Path]:
    """逐片采集，返回每片的 samples.csv。"""
    samples = []

    def one(item):
        index, rundir = item
        argv = [PY, '-B', str(HERE / 'collect.py'), '--run', str(rundir),
                '--require-complete']
        run(argv, out / 'log' / f'{unit["function"]}-collect-{index + 1}',
            f'collect {unit["function"]}-{index + 1}')
        samples.append(rundir / 'reports' / 'samples.csv')

    items = list(enumerate(unit['runs']))
    with futures.ThreadPoolExecutor(max_workers=len(items)) as pool:
        list(pool.map(one, items))
    return samples


def phase_steady(round_index: int) -> bool:
    """与正式解析一致：前 3 轮是预热，之后 7 轮为 steady。"""
    return round_index >= 3


def aggregate(samples: Path, function: str) -> list[dict]:
    """按 (模式) 归并 7 个 steady 窗口，给出周期/元素与质量。"""
    rows = [row for row in csv.DictReader(samples.open())]
    rows = [row for row in rows if row['function'] == function]
    modes = sorted({row['mode'] for row in rows})
    result = []
    for mode in modes:
        cells = [row for row in rows if row['mode'] == mode]
        if not cells:
            continue
        if cells[0]['record_type'] != 'sample':
            # rdcycle / empty / copy 是对照列，不是被测吞吐。
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
    out = Path(args.output)
    if out.exists():
        raise SystemExit(f'--output 已存在: {out}')
    for sub in ('build', 'log', 'runs'):
        (out / sub).mkdir(parents=True)

    log(f'profile={args.profile} R={PROFILES[args.profile]} jobs={args.jobs} '
        f'函数={",".join(functions)}')
    started = time.time()
    with futures.ThreadPoolExecutor(max_workers=len(functions)) as pool:
        units = list(pool.map(lambda fn: build_manifest(args, out, fn), functions))
    log(f'建镜像完成 {time.time() - started:.0f}s')

    if args.precheck == 'nemu':
        jobs = [(unit['dir'], case) for unit in units for case in unit['cases']]
        with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(lambda job: nemu_precheck(args, out, *job), jobs))

    started = time.time()
    for unit in units:
        unit['runs'] = run_gsim(args, out, unit)
    log(f'GSIM 完成 {time.time() - started:.0f}s')

    rows: list[dict] = []
    for unit in units:
        for samples in collect_reports(args, out, unit):
            rows.extend(aggregate(samples, unit['function']))

    summary = out / 'summary.csv'
    fields = ['function', 'mode', 'n', 'k', 'r', 'steady_windows',
              'min_window_cycles', 'median_window_cycles',
              'cycles_per_element', 'sample_cv', 'gate']
    with summary.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r['function'], r['mode'])):
            writer.writerow({**{key: row[key] for key in fields[:-1]},
                             'gate': gate(row)})

    log('')
    log(f'{"函数":<6}{"模式":<16}{"稳态窗":>7}{"最短窗":>10}{"周期/元素":>12}{"CV":>9}  门限')
    for row in sorted(rows, key=lambda r: (r['function'], r['mode'])):
        log(f'{row["function"]:<6}{row["mode"]:<16}{row["steady_windows"]:>7}'
            f'{row["min_window_cycles"]:>10}{row["cycles_per_element"]:>12.4f}'
            f'{row["sample_cv"]:>9.3%}  {gate(row)}')

    if args.pair_check:
        log('')
        by = {(row['function'], row['mode']): row['cycles_per_element']
              for row in rows}
        for fn in functions:
            for lmul in ('1', '2', '4', '8'):
                auto = by.get((fn, f'auto_m{lmul}'))
                direct = by.get((fn, f'direct_m{lmul}'))
                if auto and direct:
                    log(f'{fn} m{lmul}: 自动/直接 = '
                        f'{100 * (auto - direct) / direct:+.2f}% '
                        f'({auto:.4f} vs {direct:.4f})')

    failures = [f'{row["function"]}-{row["mode"]}: {gate(row)}'
                for row in rows if gate(row) != 'ok']
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
