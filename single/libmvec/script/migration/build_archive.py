#!/usr/bin/env python3
"""从函数优先布局构建显式替换归档；见 algo-migration/README.md（只构建）。"""

import argparse
from collections import Counter
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1]
CANDIDATES = LIB / 'algo-migration'
sys.path.insert(0, str(HERE.parent))
from build_oldarchive import dependency_paths
from runner_common import identity, sha256, write_json

FUNCTIONS = ('atan', 'atan2', 'acos', 'log10')
# 与来源归档保持一致；PIE 用于裸机高地址链接，当前仅构建原生对象。
FLAGS = ('-mcmodel=medlow', '-O2', '-march=rv64gcv', '-mabi=lp64d', '-fPIE',
         '-frounding-math', '-ftrapping-math', '-fno-math-errno', '-fno-common',
         '-fmerge-all-constants', '-fno-lto', '-Wall', '-Wwrite-strings', '-Wundef')


def functions_arg(text):
    names = text.split(',')
    if len(names) != len(set(names)) or not set(names) <= set(FUNCTIONS):
        raise argparse.ArgumentTypeError('functions 必须是已知函数的不重复逗号列表')
    return tuple(fn for fn in FUNCTIONS if fn in names)


def expected_symbols(functions):
    return {f'_ZGV{m}{mask}{lanes}{"vv" if f == "atan2" else "v"}_{f}'
            for f in functions for m in (1, 2) for mask in ('N', 'M')
            for lanes in (2 * m, 'x')}


def verify_exports(nm, readelf, expected):
    exports = re.findall(r'^\s*[0-9a-fA-F]+\s+([A-Za-z])\s+(\S+)$', nm, re.M)
    if Counter(exports) != Counter(('T', name) for name in expected):
        raise ValueError(f'归档全局符号不符：{sorted(exports)}')
    variant = []
    for line in readelf.splitlines():
        parts = line.split()
        if len(parts) >= 8 and re.fullmatch(r'[0-9]+:', parts[0]) and parts[-1] in expected:
            if parts[3:5] != ['FUNC', 'GLOBAL'] or parts[-2] == 'UND' or '[VARIANT_CC]' not in parts:
                raise ValueError(f'符号缺少已定义的向量调用约定：{line.strip()}')
            variant.append(parts[-1])
    if Counter(variant) != Counter(expected):
        raise ValueError('ELF 向量符号覆盖不完整或有重复')


