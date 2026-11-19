#!/usr/bin/env python3
"""观察七函数直接入口的五种合法 frm；跨镜像比较，不宣称有向舍入正确。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
ROOT = HARNESS.parents[1]
ABI = HARNESS / 'tests/abi'
RUNTIME = HARNESS / 'runtime'
FUNCTIONS = ('log', 'exp', 'expf', 'logf', 'pow', 'sin', 'cos')
CASE_KEYS = ('fn', 'iface', 'm', 'rm', 'seed', 'pattern', 'shape')
VALUE_KEYS = CASE_KEYS + ('lane',)
DATE_DIR = re.compile(r'\d{8}[-_]\d{6}')
BAD = ('HIT BAD TRAP', 'HIT CRITICAL ERROR', 'DIFFTEST ERROR',
       'difftest check failed', 'NEMU exit with bad state')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
GOOD = re.compile(r'(?m)^.*\bnemu:\s+HIT GOOD TRAP\s+at pc\s*=\s*0x[0-9a-fA-F]+\s*$')
INPUT64 = (0x3fe8000000000000, 0x3ff0000000000000, 0x3ff8000000000000, 0x4000000000000000,
           0, 0x8000000000000000, 0xbff0000000000000, 1, 0x7ff0000000000000,
           0xfff0000000000000, 0x7ff8000000000001, 0x7ff0000000000001,
           0x3fefffffffffffff, 0x3ff0000000000001, 0x0010000000000000, 0x7fefffffffffffff)
INPUT32 = (0x3f400000, 0x3f800000, 0x3fc00000, 0x40000000, 0, 0x80000000,
           0xbf800000, 1, 0x7f800000, 0xff800000, 0x7fc00001, 0x7f800001,
           0x3f7fffff, 0x3f800001, 0x00800000, 0x7f7fffff)
COMMAND_FIELDS = ('label', 'command', 'cwd', 'status', 'stdout', 'stderr', 'tmpdir')
IDENTITY_FIELDS = ('role', 'path', 'sha256')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def parse_fields(line):
    result = {}
    for part in line.split()[1:]:
        if '=' not in part:
            raise ValueError(f'无效字段: {part}')
        key, value = part.split('=', 1)
        if not key or key in result:
            raise ValueError(f'字段重复或为空: {part}')
        result[key] = value
    return result


def read_commands(path, stdout, stderr):
    """必须同时给出真实同一次运行的命令、stdout 和 stderr 路径。"""
    with Path(path).open(newline='', encoding='utf-8') as f:
        records = list(csv.DictReader(f))
    nemu = [row for row in records if row.get('label') == 'nemu']
    if len(nemu) != 1 or nemu[0].get('status') != '0':
        raise ValueError('命令表必须有且仅有一个 rc=0 的 nemu 命令')
    row = nemu[0]
    if (Path(row['stdout']).resolve() != Path(stdout).resolve() or
            Path(row['stderr']).resolve() != Path(stderr).resolve()):
        raise ValueError('命令表与 NEMU stdout/stderr 路径不匹配')
    return records


def parse_observation(stdout, stderr, commands, function, *, legacy_vlenb=None):
    """区分 NEMU 控制输出及 UART；legacy_vlenb 仅用于旧版无配置行的日志。"""
    if function not in FUNCTIONS:
        raise ValueError('未知函数')
    nemu = [row for row in commands if row.get('label') == 'nemu']
    if len(nemu) != 1 or nemu[0].get('status') != '0':
        raise ValueError('缺少唯一成功的 NEMU 进程记录')
    clean_out, clean_err = ANSI.sub('', stdout), ANSI.sub('', stderr)
    if len(GOOD.findall(clean_out)) != 1:
        raise ValueError('NEMU stdout 缺少唯一有效 GOOD TRAP')
    if any(marker.lower() in (clean_out + '\n' + clean_err).lower() for marker in BAD):
        raise ValueError('NEMU 两流存在失败标记')
    if re.search(r'(?m)^FRM_(?:CASE|VALUE|COMPLETE|CONFIG|ERROR)\b', clean_out):
        raise ValueError('目标串口记录不能位于 NEMU stdout')
    cases, values, completion, config = {}, {}, [], []
    for line in clean_err.splitlines():
        if line.startswith('FRM_ERROR '):
            raise ValueError('直接调用观察检测到 frm/fflags 错误')
        if line.startswith(('FRM_CASE ', 'FRM_VALUE ', 'FRM_COMPLETE ', 'FRM_CONFIG ')):
            fields = parse_fields(line)
            if fields.get('fn') != function:
                raise ValueError('函数名不匹配')
            if line.startswith('FRM_COMPLETE '):
                completion.append(fields)
                continue
            if line.startswith('FRM_CONFIG '):
                config.append(fields)
                continue
            target, keys = (cases, CASE_KEYS) if line.startswith('FRM_CASE ') else (values, VALUE_KEYS)
            key = tuple(fields[k] for k in keys)
            if key in target:
                raise ValueError(f'重复记录 {key}')
            target[key] = fields
    if len(config) > 1:
        raise ValueError('VLENB 配置记录重复')
    if config:
        vlenb = int(config[0]['vlenb'])
        if legacy_vlenb is not None and legacy_vlenb != vlenb:
            raise ValueError('外部 VLENB 与日志配置冲突')
    elif legacy_vlenb is not None:
        vlenb = legacy_vlenb
    else:
        raise ValueError('缺少 VLENB 配置；旧日志须显式给出 --legacy-vlenb')
    width = 4 if function in ('expf', 'logf') else 8
    if vlenb < 16 or vlenb > 2048 or vlenb & (vlenb - 1) or 4 * vlenb // width + 1 > 512:
        raise ValueError('非法或超出探针缓冲区的 VLENB')
    if len(completion) != 1 or completion[0].get('status') != 'pass' or completion[0].get('failed') != '0':
        raise ValueError('缺失唯一成功完成标记')
    if int(completion[0]['cases']) != len(cases) or int(completion[0]['values']) != len(values):
        raise ValueError('记录数与完成标记不一致')
    expected = {(function, str(iface), str(m), str(rm), str(seed), str(pattern), str(shape))
                for iface in range(2) for m in (1, 2) for rm in range(5)
                for seed in range(2) for pattern in range(16) for shape in range(5 if iface else 1)}
    if set(cases) != expected:
        raise ValueError(f'case 覆盖不完整，缺失 {len(expected - cases.keys())}，多余 {len(cases.keys() - expected)}')
    expected_values = set()
    for key, row in cases.items():
        full = int(row['m']) * vlenb // width
        n = full if row['iface'] == '0' else (1, full-1, full, full+1, 2*full+1)[int(row['shape'])]
        if int(row['n']) != n or row['after'] != row['rm']:
            raise ValueError(f'长度或 frm 不符合预期: {key}')
        flags = int(row['flags'])
        int(row['errno'])
        if flags < 0 or flags > 31 or (int(row['seed']) and flags & 24 != 24):
            raise ValueError(f'非法 fflags 或已有 sticky 位丢失: {key}')
        expected_values.update(key + (str(i),) for i in range(n))
    if set(values) != expected_values:
        raise ValueError('lane 值记录不完整或多余')
    inputs = INPUT32 if width == 4 else INPUT64
    for key, row in values.items():
        pattern, lane = int(row['pattern']), int(row['lane'])
        if int(row['x']) != inputs[(pattern + lane) % 16] or int(row['z']) != inputs[(pattern + 3*lane + 1) % 16]:
            raise ValueError(f'lane 输入与已知样本不符: {key}')
        y = int(row['y'])
        if y < 0 or y >= 1 << (width * 8):
            raise ValueError(f'非法结果位模式: {key}')
    return cases, values, vlenb


def write_csv(path, fields, rows):
    with Path(path).open('x', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def atomic_run_csv(run, name, fields, rows):
    if name not in ('commands.csv', 'identities.csv'):
        raise ValueError('非法运行记录目标')
    # 临时文件也位于独立运行目录，目标名由上面的白名单限定。
    with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8', dir=run, delete=False) as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        temp = f.name
    os.replace(temp, run / name)


def compare(before, after, function, *, legacy_vlenb=None):
    before_cases, before_values, before_vlenb = parse_observation(*before, function, legacy_vlenb=legacy_vlenb)
    after_cases, after_values, after_vlenb = parse_observation(*after, function, legacy_vlenb=legacy_vlenb)
    if before_vlenb != after_vlenb or before_cases.keys() != after_cases.keys() or before_values.keys() != after_values.keys():
        raise ValueError('两个镜像的 VLENB、运行键或向量长度不同')
    groups = {(str(iface), str(m), str(rm)): dict(function=function, interface=('Nx' if iface == 0 else 'array'),
               lmul=m, frm=rm, cases=0, state_diff=0, value_count=0, value_diff=0)
              for iface in range(2) for m in (1, 2) for rm in range(5)}
    for key, first in before_cases.items():
        second = after_cases[key]
        group = groups[(first['iface'], first['m'], first['rm'])]
        if first['n'] != second['n']:
            raise ValueError('同一 case 的长度不同')
        group['cases'] += 1
        if any(first[x] != second[x] for x in ('after', 'flags', 'errno')):
            group['state_diff'] += 1
    for key, first in before_values.items():
        second = after_values[key]
        if first['x'] != second['x'] or first['z'] != second['z']:
            raise ValueError('同一 lane 的输入不一致')
        group = groups[(first['iface'], first['m'], first['rm'])]
        group['value_count'] += 1
        if first['y'] != second['y']:
            group['value_diff'] += 1
    return list(groups.values())


def build_run(args):
    build, run = args.build_dir.resolve(), args.run_dir.resolve()
    if build.exists() or run.exists() or build == run or build in run.parents or run in build.parents:
        raise ValueError('build-dir、run-dir 必须是两个互不包含的新目录')
    if not DATE_DIR.search(run.name) or any('build' in p.lower() for p in run.parts):
        raise ValueError('run-dir 必须含日期时间且不得位于 build 路径')
    gcc, archive, libm = args.gcc.resolve(), args.archive.resolve(), args.scalar_libm.resolve()
    paths = [gcc, archive, libm] + ([args.nemu.resolve()] if args.nemu else [])
    if not args.build_only and not args.nemu:
        raise ValueError('运行需指定 --nemu，或选择 --build-only')
    if not all(p.is_file() for p in paths):
        raise ValueError('编译器、库或 NEMU 不存在')
    build.mkdir(parents=True)
    run.mkdir(parents=True)
    logs, staging, tmp = run/'logs', run/'staging', run/'tmp'
    logs.mkdir(); staging.mkdir(); tmp.mkdir()
    records, identity_rows = [], []
    atomic_run_csv(run, 'commands.csv', COMMAND_FIELDS, records)

    def identify(role, path):
        path = Path(path).resolve()
        identity_rows.append(dict(role=role, path=str(path), sha256=sha(path)))
        atomic_run_csv(run, 'identities.csv', IDENTITY_FIELDS, identity_rows)

    for role, path in [('gcc', gcc), ('archive', archive), ('scalar-libm', libm),
                       ('observer', Path(__file__)), ('source', ABI/'direct_frm_probe.c'),
                       ('probe', ABI/'nx_probe.S'), ('runtime', ROOT/'single/common/am_runtime.c'),
                       ('runtime-impl', ROOT/'autovec/av_runtime.c'), ('runtime-header', ROOT/'autovec/av_runtime.h'),
                       ('tls-anchor', RUNTIME/'tls_anchor.c'), ('tls-linker', RUNTIME/'tls-safe.ld'),
                       ('linker', ROOT/'thirdparty/link.ld'), ('am', ROOT/'thirdparty/am-riscv64-xs.a'),
                       ('klib', ROOT/'thirdparty/klib-riscv64-xs.a')]:
        identify(role, path)
    if args.nemu:
        identify('nemu', args.nemu.resolve())

    def command(label, argv, timeout=120):
        argv = list(map(str, argv))
        stdout, stderr, temp_dir = logs/(label+'.stdout'), logs/(label+'.stderr'), tmp/label
        temp_dir.mkdir()
        env = os.environ.copy()
        env.update(TMPDIR=str(temp_dir), TMP=str(temp_dir), TEMP=str(temp_dir))
        record = dict(label=label, command=shlex.join(argv), cwd=str(run), status='running',
                      stdout=str(stdout), stderr=str(stderr), tmpdir=str(temp_dir))
        records.append(record)
        atomic_run_csv(run, 'commands.csv', COMMAND_FIELDS, records)
        try:
            with stdout.open('x') as out, stderr.open('x') as err:
                try:
                    proc = subprocess.run(argv, cwd=run, env=env, stdout=out, stderr=err, timeout=timeout)
                    record['status'] = str(proc.returncode)
                except subprocess.TimeoutExpired:
                    record['status'] = 'timeout'
                except OSError as exc:
                    record['status'] = 'launch-error'
                    print(str(exc), file=err)
        finally:
            if record['status'] == 'running':
                record['status'] = 'interrupted'
            atomic_run_csv(run, 'commands.csv', COMMAND_FIELDS, records)
        if record['status'] != '0':
            raise RuntimeError(f"{label} 返回 {record['status']}，见 {stderr}")
        return stdout.read_text(errors='replace')

    def query(flag):
        return command('query-'+flag.removeprefix('-').replace('=', '-').replace('.', '-'), [gcc, flag]).strip()
    objcopy = Path(query('-print-prog-name=objcopy'))
    libc, libgcc = (Path(query('-print-file-name='+x)) for x in ('libc.a', 'libgcc.a'))
    if not all(x.is_file() for x in (objcopy, libc, libgcc)):
        raise ValueError('GCC 未找到 objcopy/libc/libgcc')
    for role, path in (('objcopy', objcopy), ('libc', libc), ('libgcc', libgcc)):
        identify(role, path)
    common = ['-O2','-g','-march=rva23u64','-mabi=lp64d','-mcmodel=medany',
              '-fno-pie','-fno-stack-protector','-fno-lto','-mrvv-vector-bits=scalable',
              '-fno-fast-math','-frounding-math','-ffp-contract=off','-fno-tree-vectorize',
              '-fno-tree-slp-vectorize','-fno-builtin']
    def cc(label, src, dest, extra=()):
        command(label, [gcc, *common, *extra, '-c', src, '-o', dest])
    cc('runtime', ROOT/'single/common/am_runtime.c', build/'runtime.o', ['-ffreestanding','-I'+str(HARNESS)])
    cc('tls-anchor', RUNTIME/'tls_anchor.c', build/'tls_anchor.o')
    cc('nx-probe', ABI/'nx_probe.S', build/'nx_probe.o')
    f = args.function
    suffix = 'vv' if f == 'pow' else 'v'
    defs = [f'-DFUNC={f}', f'-DSINGLE={int(f in ("logf", "expf"))}', f'-DBINARY={int(f == "pow")}',
            f'-DNX1=_ZGV1Nx{suffix}_{f}', f'-DNX2=_ZGV2Nx{suffix}_{f}',
            f'-DARRAY1={f}vec_m1', f'-DARRAY2={f}vec_m2']
    cc('driver', ABI/'direct_frm_probe.c', build/'driver.o', defs)
    elf, binary = build/'frm.elf', build/'frm.bin'
    command('link', [gcc, *common, '-nostdlib','-static','-no-pie',build/'driver.o',build/'nx_probe.o',
                     archive,build/'runtime.o',build/'tls_anchor.o','-Wl,--wrap=_trm_init',
                     '-Wl,--build-id=none','-T',RUNTIME/'tls-safe.ld','-T',ROOT/'thirdparty/link.ld',
                     '-Wl,-Map='+str(build/'frm.map'),ROOT/'thirdparty/am-riscv64-xs.a',
                     '-Wl,--start-group',ROOT/'thirdparty/klib-riscv64-xs.a',libm,libc,libgcc,'-Wl,--end-group','-o',elf])
    identify('elf', elf)
    command('objcopy', [objcopy,'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary])
    identify('binary', binary)
    staged = staging/'frm.bin'
    shutil.copyfile(binary, staged)
    if sha(binary) != sha(staged):
        raise ValueError('staging SHA 不符')
    identify('staged-bin', staged)
    if not args.build_only:
        command('nemu', [args.nemu.resolve(),'-b','--max-instr='+str(args.max_instr),staged], timeout=args.timeout)
        stdout, stderr = logs/'nemu.stdout', logs/'nemu.stderr'
        evidence = read_commands(run/'commands.csv', stdout, stderr)
        cases, values, vlenb = parse_observation(stdout.read_text(errors='replace'), stderr.read_text(errors='replace'), evidence, f)
        if any(sha(row['path']) != row['sha256'] for row in identity_rows):
            raise ValueError('运行期间工具、库、源码或镜像改变')
        print(f'{f} 直接五 frm 观察完成：vlenb={vlenb} case={len(cases)} values={len(values)}；不代表数学精度合格')
    else:
        print('仅构建；未运行，不能标记为通过')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run', help='编译并可选运行单个函数的直接调用观察')
    for flag in ('gcc','archive','scalar-libm','build-dir','run-dir'):
        run.add_argument('--'+flag, type=Path, required=True)
    run.add_argument('--function', choices=FUNCTIONS, required=True)
    run.add_argument('--nemu', type=Path)
    run.add_argument('--build-only', action='store_true')
    run.add_argument('--max-instr', type=int, default=150000000)
    run.add_argument('--timeout', type=int, default=900)
    diff = sub.add_parser('compare', help='逐键比较两份完整 GOOD TRAP 日志')
    for side in ('before', 'after'):
        diff.add_argument('--'+side, required=True, type=Path, help='NEMU stdout')
        diff.add_argument('--'+side+'-stderr', required=True, type=Path, help='同次运行的 NEMU stderr（目标串口）')
        diff.add_argument('--'+side+'-commands', required=True, type=Path, help='同次运行的 commands.csv')
    diff.add_argument('--legacy-vlenb', type=int, help='仅旧版缺 FRM_CONFIG 日志使用的外部 VLENB；必须注明为假设')
    diff.add_argument('--function', choices=FUNCTIONS, required=True)
    diff.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'run':
            if args.max_instr <= 0 or args.timeout <= 0:
                raise ValueError('max-instr 和 timeout 必须为正')
            build_run(args)
        else:
            before_cmd = read_commands(args.before_commands, args.before, args.before_stderr)
            after_cmd = read_commands(args.after_commands, args.after, args.after_stderr)
            before = (args.before.read_text(errors='replace'), args.before_stderr.read_text(errors='replace'), before_cmd)
            after = (args.after.read_text(errors='replace'), args.after_stderr.read_text(errors='replace'), after_cmd)
            rows = compare(before, after, args.function, legacy_vlenb=args.legacy_vlenb)
            write_csv(args.output, ('function','interface','lmul','frm','cases','state_diff','value_count','value_diff'), rows)
            print(f'逐键比较完成；状态差异 {sum(x["state_diff"] for x in rows)} case，结果差异 {sum(x["value_diff"] for x in rows)} lane；不代表精度合格')
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        parser.exit(2, f'直接 frm 观察失败: {exc}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
