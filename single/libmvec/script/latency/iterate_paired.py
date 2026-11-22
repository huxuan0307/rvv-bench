#!/usr/bin/env python3
"""配对延迟的快速迭代流水线：建镜像 → NEMU 预检 → GSIM → 汇总表。

一个 (函数, 探针) 建一个独立镜像并并行推进，因此墙钟时间由最慢的单元决定，
而不是所有用例之和。``--profile fast`` 把每个窗口压到刚过协议下限
（K=1、固定 R、仍是 8 个稳态窗口），墙钟约为 ``full`` 的 1/4。

用途：libmvec 实现迭代时判断「这一版能不能接受」，以及自动向量化链与手写链
是否仍然一致（``--pair-check``）。正式证据仍用 ``--profile full`` 复核。

最小命令：
  iterate_paired.py --prefix /工具链 --data-dir DATA --header-sha-file SHA \\
    --emu GSIM --reference REF.SO --dramsim3-ini dram.ini \\
    --functions sin,exp --probes m1_scalable_unmasked,m1_auto \\
    --profile fast --output OUT

限制：只覆盖配对（延迟）协议；吞吐走 build.py/run.py/collect.py。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PY = sys.executable

# 迭代档位：fast 只压窗口，不减少稳态窗口数，也不改协议口径。
PROFILES = {
    # 快档交给构建器的 auto：预测窗按 40,000 周期目标自选 R，实现变化无需改表。
    'fast': dict(rounds=8, k='1', streams='1', timing_repeat='auto',
                 lengths='128', batches='1'),
    'full': dict(rounds=8, k='1,2', streams='1', timing_repeat='auto',
                 lengths='128', batches='1'),
}

WINDOW_MIN = 20_000
CV_LIMIT = 1e-2
MIN_STEADY = 5


def log(message: str) -> None:
    print(message, flush=True)


def run(cmd: list[str], log_path: Path, label: str) -> None:
    """运行一个子进程，stdout/stderr 分开落盘；失败时抛出并保留日志。"""
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
        description='配对延迟快速迭代：建镜像 → NEMU 预检 → GSIM → 汇总。',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prefix', required=True, help='工具链安装前缀')
    ap.add_argument('--data-dir', required=True, help='冻结输入目录（含 <函数>.h）')
    ap.add_argument('--header-sha-file', default=None, help='冻结输入 SHA256SUMS')
    ap.add_argument('--emu', required=True, help='动态链接的 gsim 可执行文件')
    ap.add_argument('--reference', required=True, help='DiffTest 参考共享库')
    ap.add_argument('--dramsim3-ini', required=True, help='DRAMSim3 配置')
    ap.add_argument('--functions', default='sin,exp',
                    help='逗号分隔的函数名（配对支持的函数）')
    ap.add_argument('--probes', default='m1_scalable_unmasked,m1_auto',
                    help='逗号分隔的探针名；可用 m1_auto 表示自动向量化链')
    ap.add_argument('--profile', choices=sorted(PROFILES), default='fast')
    ap.add_argument('--jobs', type=int, default=4, help='并行单元数（默认 4）')
    ap.add_argument('--output', required=True,
                    help='输出目录（必须不存在；建议带日期时间）')
    ap.add_argument('--precheck', choices=('nemu', 'none'), default='nemu',
                    help='建镜像后是否用参考模型先跑一遍协议预检')
    ap.add_argument('--pair-check', action='store_true',
                    help='同时比较手写链与自动向量化链，并报告相对差')
    ap.add_argument('--timeout', default='2h', help='单个 GSIM 单元的时限')
    ap.add_argument('--seed', default='123456789', help='DiffTest 种子')
    return ap.parse_args(argv)


def profile_flags(profile: str) -> list[str]:
    spec = PROFILES[profile]
    return ['--rounds', str(spec['rounds']), '--paired-k', spec['k'],
            '--streams', spec['streams'],
            '--timing-repeat', spec['timing_repeat'],
            '--lengths', spec['lengths'], '--batches', spec['batches']]


def build_image(args, out: Path, function: str, probe: str) -> dict:
    """建一个只含该 (函数, 探针) 的镜像，返回镜像目录与清单信息。"""
    image = out / 'build' / f'{function}-{probe}'
    argv = [PY, '-B', str(HERE / 'build_latency.py'),
            '--protocol', 'paired',
            '--prefix', args.prefix,
            '--output', str(image),
            '--data-dir', args.data_dir,
            '--functions', function,
            '--only-case', probe,
            '--modes', 'latency',
            '--vlen', '128',
            '--serial', '1',
            '--tag', f'{function}-{probe}',
            *profile_flags(args.profile)]
    if args.header_sha_file:
        argv += ['--header-sha-file', args.header_sha_file]
    # 配对构建默认开 LTO；保持与已验收测量一致。
    argv.append('--lto')
    run(argv, out / 'log' / f'{function}-{probe}-build', 'build')
    manifest = json.loads((image / 'manifest.json').read_text())
    bins = [name for name in manifest['artifacts'] if name.endswith('.bin')]
    return dict(function=function, probe=probe, image=image,
                manifest=image / 'manifest.json', bin=image / bins[0],
                manifest_sha=manifest['artifacts'][bins[0]]['sha256'])


def precheck_inprocess(image_path: Path, reference: str,
                      wire_path: Path) -> None:
    """在独立进程内用参考模型跑一遍镜像并校验协议收尾。"""
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
    # 同一进程内多次预检：参考模型的结束状态与指令计数必须显式复位。
    st.state = 0
    c.c_uint64.in_dll(ref, 'g_nr_guest_instr').value = 0
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
    if 'PAIR_END,status=ok' not in text:
        raise RuntimeError('协议收尾不是 ok')
    log('  ok')


def nemu_precheck(args, out: Path, unit: dict) -> None:
    """每个镜像单开一个进程预检：参考模型在同一进程内不能重复执行。"""
    wire = out / 'log' / f'{unit["function"]}-{unit["probe"]}-nemu.wire'
    argv = [PY, '-B', str(Path(__file__).resolve()), '--precheck-one',
            str(unit['bin']), '--reference', args.reference,
            '--wire-out', str(wire)]
    run(argv, out / 'log' / f'{unit["function"]}-{unit["probe"]}-nemu',
        f'nemu {unit["function"]}-{unit["probe"]}')


def run_gsim(args, out: Path, unit: dict) -> dict:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    rundir = out / 'runs' / f'{unit["function"]}-{unit["probe"]}-{stamp}'
    argv = [PY, '-B', str(HERE / 'run_paired.py'),
            '--manifest', str(unit['manifest']),
            '--kind', 'gsim',
            '--emu', args.emu,
            '--reference', args.reference,
            '--dramsim3-ini', args.dramsim3_ini,
            '--seed', args.seed,
            '--timeout', args.timeout,
            '--output', str(rundir)]
    run(argv, out / 'log' / f'{unit["function"]}-{unit["probe"]}-gsim', 'gsim')
    return {**unit, 'run': rundir}


def analyse(args, out: Path, unit: dict) -> list[dict]:
    target = out / 'analysis' / f'{unit["function"]}-{unit["probe"]}'
    argv = [PY, '-B', str(HERE / 'paired_parse.py'),
            '--run', str(unit['run']), '--out', str(target)]
    run(argv, out / 'log' / f'{unit["function"]}-{unit["probe"]}-parse', 'parse')
    with (target / 'cells.csv').open() as handle:
        return list(csv.DictReader(handle))


def gate(row: dict) -> str:
    problems = []
    if row.get('quality') != 'eligible':
        problems.append(f'quality={row.get("quality")}')
    if float(row['min_window_cycles']) < WINDOW_MIN:
        problems.append(f'窗口 {row["min_window_cycles"]} < {WINDOW_MIN}')
    if int(row['steady_windows']) < MIN_STEADY:
        problems.append(f'稳态窗口 {row["steady_windows"]} < {MIN_STEADY}')
    if float(row['sample_cv']) > CV_LIMIT:
        problems.append(f'CV {float(row["sample_cv"]):.3%} > {CV_LIMIT:.0%}')
    return '; '.join(problems) if problems else 'ok'


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--precheck-one' in argv:
        ap = argparse.ArgumentParser(description='单镜像 NEMU 预检（内部使用）')
        ap.add_argument('--precheck-one', required=True, help='镜像 BIN 路径')
        ap.add_argument('--reference', required=True, help='DiffTest 参考共享库')
        ap.add_argument('--wire-out', required=True, help='协议输出落盘路径')
        single = ap.parse_args(argv)
        precheck_inprocess(Path(single.precheck_one), single.reference,
                           Path(single.wire_out))
        return 0
    args = parse_args(argv)
    if args.jobs < 1:
        raise SystemExit('--jobs 必须 ≥ 1')
    functions = [name for name in args.functions.split(',') if name.strip()]
    probes = [name for name in args.probes.split(',') if name.strip()]
    out = Path(args.output)
    if out.exists():
        raise SystemExit(f'--output 已存在: {out}')
    for sub in ('build', 'log', 'runs', 'analysis'):
        (out / sub).mkdir(parents=True)

    units = [dict(function=fn, probe=probe) for fn in functions for probe in probes]
    log(f'profile={args.profile} jobs={args.jobs} 单元={len(units)}')

    started = time.time()
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        built = list(pool.map(lambda u: build_image(args, out, u['function'],
                                                   u['probe']), units))
    log(f'建镜像完成 {time.time() - started:.0f}s')

    if args.precheck == 'nemu':
        # 参考模型是进程内共享库，全局状态不能并发：预检串行执行（每单元数秒）。
        for unit in built:
            nemu_precheck(args, out, unit)

    started = time.time()
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        measured = list(pool.map(lambda u: run_gsim(args, out, u), built))
    log(f'GSIM 完成 {time.time() - started:.0f}s')

    rows: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for cells in pool.map(lambda u: analyse(args, out, u), measured):
            rows.extend(cells)
    for row in rows:
        parts = row['case'].split('|')
        row['probe'] = f'{parts[1]}_{parts[2]}_{parts[3]}'

    summary = out / 'summary.csv'
    fields = ['function', 'probe', 'case', 'K', 'R', 'steady_windows',
              'min_window_cycles', 'median_window_cycles',
              'median_cycles_per_call', 'sample_cv', 'quality', 'gate']
    with summary.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r['function'], r['probe'],
                                               int(r['K']))):
            writer.writerow({
                'function': row['function'], 'probe': row['probe'],
                'case': row['case'],
                'K': row['K'], 'R': row['R'],
                'steady_windows': row['steady_windows'],
                'min_window_cycles': row['min_window_cycles'],
                'median_window_cycles': row['median_window_cycles'],
                'median_cycles_per_call': row['median_cycles_per_call'],
                'sample_cv': row['sample_cv'], 'quality': row['quality'],
                'gate': gate(row)})

    log('')
    header = f'{"函数":<6}{"探针":<26}{"K":>2}{"稳态窗":>7}{"最短窗":>10}{"周期/调用":>12}{"CV":>9}  门限'
    log(header)
    for row in sorted(rows, key=lambda r: (r['function'], r['probe'], int(r['K']))):
        log(f'{row["function"]:<6}{row["probe"]:<26}{row["K"]:>2}'
            f'{row["steady_windows"]:>7}{row["min_window_cycles"]:>10}'
            f'{float(row["median_cycles_per_call"]):>12.4f}'
            f'{float(row["sample_cv"]):>9.3%}  {gate(row)}')

    if args.pair_check:
        log('')
        by_key = {(row['function'], row['K'], row['probe']):
                  float(row['median_cycles_per_call']) for row in rows}
        for fn in functions:
            for k in sorted({row['K'] for row in rows}, key=int):
                direct = by_key.get((fn, k, 'scalable_unmasked'))
                auto = by_key.get((fn, k, 'auto_unmasked'))
                if direct and auto:
                    log(f'{fn} K={k}: 自动/手写 = {100 * (auto - direct) / direct:+.2f}% '
                        f'({auto:.3f} vs {direct:.3f})')

    failures = [f'{row["function"]}-{row["probe"]} K={row["K"]}: {gate(row)}'
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