def build(args):
    cc = args.gcc.expanduser().absolute()
    if not cc.name.endswith('-gcc'):
        raise ValueError('--gcc 必须指向带 target 前缀的 GCC')
    stem = cc.name.removesuffix('gcc')
    tools = {'gcc': cc, **{name: cc.with_name(stem + name)
                          for name in ('ar', 'gcc-nm', 'readelf')}}
    for path in tools.values():
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f'工具不存在或不可执行：{path}')
    tool_ids = {name: identity(path) for name, path in tools.items()}
    source = {'README.md': CANDIDATES / 'README.md',
              'COPYING.LIB': LIB / 'oldarchive' / 'COPYING.LIB'}
    sources = []
    for fn in args.functions:
        kernel = f'kernels/{fn}/{args.variant}.c'
        source[kernel] = CANDIDATES / kernel
        for m in (1, 2):
            entry = f'entries/{fn}/{args.variant}_m{m}.c'
            source[entry] = CANDIDATES / entry
            sources.append((fn, m, entry, kernel))
    hashes = {rel: sha256(path) for rel, path in source.items()}
    builder_sha = sha256(__file__)
    out = args.output.expanduser().resolve()
    if args.output.is_symlink() or out.exists() or out.is_relative_to(LIB):
        raise ValueError('output 必须是源码目录之外的全新目录')
    out.mkdir(parents=True, exist_ok=False)
    snap, logs, scratch = (out / name for name in ('source', 'logs', 'scratch'))
    for path in (snap, logs, scratch):
        path.mkdir()
    for rel, path in source.items():
        dest = snap / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        if sha256(dest) != hashes[rel]:
            raise ValueError(f'源快照哈希不符：{rel}')
    env = os.environ.copy()
    env.update(LC_ALL='C', TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch))
    commands = []

    def run(label, *argv):
        argv = list(map(str, argv))
        with (logs / (label + '.stdout')).open('xb') as stdout, \
             (logs / (label + '.stderr')).open('xb') as stderr:
            process = subprocess.run(argv, cwd=out, env=env, stdout=stdout, stderr=stderr)
        commands.append({'label': label, 'argv': argv, 'rc': process.returncode})
        if process.returncode:
            raise ValueError(f'{label} 退出 {process.returncode}；见 {logs / (label + ".stderr")}')
        return (logs / (label + '.stdout')).read_text()

    if not run('target', cc, '-dumpmachine').strip().startswith('riscv64-'):
        raise ValueError('--gcc 不是 RV64 编译器')
    version = run('gcc-version', cc, '--version').splitlines()[0]
    for name in ('as', 'cc1'):
        text = run('gcc-' + name, cc, '-print-prog-name=' + name).strip()
        path = text if Path(text).is_absolute() else shutil.which(text, path=env.get('PATH'))
        if path is None:
            raise ValueError(f'无法定位 GCC 子程序：{text}')
        tool_ids[name] = identity(path)

    objects = []
    dependencies = {}
    for fn, m, entry, kernel in sources:
        label = f'{fn}_m{m}'
        obj = out / (label + '.o')
        flags = FLAGS + (('-mrvv-max-lmul=m1',) if m == 1 else ())

        def deps(suffix):
            text = run(label + '-' + suffix, cc, *flags, '-M', '-MT', obj.name, snap / entry)
            paths = dependency_paths(text, out)
            if not {snap / entry, snap / kernel} <= set(paths) or any(
                    p.is_relative_to(CANDIDATES) for p in paths):
                raise ValueError(f'编译依赖未绑定快照：{label}')
            return {str(p): sha256(p) for p in paths}

        before = deps('deps-before')
        run(label + '-compile', cc, *flags, '-c', snap / entry, '-o', obj)
        if deps('deps-after') != before:
            raise ValueError(f'编译期间依赖变化：{label}')
        dependencies[obj.name] = before
        objects.append(obj)
    archive = out / f'libmvec-{args.variant}.a'
    run('archive', tools['ar'], 'rcsD', archive, *objects)
    if run('members', tools['ar'], 't', archive).splitlines() != [p.name for p in objects]:
        raise ValueError('归档成员或顺序不符')
    symbols = run('exports', tools['gcc-nm'], '-g', '--defined-only', archive)
    attrs = run('attributes', tools['readelf'], '-W', '-s', archive)
    verify_exports(symbols, attrs, expected_symbols(args.functions))
    for rel, digest in hashes.items():
        if sha256(source[rel]) != digest or sha256(snap / rel) != digest:
            raise ValueError(f'构建期间源码或快照发生变化：{rel}')
    if sha256(__file__) != builder_sha or any(
            sha256(info['realpath']) != info['sha256'] for info in tool_ids.values()):
        raise ValueError('构建期间工具或脚本发生变化')
    info = identity(archive)
    write_json(out / 'manifest.json', {
        'schema_version': 1, 'suite': 'libmvec-candidate', 'status': 'built',
        'variant': args.variant, 'functions': list(args.functions), 'lto': False,
        'archive': info, 'objects': {p.name: sha256(p) for p in objects},
        'builder_sha256': builder_sha, 'sources': hashes, 'tools': tool_ids,
        'gcc_version': version, 'commands': commands, 'dependencies': dependencies,
    })
    print(f'ARCHIVE={archive}\nSHA256={info["sha256"]}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', required=True, choices=('arm',))
    parser.add_argument('--functions', default=FUNCTIONS, type=functions_arg,
                        help='逗号分隔，默认 atan,atan2,acos,log10')
    parser.add_argument('--gcc', required=True, type=Path, help='带 target 前缀的 RV64 GCC 路径')
    parser.add_argument('--output', required=True, type=Path, help='全新的归档输出目录')
    try:
        build(parser.parse_args(argv))
    except (OSError, ValueError) as exc:
        print(f'ARCHIVE_REJECTED {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
