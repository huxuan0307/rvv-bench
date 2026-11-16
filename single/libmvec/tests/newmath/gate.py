#!/usr/bin/env python3
"""七函数历史 MPFR512 / array / Nx 门禁；不构建数学库，不隐式启动仿真。"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT = HERE / 'unit'
HARNESS = HERE.parents[1]
ROOT = HERE.parents[3]
FUNCTIONS = ('sin', 'cos', 'log', 'logf', 'exp', 'expf', 'pow')
# 冻结数据原字节的 SHA256；压缩封装允许不同，但解压内容绝不放宽。
FIXTURES = {
    'exp': ('exp-unit.h', 102039, 1, '86b1414a952b04f5e30f62ba78e441dfc11b51f83a3fa65889c84e354cf64274'),
    'expf': ('expf-unit.h', 100608, 2, 'd70e24909b2b1d2203e5d382b9643a427f90cc58f2747232b0fc81b334538106'),
    'logf': ('logf-unit.h', 101385, 3, 'f5f37d8873c667f8e3a0c8c3576c1c93535695b04069ee352d6f16fbbe7ce1d1'),
    'log': ('log-unit.h', 104965, 1, 'e07f8f882e82a1000fe2e785199bf0ebba2872e1f6a900decb0ccd2217b5e208'),
    'sin': ('trigvec-unit-data.h', 103084, 1, '247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d'),
    'cos': ('trigvec-unit-data.h', 103084, 1, '247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d'),
    'pow': ('powvec_unit_data.h', 124238, 1, 'a4dbbb9daea603282f8f4df2734cf18e1075fc5adaff02c2a4fb7e5ff187898e'),
}
BAD = ('HIT BAD TRAP', 'HIT CRITICAL ERROR', 'DIFFTEST ERROR', 'difftest check failed')
DATE_DIR = re.compile(r'\d{8}[-_]\d{4,6}')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def csv_write(path, fields, rows):
    with Path(path).open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def path_not_build(path):
    return not any('build' in p.lower() for p in Path(path).resolve().parts)


def staged_path(run, tag):
    run = Path(run).resolve()
    if not DATE_DIR.search(run.name) or not path_not_build(run):
        raise ValueError('staging 必须位于带日期时间且不含 build 的运行目录')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', tag):
        raise ValueError(f'非法 case 名称: {tag}')
    staging = run / 'staging'
    if staging.is_symlink() or not staging.is_dir() or staging.resolve() != staging:
        raise ValueError('staging 必须是本次运行目录下的真实目录')
    target = staging / (tag + '.bin')
    if target.is_symlink() or target.resolve() != target or not path_not_build(target):
        raise ValueError('staging 路径越界或包含 build')
    return target


def stage_binary(run, tag, binary, expected_sha):
    target = staged_path(run, tag)
    if sha(binary) != expected_sha:
        raise ValueError(f'{tag}: 复制前构建 BIN 已改变')
    # Exclusive creation refuses pre-existing files and symbolic links.
    with Path(binary).open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    if sha(binary) != expected_sha or sha(target) != expected_sha:
        raise ValueError(f'{tag}: staging 前后 BIN SHA 不一致')
    return target


def verify_artifacts(run, record, archive, archive_hash):
    target = staged_path(run, record['test'])
    if str(target) != record['staged_binary']:
        raise ValueError(f"{record['test']}: staging 路径已改变")
    checks = ((record['elf'], record['elf_sha256']),
              (record['binary'], record['binary_sha256']),
              (target, record['staged_binary_sha256']), (archive, archive_hash))
    if (record['binary_sha256'] != record['staged_binary_sha256'] or
            any(sha(path) != expected for path, expected in checks)):
        raise ValueError(f"{record['test']}: ELF、BIN、staging 或库已改变")


def choose_functions(text):
    values = text.split(',')
    if not values or len(values) != len(set(values)) or not set(values) <= set(FUNCTIONS):
        raise ValueError('functions 必须是不重复的七函数子集')
    if ('sin' in values) != ('cos' in values):
        raise ValueError('原 trig array 单元同时覆盖 sin/cos；必须同时指定两者')
    return values


def verify_header(path, function):
    _, count, limit, expected = FIXTURES[function]
    path = Path(path)
    if not path.is_file() or sha(path) != expected:
        raise ValueError(f'冻结 oracle 身份不符: {path}')
    with path.open(encoding='ascii') as f:
        prefix = f.read(512)
    cm = 'TRIG_UNIT_COUNT' if function in ('sin', 'cos') else 'POWVEC_UNIT_COUNT' if function == 'pow' else 'UNIT_COUNT'
    lm = 'TRIG_UNIT_ULP_LIMIT' if function in ('sin', 'cos') else 'POWVEC_UNIT_ULP_LIMIT' if function == 'pow' else 'UNIT_ULP_LIMIT' if function == 'log' else 'UNIT_LIMIT'
    for key, expected_value in ((cm, count), (lm, limit)):
        m = re.search(r'^#define\s+' + key + r'\s+(\d+)\s*$', prefix, re.M)
        if not m or int(m[1]) != expected_value:
            raise ValueError(f'{path}: {key} 不是冻结值 {expected_value}')
    return {'function': function, 'header': str(path.resolve()), 'header_sha256': expected, 'count': count, 'ulp_limit': limit}


def prepare(args):
    source, dest = args.oracle_dir.resolve(), args.headers_dir.resolve()
    if dest.exists():
        raise ValueError(f'拒绝覆盖共享头目录: {dest}')
    # 先核对全部数据，后创建目标目录，避免留下部分成功的正式输入。
    data = {}
    for name in sorted(set(x[0] for x in FIXTURES.values())):
        packed = source / Path(name).with_suffix('.dat')
        raw = gzip.decompress(packed.read_bytes())
        expected = next(x[3] for x in FIXTURES.values() if x[0] == name)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'oracle 解压 SHA 不符: {packed}')
        data[name] = (packed, raw)
    dest.mkdir(parents=True)
    for name, (_, raw) in data.items():
        with (dest / name).open('xb') as f:
            f.write(raw)
    rows = []
    for function in FUNCTIONS:
        row = verify_header(dest / FIXTURES[function][0], function)
        packed = data[FIXTURES[function][0]][0]
        row.update(compressed=str(packed.resolve()), compressed_sha256=sha(packed))
        rows.append(row)
    csv_write(dest / 'oracles.csv', ['function', 'count', 'ulp_limit', 'compressed', 'compressed_sha256', 'header', 'header_sha256'], rows)
    print(f'已物化并验证 {len(data)} 份共享头：{dest}')


def semantic_pairs(function):
    binary = function == 'pow'
    single = function in ('expf', 'logf')
    if function in ('sin', 'cos'):
        unit = UNIT / 'trigvec_unit.c'
    elif binary:
        unit = UNIT / 'powvec_target_unit.c'
    elif function == 'log':
        unit = UNIT / 'logvec_safe_unit.c'
    else:
        unit = UNIT / 'batch01_unit.c'
    text = unit.read_text(encoding='utf-8')
    if binary:
        sem = re.search(r'static const uint64_t sem\[\]\[2\]=\{(.*?)\n\};', text, re.S)
        if not sem:
            raise ValueError('pow 语义表未找到')
        pairs = re.findall(r'\{([^{}]+)\}', sem[1])
    else:
        if single:
            sem = text.split('#if SINGLE\nstatic const bits_t special[]=', 1)[1].split('#else', 1)[0].split('{', 1)[1].split('};', 1)[0]
        elif function in ('exp', 'log'):
            sem = text.split('#else\nstatic const bits_t special[]={', 1)[1].split('};', 1)[0]
        else:
            found = re.search(r'static const (?:uint64_t|bits_t) special\[\]=\{(.*?)\};', text, re.S)
            if not found:
                raise ValueError('trig 语义表未找到')
            sem = found[1]
        pairs = [v.strip() + ',0' for v in sem.replace('\n', '').split(',') if v.strip()]
    if not pairs:
        raise ValueError(f'{function}: 语义表为空')
    return unit, pairs


def create_nx_header(path, headers, f):
    single, binary = f in ('expf', 'logf'), f == 'pow'
    kind = 1 if f in ('sin', 'cos') else {'log': 2, 'logf': 3, 'exp': 4, 'expf': 5, 'pow': 6}[f]
    if kind == 1:
        names = ('TRIG_UNIT_COUNT', 'TRIG_UNIT_ULP_LIMIT', 'trig_unit_input', 'trig_unit_' + f)
    elif binary:
        names = ('POWVEC_UNIT_COUNT', 'POWVEC_UNIT_ULP_LIMIT', 'powvec_unit_x', 'powvec_unit_reference')
    elif f == 'log':
        names = ('UNIT_COUNT', 'UNIT_ULP_LIMIT', 'unit_input', 'unit_reference')
    else:
        names = ('UNIT_COUNT', 'UNIT_LIMIT', 'unit_input', 'unit_reference')
    _, pairs = semantic_pairs(f)
    oracle = (headers / FIXTURES[f][0]).resolve()
    if '"' in str(oracle) or '\n' in str(oracle):
        raise ValueError('oracle 路径含无法安全引用的字符')
    lines = ['/* Frozen MPFR512 oracle; no tolerance relaxation. */', f'#include "{oracle}"',
             f'#define FUNC {f}', f'#define FN_TEXT "{f}"', f'#define SINGLE {int(single)}',
             f'#define BINARY {int(binary)}', f'#define KIND {kind}', f'#define COS_CASE {int(f == "cos")}',
             f'#define NX1 _ZGV1Nx{"vv" if binary else "v"}_{f}', f'#define NX2 _ZGV2Nx{"vv" if binary else "v"}_{f}']
    lines += [f'#define {k} {v}' for k, v in zip(('ORACLE_COUNT', 'ORACLE_LIMIT', 'ORACLE_X', 'ORACLE_REF'), names)]
    if binary:
        lines.append('#define ORACLE_Z powvec_unit_z')
    lines.append('static const ' + ('uint32_t' if single else 'uint64_t') + ' semantic[][2]={' + ','.join('{' + p + '}' for p in pairs) + '};')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(pairs)


def parse_records(text, prefix):
    result = []
    for line in text.splitlines():
        if line.startswith(prefix + ' '):
            fields = {}
            for token in line.split()[1:]:
                if '=' in token:
                    key, val = token.split('=', 1)
                    if key in fields:
                        raise ValueError(f'重复字段: {line}')
                    fields[key] = val
            result.append(fields)
    return result


def exactly(rows, expected, wanted):
    if len(rows) != len(expected):
        raise ValueError(f'记录数 {len(rows)} != {len(expected)}: {wanted}')
    actual = []
    for row in rows:
        try:
            actual.append(tuple(row[k] for k in wanted))
        except KeyError as exc:
            raise ValueError(f'缺失字段 {exc}') from exc
    if sorted(actual) != sorted(expected):
        raise ValueError(f'{wanted}: {actual} != {expected}')


def validate_log(tag, text, functions):
    # 全套运行以实际计数行而非 COMPLETE 的常量自述判定。
    if any(b in text for b in BAD) or re.search(r'\b(?:NX|TRIG|UNIT|POWVEC|GATE)_(?:ERROR|UNSUPPORTED)\b', text):
        raise ValueError(f'{tag} 报错')
    if tag.startswith('nx-'):
        f = tag[3:]
        count, limit = FIXTURES[f][1:3]
        ns = len(semantic_pairs(f)[1])
        exactly(parse_records(text, 'NX_MPFR'), [(f, str(m), str(count), str(limit), '0') for m in (1, 2)], ('fn', 'lmul', 'count', 'limit', 'forbidden_calls'))
        exactly(parse_records(text, 'NX_ABI'),
                [(f, str(m), str(ns), str(ns * 8), '0,1,vlmax-1,vlmax', '1', 'v1-v7,v24-v31', '1') for m in (1, 2)],
                ('fn', 'lmul', 'semantics', 'calls', 'incoming_vl', 'mixed', 'preserved', 'caller_strict'))
        exactly(parse_records(text, 'NX_COMPLETE'), [(f, '0', 'pass')], ('fn', 'failed', 'status'))
    elif tag == 'array-trig':
        count, limit = FIXTURES['sin'][1:3]
        ns = len(semantic_pairs('sin')[1])
        exactly(parse_records(text, 'TRIG_MPFR'), [(str(f), f'm{m}', str(count), str(limit), '0') for f in (0, 1) for m in (1, 2)], ('function', 'mode', 'count', 'limit', 'ordinary_fallback'))
        exactly(parse_records(text, 'TRIG_SEMANTICS'), [(str(f), f'm{m}', str(ns), str(ns), '66', '0') for f in (0, 1) for m in (1, 2)], ('function', 'mode', 'isolated', 'mixed', 'tails', 'failed'))
        exactly(parse_records(text, 'TRIG_COMPLETE'), [('0', 'pass')], ('failed', 'status'))
    elif tag == 'array-pow':
        count, limit = FIXTURES['pow'][1:3]
        ns = len(semantic_pairs('pow')[1])
        exactly(parse_records(text, 'POWVEC_MPFR'), [(f'm{m}', str(count), str(limit), '0') for m in (1, 2)], ('mode', 'count', 'limit', 'normal_fallback'))
        exactly(parse_records(text, 'POWVEC_SEMANTICS'), [(f'm{m}', str(ns), str(ns), str(ns), '0') for m in (1, 2)], ('mode', 'isolated', 'mixed', 'tails', 'failed'))
        exactly(parse_records(text, 'POWVEC_COMPLETE'), [('0', 'pass')], ('failed', 'status'))
    else:
        f = tag.removeprefix('array-')
        if f not in functions or f not in ('exp', 'expf', 'log', 'logf'):
            raise ValueError(f'未知 array 测试 {tag}')
        count, limit = FIXTURES[f][1:3]
        ns = len(semantic_pairs(f)[1])
        exactly(parse_records(text, 'UNIT_MPFR'), [(f, f'm{m}', str(count), str(limit), '0') for m in (1, 2)], ('fn', 'mode', 'count', 'limit', 'hot_fallback'))
        exactly(parse_records(text, 'UNIT_SEMANTICS'), [(f, f'm{m}', str(ns), str(ns), str(ns), '0') for m in (1, 2)], ('fn', 'mode', 'isolated', 'mixed', 'inplace', 'failed'))
        exactly(parse_records(text, 'UNIT_COMPLETE'), [(f, '0', 'pass')], ('fn', 'failed', 'status'))
    complete = parse_records(text, 'NX_COMPLETE' if tag.startswith('nx-') else 'TRIG_COMPLETE' if tag == 'array-trig' else 'POWVEC_COMPLETE' if tag == 'array-pow' else 'UNIT_COMPLETE')
    if int(complete[0].get('checks', '0')) <= 0:
        raise ValueError('检查数为空')


def run_gate(args):
    fs = choose_functions(args.functions)
    build, run, headers = args.build_dir.resolve(), args.run_dir.resolve(), args.headers_dir.resolve()
    archive, gcc, libm = args.new_archive.resolve(), args.gcc.resolve(), args.scalar_libm.resolve()
    if build.exists() or run.exists():
        raise ValueError('build-dir 和 run-dir 必须都是尚不存在的新目录')
    if build == run or build in run.parents or run in build.parents:
        raise ValueError('build-dir 与 run-dir 必须分离且不得互相包含')
    if not DATE_DIR.search(run.name) or not path_not_build(run):
        raise ValueError('run-dir 名必须带日期时间，且路径不得包含 build')
    if args.max_instr <= 0 or args.timeout <= 0:
        raise ValueError('max-instr 和 timeout 必须为正')
    if not args.build_only and not args.nemu:
        raise ValueError('必须指定 --nemu；仅构建请用 --build-only')
    for path in (archive, gcc, libm, *([args.nemu.resolve()] if args.nemu else [])):
        if not path.is_file():
            raise ValueError(f'必需文件不存在: {path}')
    for f in fs:
        verify_header(headers / FIXTURES[f][0], f)
    build.mkdir(parents=True)
    run.mkdir(parents=True)
    logs = run / 'logs'
    logs.mkdir()
    (run / 'staging').mkdir()
    temp_root = run / 'tmp'
    temp_root.mkdir()
    archive_hash = sha(archive)
    command_rows = []
    test_rows = []
    identity_rows = []

    def identify(role, path):
        path = Path(path).resolve()
        identity_rows.append({'role': role, 'path': str(path), 'sha256': sha(path)})

    def save_rows(name, fields, rows):
        # 仅允许覆盖本次新建运行目录的固定报告，原子写入由 mkstemp 创建。
        if name not in ('commands.csv', 'tests.csv', 'identities.csv', 'coverage.csv'):
            raise ValueError(f'非法报告名: {name}')
        with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8', dir=run,
                                         prefix=name + '.', suffix='.partial', delete=False) as stream:
            temp = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        if temp.parent.resolve() != run or temp.name.startswith(name + '.') is False:
            raise ValueError('临时报告路径不属于本次运行')
        temp.replace(run / name)

    command_fields = ['label', 'cwd', 'tmpdir', 'command', 'status', 'stdout', 'stderr']
    test_fields = ['test', 'status', 'elf', 'elf_sha256', 'binary', 'binary_sha256', 'stdout', 'stderr',
                   'staged_binary', 'staged_binary_sha256']
    coverage_fields = ['function', 'interface', 'lmul', 'expected_count', 'actual_count', 'ulp_limit', 'status']
    coverage = [dict(function=f, interface=interface, lmul=str(m), expected_count=str(FIXTURES[f][1]),
                     actual_count='', ulp_limit=str(FIXTURES[f][2]), status='not-run')
                for f in fs for interface in ('Nx', 'array') for m in (1, 2)]
    save_rows('coverage.csv', coverage_fields, coverage)

    def record_coverage(tag, text):
        if tag.startswith('nx-'):
            interface, function, records = 'Nx', tag[3:], parse_records(text, 'NX_MPFR')
            measured = {(function, r['lmul']): r['count'] for r in records}
        elif tag == 'array-trig':
            interface = 'array'
            measured = {(('sin', 'cos')[int(r['function'])], r['mode'][1:]): r['count']
                        for r in parse_records(text, 'TRIG_MPFR')}
        elif tag == 'array-pow':
            interface = 'array'
            measured = {('pow', r['mode'][1:]): r['count'] for r in parse_records(text, 'POWVEC_MPFR')}
        else:
            interface, function = 'array', tag.removeprefix('array-')
            measured = {(function, r['mode'][1:]): r['count'] for r in parse_records(text, 'UNIT_MPFR')}
        hits = 0
        for row in coverage:
            key = (row['function'], row['lmul'])
            if row['interface'] == interface and key in measured:
                if row['status'] != 'not-run' or measured[key] != row['expected_count']:
                    raise ValueError(f'覆盖重复或数量不符: {interface} {key}')
                row['actual_count'], row['status'] = measured[key], 'pass'
                hits += 1
        if hits != len(measured):
            raise ValueError(f'覆盖项目不属于当前计划: {tag}')
        save_rows('coverage.csv', coverage_fields, coverage)

    def command(label, argv, cwd=run, timeout=120):
        argv = list(map(str, argv))
        out, err = logs / (label + '.stdout'), logs / (label + '.stderr')
        temp = temp_root / label
        temp.mkdir()
        env = os.environ.copy()
        env.update(TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp))
        status = 'not-started'
        try:
            with out.open('x') as stdout, err.open('x') as stderr:
                try:
                    p = subprocess.run(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
                    status = str(p.returncode)
                except subprocess.TimeoutExpired:
                    status = 'timeout'
        finally:
            command_rows.append(dict(label=label, cwd=str(cwd), tmpdir=str(temp), command=shlex.join(argv), status=status, stdout=str(out), stderr=str(err)))
            save_rows('commands.csv', command_fields, command_rows)
        if status != '0':
            raise RuntimeError(f'{label}: {status}；详见 {err}')
        return out.read_text(errors='replace'), err.read_text(errors='replace')

    def query(flag):
        # GCC 信息查询也是可审计命令，输出分流。
        text, _ = command('query-' + flag.removeprefix('-').replace('=', '-').replace('.', '-'), [gcc, flag])
        return text.strip()

    tools = {'gcc': gcc}
    for name in ('readelf', 'objcopy', 'objdump'):
        tool = Path(query('-print-prog-name=' + name))
        if not tool.is_file():
            raise ValueError(f'GCC 未返回有效 {name} 路径: {tool}')
        tools[name] = tool.resolve()
    libraries = {'libm': libm}
    for name in ('libc.a', 'libgcc.a'):
        lib = Path(query('-print-file-name=' + name))
        if not lib.is_file():
            raise ValueError(f'GCC 未找到 {name}: {lib}')
        libraries[name] = lib.resolve()
    for role, path in [('archive', archive), *tools.items(), *libraries.items(), ('runtime', ROOT / 'single/veclibm-runtime.c'),
                       ('runtime-impl', ROOT / 'autovec/av_runtime.c'), ('runtime-header', ROOT / 'autovec/av_runtime.h'),
                       ('entry-rne', HARNESS / 'entry_rne.c'), ('tls-anchor', HARNESS / 'tls_anchor.c'),
                       ('tls-linker', HARNESS / 'tls-safe.ld'), ('linker', ROOT / 'thirdparty/link.ld'),
                       ('am', ROOT / 'thirdparty/am-riscv64-xs.a'), ('klib', ROOT / 'thirdparty/klib-riscv64-xs.a'),
                       ('nx-source', HERE / 'nx_gate.c'), ('nx-probe', HERE / 'nx_probe.S'), ('gate-script', HERE / 'gate.py')]:
        identify(role, path)
    for f in fs:
        identify('oracle-' + f, headers / FIXTURES[f][0])
        identify('unit-' + f, semantic_pairs(f)[0])
    if 'sin' in fs:
        identify('trig-unit-header', UNIT / 'trigvec_data.h')
    if 'pow' in fs:
        identify('pow-unit-header', UNIT / 'powvec.h')
    if args.nemu:
        identify('nemu', args.nemu)
    save_rows('identities.csv', ['role', 'path', 'sha256'], identity_rows)
    common = ['-O2', '-g', '-march=rva23u64', '-mabi=lp64d', '-mcmodel=medany', '-fno-pie', '-fno-stack-protector',
              '-flto' if args.lto else '-fno-lto', '-mrvv-vector-bits=scalable', '-fno-fast-math', '-frounding-math',
              '-ffp-contract=off', '-fno-tree-vectorize', '-fno-tree-slp-vectorize', '-fno-builtin']

    def cc(label, src, dest, extra=()):
        command(label, [gcc, *common, *extra, '-c', src, '-o', dest])

    runtime = ROOT / 'single/veclibm-runtime.c'
    cc('runtime', runtime, build / 'runtime.o', ['-ffreestanding', '-I' + str(HARNESS)])
    for name in ('entry_rne', 'tls_anchor'):
        cc(name, HARNESS / (name + '.c'), build / (name + '.o'))
    cc('nx_probe', HERE / 'nx_probe.S', build / 'nx_probe.o')

    def link(label, objects, wraps, elf):
        command(label, [gcc, *common, '-nostdlib', '-static', '-no-pie', *objects, archive, build / 'entry_rne.o',
                        build / 'runtime.o', build / 'tls_anchor.o', *[f'-Wl,--wrap={s}' for s in [*wraps, 'main', '_trm_init']],
                        '-Wl,--build-id=none', '-T', HARNESS / 'tls-safe.ld', '-T', ROOT / 'thirdparty/link.ld',
                        '-Wl,-Map=' + str(elf.with_suffix('.map')), ROOT / 'thirdparty/am-riscv64-xs.a', '-Wl,--start-group',
                        ROOT / 'thirdparty/klib-riscv64-xs.a', libm, libraries['libc.a'], libraries['libgcc.a'], '-Wl,--end-group', '-o', elf])

    def tls_offset(elf, label):
        txt, _ = command(label, [tools['readelf'], '-W', '-s', elf])
        mt = re.search(r'\d+:\s+([0-9a-f]+)\s+4\s+TLS\s+GLOBAL\s+DEFAULT\s+\d+\s+errno$', txt, re.M)
        if not mt:
            raise ValueError(f'{label}: 未找到 errno TLS 符号')
        return int(mt[1], 16)

    def execute(tag, elf):
        binary = elf.with_suffix('.bin')
        command(tag + '-objcopy', [tools['objcopy'], '-S', '--set-section-flags', '.bss=alloc,contents', '-O', 'binary', elf, binary])
        target = staged_path(run, tag)
        record = dict(test=tag, status='staging', elf=str(elf), elf_sha256=sha(elf), binary=str(binary),
                      binary_sha256=sha(binary), stdout='', stderr='', staged_binary=str(target), staged_binary_sha256='')
        test_rows.append(record)
        save_rows('tests.csv', test_fields, test_rows)
        try:
            stage_binary(run, tag, binary, record['binary_sha256'])
            record['staged_binary_sha256'] = sha(target)
            verify_artifacts(run, record, archive, archive_hash)
            record['status'] = 'built'
            save_rows('tests.csv', test_fields, test_rows)
            if not args.build_only:
                record['status'] = 'running'
                record['stdout'], record['stderr'] = str(logs / (tag + '-nemu.stdout')), str(logs / (tag + '-nemu.stderr'))
                save_rows('tests.csv', test_fields, test_rows)
                # Recheck identities even when the simulator fails or times out.
                try:
                    verify_artifacts(run, record, archive, archive_hash)
                    stdout, stderr = command(tag + '-nemu', [args.nemu.resolve(), '-b', '--max-instr=' + str(args.max_instr), target], timeout=args.timeout)
                finally:
                    verify_artifacts(run, record, archive, archive_hash)
                if 'HIT GOOD TRAP' not in stdout or any(x in stdout + stderr for x in BAD):
                    raise ValueError(f'{tag}: NEMU 未给出有效 good trap')
                text = stdout + '\n' + stderr
                validate_log(tag, text, fs)
                record_coverage(tag, text)
                record['status'] = 'pass'
                save_rows('tests.csv', test_fields, test_rows)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            record['status'] = 'fail'
            save_rows('tests.csv', test_fields, test_rows)
            raise

    for f in fs:
        d = build / ('nx-' + f)
        d.mkdir()
        create_nx_header(d / 'nx_case.h', headers, f)
        cc('nx-' + f + '-rne-probe', HARNESS / 'entry_rne.c', d / 'rne_probe.o', ['-D__real_main=nx_probe_main', '-D__wrap_main=nx_rne_probe'])
        def compile_nx(label, offset):
            cc(label, HERE / 'nx_gate.c', d / 'unit.o', ['-I' + str(d), f'-DUNIT_ERRNO_OFFSET={offset}', '-DABI_ONLY=0'])
        elf = d / 'unit.elf'
        objects = [d / 'unit.o', build / 'nx_probe.o', d / 'rne_probe.o']
        compile_nx('nx-' + f + '-compile-discover', 0)
        link('nx-' + f + '-link-discover', objects, [f], elf)
        offset = tls_offset(elf, 'nx-' + f + '-tls-discover')
        compile_nx('nx-' + f + '-compile', offset)
        link('nx-' + f + '-link', objects, [f], elf)
        if tls_offset(elf, 'nx-' + f + '-tls') != offset:
            raise ValueError(f'{f}: 两次链接的 TLS 偏移不一致')
        command('nx-' + f + '-disassembly', [tools['objdump'], '-dr', elf])
        execute('nx-' + f, elf)
    cases = []
    for f in fs:
        if f in ('exp', 'expf', 'logf', 'log'):
            islog = f == 'log'
            src = UNIT / ('logvec_safe_unit.c' if islog else 'batch01_unit.c')
            oracle = headers / FIXTURES[f][0]
            defs = [f'-DFUNC={f}', f'-DVEC1={f}vec_m1', f'-DVEC2={f}vec_m2', f'-DSINGLE={int(f.endswith("f"))}',
                    f'-DLOG_INPUT={int(f in ("log", "logf"))}', f'-DDATA_HEADER="{oracle}"']
            if islog:
                defs.append('-DUNIT_LIMIT=UNIT_ULP_LIMIT')
            cases.append((f, src, [f], defs, 'UNIT_ERRNO_OFFSET', 'probe_main', 'rne_probe'))
    if 'sin' in fs:
        cases.append(('trig', UNIT / 'trigvec_unit.c', ['sin', 'cos'], [], 'UNIT_ERRNO_OFFSET', 'trigvec_probe_main', 'trigvec_rne_probe'))
    if 'pow' in fs:
        cases.append(('pow', UNIT / 'powvec_target_unit.c', ['pow'], [], 'POWVEC_ERRNO_TLS_OFFSET', 'powvec_probe_main', 'powvec_rne_probe'))
    for tag, src, wraps, defs, macro, probe_main, probe_name in cases:
        d = build / ('array-' + tag)
        d.mkdir()
        inc = ['-I' + str(UNIT), '-I' + str(headers)]
        cc('array-' + tag + '-probe', HARNESS / 'entry_rne.c', d / 'probe.o', [f'-D__real_main={probe_main}', f'-D__wrap_main={probe_name}'])
        def compile_array(label, off):
            cc(label, src, d / 'unit.o', [*inc, *defs, f'-D{macro}={off}'])
        elf = d / 'unit.elf'
        objects = [d / 'unit.o', d / 'probe.o']
        compile_array('array-' + tag + '-compile-discover', 0)
        link('array-' + tag + '-discover', objects, wraps, elf)
        offset = tls_offset(elf, 'array-' + tag + '-tls-discover')
        compile_array('array-' + tag + '-compile', offset)
        link('array-' + tag + '-link', objects, wraps, elf)
        if tls_offset(elf, 'array-' + tag + '-tls') != offset:
            raise ValueError(f'{tag}: 两次链接的 TLS 偏移不一致')
        execute('array-' + tag, elf)
    for record in test_rows:
        try:
            verify_artifacts(run, record, archive, archive_hash)
        except (ValueError, OSError):
            record['status'] = 'fail'
            save_rows('tests.csv', test_fields, test_rows)
            raise
    for identity in identity_rows:
        if sha(identity['path']) != identity['sha256']:
            raise ValueError(f"测试期间输入或工具改变: {identity['path']}")
    expected = len(fs) + len(cases)
    if len(test_rows) != expected or any(r['status'] != ('built' if args.build_only else 'pass') for r in test_rows):
        raise ValueError('实际 case 数量或状态不完整')
    if not args.build_only and any(r['status'] != 'pass' or r['actual_count'] != r['expected_count'] for r in coverage):
        raise ValueError('全量覆盖计数有缺口')
    expected_lanes = sum(int(r['expected_count']) for r in coverage)
    actual_lanes = sum(int(r['actual_count'] or 0) for r in coverage)
    report = run / 'summary.md'
    report.write_text('# 七函数门禁摘要\n\n' + f"- 函数：{', '.join(fs)}\n- 实际 case：{len(test_rows)} / {expected}\n" +
                      f'- MPFR 样本比较：实际 {actual_lanes} / 预期 {expected_lanes}（array 与 Nx、m1 与 m2 分别计数）\n' +
                      f"- 结果：{'仅构建，未运行，不是 pass' if args.build_only else '所选测试通过'}\n" +
                      '- 分片：未实现；完整原相位，不承诺 50M 指令/进程。\n'
                      '- 数学精度只在 RNE 测试；5 个 frm 仅测试外层 RNE 包装，未直接比较数学核。\n'
                      '- 每个 case、命令、输入身份及 stdout/stderr 见同目录 CSV。\n', encoding='utf-8')
    print(f'{report}: {len(test_rows)} cases')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare', help='一次解压并核验六份共享 oracle，输出 oracles.csv')
    p.add_argument('--oracle-dir', type=Path, required=True)
    p.add_argument('--headers-dir', type=Path, required=True)
    p = sub.add_parser('run', help='编译，或显式使用 NEMU 执行未分片完整门禁')
    p.add_argument('--new-archive', type=Path, required=True)
    p.add_argument('--gcc', type=Path, required=True)
    p.add_argument('--scalar-libm', type=Path, required=True)
    p.add_argument('--headers-dir', type=Path, required=True)
    p.add_argument('--build-dir', type=Path, required=True, help='只存放编译产物；不得作为运行 cwd')
    p.add_argument('--run-dir', type=Path, required=True, help='全新、名称含日期时间、不在任何 build 路径下')
    p.add_argument('--functions', default=','.join(FUNCTIONS))
    p.add_argument('--nemu', type=Path, help='显式传入才允许实际启动 NEMU')
    p.add_argument('--build-only', action='store_true')
    p.add_argument('--lto', action='store_true')
    p.add_argument('--max-instr', type=int, default=1500000000)
    p.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args)
        else:
            run_gate(args)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'门禁停止：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
