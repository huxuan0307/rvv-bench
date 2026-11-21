#!/usr/bin/env python3
"""构建七函数 baseline 或单函数候选；显式提供工具链、源码与全新输出。

示例（在独立日期运行目录内，TMPDIR 指向该目录下独立子目录）：
  python3 -B /绝对路径/script/build_oldarchive.py --prefix /工具链 --source /源码/algo-migration \
      --output /实验/build/日期时间-seven-baseline --log-dir ./commands \
      --functions log,exp,expf,logf,pow,sin,cos --variant baseline

默认仍仅构建 log，兼容原 baseline/cold/lifetime 用法；另可显式选择
lifetime-hybrid，使 log m1 array 使用 baseline 完整核，其他入口保持
lifetime。其他六函数各可选 baseline/cold/lifetime，非 baseline 仅支持
non-LTO；sin 还可加 +uniform 独立实验因子（--select sin=lifetime+uniform）。
多函数候选必须用 --select 逐函数指定变体，并要求覆盖 --functions 全部
函数；sin,cos 合库也可用 --focus 只优化其中一个。本工具不运行目标程序
或测试，不说明精度。
构建目录含源码快照、对象、archive 和清单；命令输出默认保存在构建
目录，显式 --log-dir 时保存在调用者的独立日期运行目录。每个编译
单元均在编译前后提取包括系统头在内的依赖并核对 SHA-256。
LTO 对象的反汇编可能不含最终机器码，须检查最终链接产物。
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

BASE = ['-O3', '-g', '-march=rva23u64', '-mabi=lp64d', '-mcmodel=medany',
        '-fno-pie', '-fno-stack-protector', '-mrvv-vector-bits=scalable',
        '-fno-fast-math', '-fno-tree-vectorize', '-fno-tree-slp-vectorize',
        '--param=gpr2vr-cost=0', '--param=fpr2vr-cost=0']
VARIANT_FLAGS = {'baseline': ['-DLOGVEC_BASELINE=1'], 'cold': [],
                 'lifetime': ['-DLOGVEC_HOT_LIFETIME=1'],
                 'lifetime-hybrid': ['-DLOGVEC_HOT_LIFETIME=1',
                                     '-DLOGVEC_ARRAY_M1_BASELINE=1']}
LOG_MACROS = {'LOGVEC_BASELINE', 'LOGVEC_HOT_LIFETIME', 'LOGVEC_ARRAY_M1_BASELINE'}
INTERNAL_LOG_MACROS = {'LOGVEC_M1_ARRAY_FULL', 'LOGVEC_M1_ARRAY_CORE'}
SIXMATH_COLD = '-DSIXMATH_COLD_DISPATCH=1'
SIXMATH_HOT = '-DSIXMATH_HOT_LIFETIME=1'
SIXMATH_EXPF_HOT = '-DSIXMATH_EXPF_HOT_LIFETIME=1'
SIXMATH_TRIG_UNIFORM = '-DSIXMATH_TRIG_UNIFORM=1'
FUNCTION_UNITS = {
    'log': ('log_m1', 'log_m2', 'logvec_data'),
    'exp': ('exp_m1', 'exp_m2', 'expvec_data'),
    'expf': ('expf_m1', 'expf_m2'),
    'logf': ('logf_m1', 'logf_m2', 'logfvec_data'),
    'pow': ('pow_m1', 'pow_m2'),
    'sin': ('sin_m1', 'sin_m2', 'trigvec_data'),
    'cos': ('cos_m1', 'cos_m2', 'trigvec_data'),
    'sinf': ('sinf_m1', 'sinf_m2', 'sincosfvec_data'),
    'cosf': ('cosf_m1', 'cosf_m2', 'sincosfvec_data'),
    'powf': ('powf_m1', 'powf_m2', 'powfvec_data', 'expfvec_data'),
}
# 保留原编译单元命名习惯，使变体选择与成员名稳定。
UNIT_PATHS = {
    f'{function}_m{lmul}': f'entries/{function}/glibc_m{lmul}.c'
    for function in FUNCTION_UNITS for lmul in (1, 2)
}
UNIT_PATHS.update({
    'expvec_data': 'kernels/exp/expvec_data.c',
    'logvec_data': 'kernels/log/logvec_data.c',
    'logfvec_data': 'kernels/logf/logfvec_data.c',
    'powfvec_data': 'kernels/powf/powfvec_data.c',
    'trigvec_data': 'kernels/common/trigvec_data.c',
    'sincosfvec_data': 'kernels/common/sincosfvec_data.c',
    'expfvec_data': 'kernels/common/expfvec_data.c',
})
# 入口以相对路径包含核与共享头，不需要额外 -I 目录。
SOURCE_DIRS = ()


def include_dirs(source):
    return [source / name for name in SOURCE_DIRS]


def unit_source(source, unit):
    return source / UNIT_PATHS.get(unit, unit + '.c')


INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"\n]+)"', re.MULTILINE)
DATE_DIR = re.compile(r'\d{8}[-_]\d{6}')
ENV_NAMES = ('PATH', 'TMPDIR', 'TMP', 'TEMP', 'CPATH', 'C_INCLUDE_PATH',
             'COMPILER_PATH', 'GCC_EXEC_PREFIX', 'LIBRARY_PATH', 'SOURCE_DATE_EPOCH')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def select_functions(text):
    functions = text.split(',')
    if (not functions or any(f not in FUNCTION_UNITS for f in functions)
            or len(functions) != len(set(functions))):
        raise ValueError('functions 须为不重复的已知函数名，用逗号分隔')
    return functions


def select_units(functions):
    return list(dict.fromkeys(unit for f in functions for unit in FUNCTION_UNITS[f]))


def select_variants(functions, variant, focus=None, lto=False):
    """返回每个所选函数的实际变体；拒绝含糊或不支持的组合。"""
    if not functions or len(set(functions)) != len(functions) or any(
            f not in FUNCTION_UNITS for f in functions):
        raise ValueError('函数选择无效')
    if variant not in VARIANT_FLAGS:
        raise ValueError('未知变体: ' + variant)
    if focus is not None:
        if (len(functions) != 2 or set(functions) != {'sin', 'cos'}
                or focus not in ('sin', 'cos') or variant not in ('cold', 'lifetime')):
            raise ValueError('--focus 仅支持 sin,cos 合库的单函数 cold/lifetime 候选')
        if lto:
            raise ValueError('六函数候选仅支持 non-LTO')
        return {f: variant if f == focus else 'baseline' for f in functions}
    if len(functions) == 1:
        f = functions[0]
        if f != 'log' and variant == 'lifetime-hybrid':
            raise ValueError('lifetime-hybrid 仅支持 log')
        if f != 'log' and variant != 'baseline' and lto:
            raise ValueError('六函数候选仅支持 non-LTO')
        return {f: variant}
    if variant != 'baseline':
        raise ValueError('多函数非 baseline 需用 --select 逐函数指定')
    return {f: 'baseline' for f in functions}


def check_variant_value(function, variant, lto=False):
    """Normalize one ``function=variant[+uniform]`` selection value."""
    if not isinstance(variant, str):
        raise ValueError('变体必须为字符串')
    base, separator, modifier = variant.partition('+')
    if base not in VARIANT_FLAGS:
        raise ValueError(f'{function}: 未知变体 {base!r}')
    if base == 'lifetime-hybrid' and function != 'log':
        raise ValueError('lifetime-hybrid 仅支持 log')
    if separator:
        if modifier != 'uniform':
            raise ValueError(f'{function}: 未知变体修饰 {modifier!r}')
        if function != 'sin':
            raise ValueError('+uniform 仅支持 sin')
    if function != 'log' and base != 'baseline' and lto:
        raise ValueError('六函数候选仅支持 non-LTO')
    return variant


def parse_selection(spec, functions, lto=False):
    """Parse ``log=lifetime-hybrid,sin=lifetime+uniform,...`` for one archive."""
    if not spec or not isinstance(spec, str):
        raise ValueError('--select 不得为空')
    selected = {}
    for item in spec.split(','):
        if '=' not in item:
            raise ValueError('--select 每项必须为 函数=变体: ' + item)
        function, variant = item.split('=', 1)
        if function not in functions:
            raise ValueError(f'--select 中的函数 {function!r} 不在 --functions 内')
        if function in selected:
            raise ValueError('--select 中函数重复: ' + function)
        selected[function] = check_variant_value(function, variant, lto)
    missing = [f for f in functions if f not in selected]
    if missing:
        raise ValueError('--select 缺少函数: ' + ','.join(missing))
    return {f: selected[f] for f in functions}


def local_closure(source, units):
    """保守跟踪所有本地 quoted include，包括预处理条件未启用的分支。"""
    source = Path(source).resolve(strict=True)
    pending = [unit_source(source, unit) for unit in units]
    found = set()
    while pending:
        path = pending.pop()
        if not path.is_file():
            raise ValueError('缺少本地源码或头文件: ' + str(path))
        path = path.resolve(strict=True)
        if not path.is_relative_to(source):
            raise ValueError('本地 include 越过源码目录: ' + str(path))
        if path in found:
            continue
        found.add(path)
        text = path.read_text(encoding='utf-8')
        for name in INCLUDE.findall(text):
            candidate = next((directory / name for directory in [path.parent, *include_dirs(source)]
                              if (directory / name).is_file()), path.parent / name)
            if not candidate.is_file():
                raise ValueError(f'无法解析 {path} 中的本地 include: {name}')
            pending.append(candidate)
    return sorted(p.relative_to(source).as_posix() for p in found)


def unit_variant(unit, variants):
    owners = [f for f in variants if unit in FUNCTION_UNITS[f]]
    if not owners:
        raise ValueError('编译单元不属于所选函数: ' + unit)
    if unit == 'logvec_data':
        return variants['log']
    if unit.endswith('vec_data'):
        return 'baseline'
    if len(owners) != 1:
        raise ValueError('算法编译单元归属不唯一: ' + unit)
    return variants[owners[0]]


def unit_experiment_flags(unit, variant):
    base, separator, modifier = variant.partition('+')
    if separator and (modifier != 'uniform' or base not in VARIANT_FLAGS):
        raise ValueError('未知变体: ' + variant)
    uniform = bool(separator)
    if unit in FUNCTION_UNITS['log']:
        if uniform:
            raise ValueError('+uniform 仅支持 sin')
        return list(VARIANT_FLAGS[base])
    kernels = {entry: f for f, units in FUNCTION_UNITS.items() if f != 'log'
               for entry in units if entry.endswith(('_m1', '_m2'))}
    if unit not in kernels:
        if unit not in select_units(list(FUNCTION_UNITS)):
            raise ValueError('未知编译单元: ' + unit)
        return []
    if uniform and kernels[unit] != 'sin':
        raise ValueError('+uniform 仅支持 sin')
    if base == 'baseline':
        return [SIXMATH_TRIG_UNIFORM] if uniform else []
    if base not in ('cold', 'lifetime'):
        raise ValueError('六函数仅支持 baseline/cold/lifetime')
    flags = [SIXMATH_COLD]
    if base == 'lifetime':
        flags.append(SIXMATH_EXPF_HOT if kernels[unit] == 'expf' else SIXMATH_HOT)
    if uniform:
        flags.append(SIXMATH_TRIG_UNIFORM)
    return flags


def unit_flags(unit, variant, lto):
    return (BASE + (['-flto'] if lto else ['-fno-lto'])
            + unit_experiment_flags(unit, variant) + ['-ffp-contract=fast'])


def dependency_paths(text, cwd):
    """解析 GCC -M 输出中的 make 转义及续行；返回绝对规范文件路径。"""
    target, sep, dependencies = text.partition(':')
    if not sep or not target.strip():
        raise ValueError('无效 GCC 依赖输出')
    words = shlex.split(dependencies.replace('\\\n', ' '))
    if not words:
        raise ValueError('空 GCC 依赖输出')
    paths = []
    for word in words:
        path = Path(word.replace('$$', '$'))
        if not path.is_absolute():
            path = cwd / path
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError('编译依赖不是普通文件: ' + str(path))
        paths.append(path)
    return sorted(set(paths), key=str)


def check_macros(text, unit, variant):
    macros = {}
    for line in text.splitlines():
        match = re.match(r'#define\s+(\w+)\s*(.*)', line)
        if match:
            macros[match.group(1)] = match.group(2).strip()
    expected = {flag[2:].split('=', 1)[0] for flag in unit_experiment_flags(unit, variant)}
    actual = {name for name in macros if name.startswith(('SIXMATH_', 'LOGVEC_'))}
    if actual != expected or any(macros[name] != '1' for name in expected):
        raise ValueError(f'意外的预定义实验宏: actual={sorted(actual)}, '
                         f'expected={sorted(expected)}')


def check_unit_predefs(cache, flags, unit, variant, load):
    """相同 flags 只调用一次预处理器；每个 TU 都独立验证角色。"""
    key = tuple(flags)
    if key not in cache:
        cache[key] = load()
    check_macros(cache[key], unit, variant)


def expected_symbols(functions):
    symbols = []
    for f in functions:
        sig = 'vv' if f in ('pow', 'powf') else 'v'
        symbols.extend((f'{f}vec_m1', f'{f}vec_m2',
                        f'_ZGV1Nx{sig}_{f}', f'_ZGV2Nx{sig}_{f}'))
    return symbols


def verify_symbols(text, functions):
    symbols = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-2] == 'T':
            symbols.append(parts[-1])
    for symbol in expected_symbols(functions):
        if symbols.count(symbol) != 1:
            raise ValueError(f'archive 中应恰有一个定义: {symbol}; count={symbols.count(symbol)}')


def verify_snapshot(source, snapshot, original):
    for name, digest in original.items():
        if sha(source / name) != digest or sha(snapshot / name) != digest:
            raise RuntimeError('源码或快照在构建时发生变化: ' + name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prefix', type=Path, required=True, help='GCC 安装前缀')
    ap.add_argument('--source', type=Path, required=True, help='数学实现源码目录')
    ap.add_argument('--output', type=Path, required=True, help='不存在的构建目录')
    ap.add_argument('--log-dir', type=Path,
                    help='不存在的命令日志目录，必须位于当前日期运行目录内且不含 build')
    ap.add_argument('--functions', default='log',
                    help='逗号分隔的不重复函数子集，默认 log')
    ap.add_argument('--variant', choices=tuple(VARIANT_FLAGS),
                    help='所选函数共用变体；多函数非 baseline 请改用 --select')
    ap.add_argument('--select',
                    help='逐函数变体，如 log=lifetime-hybrid,sin=lifetime+uniform；'
                         '必须覆盖 --functions 中每个函数')
    ap.add_argument('--focus', choices=('sin', 'cos'),
                    help='sin,cos 合库时只把该函数的 cold/lifetime 候选入库')
    ap.add_argument('--lto', action='store_true')
    a = ap.parse_args(argv)
    try:
        functions = select_functions(a.functions)
        if bool(a.variant) == bool(a.select):
            raise ValueError('必须且只能指定 --variant 或 --select 之一')
        if a.focus and (a.select or a.variant is None):
            raise ValueError('--focus 只能与 --variant 一起使用')
        variants = (parse_selection(a.select, functions, a.lto) if a.select
                    else select_variants(functions, a.variant, a.focus, a.lto))
        source = a.source.resolve(strict=True)
        prefix = a.prefix.resolve(strict=True)
        cwd = Path.cwd().resolve(strict=True)
        out = a.output.resolve()
        if not source.is_dir() or not prefix.is_dir():
            raise ValueError('source 和 prefix 必须是目录')
        if out == source or out.is_relative_to(source):
            raise ValueError('output 不得在 source 内')
        if out.exists():
            raise ValueError('output 已存在: ' + str(out))
        if a.log_dir:
            logs = a.log_dir.resolve()
            if (not DATE_DIR.search(cwd.name)
                    or any('build' in p.lower() for p in cwd.parts)
                    or not logs.is_relative_to(cwd) or logs == cwd
                    or any('build' in p.lower() for p in logs.parts)):
                raise ValueError('log-dir 必须位于当前带日期时间、路径不含 build 的运行目录内')
            if logs.exists() or logs.is_relative_to(out) or logs.is_relative_to(source):
                raise ValueError('log-dir 已存在或位于 output/source 内')
            tmp_value = os.environ.get('TMPDIR')
            if not tmp_value or not Path(tmp_value).is_absolute():
                raise ValueError('log-dir 模式必须设置指向运行目录内子目录的绝对 TMPDIR')
            tmpdir = Path(tmp_value).resolve(strict=True)
            if not tmpdir.is_dir() or tmpdir == cwd or not tmpdir.is_relative_to(cwd):
                raise ValueError('TMPDIR 必须是运行目录内已存在的子目录')
        else:
            logs = out
        units = select_units(functions)
        local_files = local_closure(source, units)
        # 每份可复现源码快照都携带许可证原字节。
        for license_name in ('COPYING.LIB', 'LICENSES.md'):
            if not (source / license_name).is_file():
                raise ValueError('缺少许可证: ' + license_name)
            local_files.append(license_name)
        local_files.sort()
    except (ValueError, OSError) as exc:
        ap.error(str(exc))

    # 在创建输出前固定源码身份；复制后和每次编译前后都核对原件及快照。
    original = {name: sha(source / name) for name in local_files}
    builder = Path(__file__).resolve(strict=True)
    builder_sha = sha(builder)
    out.mkdir(parents=True, exist_ok=False)
    if logs != out:
        logs.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(builder, out / 'build-script.py')
    if sha(builder) != builder_sha or sha(out / 'build-script.py') != builder_sha:
        raise RuntimeError('构建脚本或快照在构建前发生变化')
    snapshot = out / 'source'
    snapshot.mkdir()
    for name in local_files:
        dest = snapshot / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)
    verify_snapshot(source, snapshot, original)
    if sorted(local_closure(snapshot, units) + ['COPYING.LIB', 'LICENSES.md']) != local_files:
        raise RuntimeError('源码快照的 include 闭包与原件不一致')

    toolbase = prefix / 'bin/riscv64-unknown-linux-gnu-'
    cc = Path(str(toolbase) + 'gcc')
    ar = Path(str(toolbase) + ('gcc-ar' if a.lto else 'ar'))
    nm = Path(str(toolbase) + ('gcc-nm' if a.lto else 'nm'))
    objdump = Path(str(toolbase) + 'objdump')
    readelf = Path(str(toolbase) + 'readelf')
    tools = {'gcc': cc, 'ar': ar, 'nm': nm, 'objdump': objdump, 'readelf': readelf}
    identities = {}
    for name, tool in tools.items():
        resolved = tool.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise RuntimeError('工具不可执行: ' + str(tool))
        identities[name] = {'path': str(tool), 'resolved': str(resolved), 'sha256': sha(resolved)}

    commands = []
    def run(cmd, label, *, input_bytes=None):
        cmd = [str(x) for x in cmd]
        p = subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, cwd=cwd)
        (logs / (label + '.stdout')).write_bytes(p.stdout)
        (logs / (label + '.stderr')).write_bytes(p.stderr)
        commands.append({'label': label, 'command': cmd, 'cwd': str(cwd), 'rc': p.returncode})
        (logs / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        with (logs / 'commands.txt').open('a') as stream:
            stream.write(f'[{label}] cwd={shlex.quote(str(cwd))}\n{shlex.join(cmd)}\nrc={p.returncode}\n')
        if p.returncode:
            raise RuntimeError(label + ' failed; see ' + str(logs / (label + '.stderr')))
        return p.stdout.decode(errors='strict')

    version = run([cc, '--version'], 'compiler-version').splitlines()[0]
    for name in ('cc1', 'as'):
        output = run([cc, '-print-prog-name=' + name], 'compiler-' + name).strip()
        candidate = Path(output)
        if not candidate.is_absolute():
            located = shutil.which(output)
            if not located:
                raise RuntimeError('无法定位编译器子程序: ' + output)
            candidate = Path(located)
        resolved = candidate.resolve(strict=True)
        identities[name] = {'path': str(candidate), 'resolved': str(resolved), 'sha256': sha(resolved)}
    run([cc, '-dumpspecs'], 'compiler-specs')
    run([cc, '-print-sysroot'], 'compiler-sysroot')
    macro_checked = set()
    objects = []
    object_records = {}
    dependency_rows = []
    for stem in units:
        obj = out / (stem + '.o')
        variant = unit_variant(stem, variants)
        flags = unit_flags(stem, variant, a.lto)
        flags += ['-I' + str(directory) for directory in include_dirs(snapshot)]
        flag_key = tuple(flags)
        if flag_key not in macro_checked:
            predefs = run([cc, *flags, '-dM', '-E', '-x', 'c', '-'],
                          stem + '.macros', input_bytes=b'')
            check_macros(predefs, stem, variant)
            macro_checked.add(flag_key)
        dep_cmd = [cc, *flags, '-M', '-MT', stem + '.o', unit_source(snapshot, stem)]
        verify_snapshot(source, snapshot, original)
        dep_text = run(dep_cmd, stem + '.dependencies-before')
        deps = dependency_paths(dep_text, cwd)
        if (unit_source(snapshot, stem)).resolve() not in deps:
            raise RuntimeError('编译依赖缺少对应源码: ' + stem)
        if any(p.is_relative_to(source) for p in deps):
            raise RuntimeError('编译器引用了快照以外的原始源码')
        before = {str(path): sha(path) for path in deps}
        run([cc, *flags, '-c', unit_source(snapshot, stem), '-o', obj], stem + '.compile')
        dep_text_after = run(dep_cmd, stem + '.dependencies-after')
        deps_after = dependency_paths(dep_text_after, cwd)
        after = {str(path): sha(path) for path in deps_after}
        if before != after:
            raise RuntimeError('编译前后依赖或 SHA 发生变化: ' + stem)
        verify_snapshot(source, snapshot, original)
        run([objdump, '-dr', obj], stem + '.disassembly')
        run([readelf, '-A', '-sW', obj], stem + '.attributes')
        objects.append(obj)
        object_records[obj.name] = {'sha256': sha(obj), 'compile_flags': flags,
                                    'dependencies': before}
        dependency_rows.extend((obj.name, path, digest) for path, digest in before.items())

    archive = out / 'libnewmath.a'
    run([ar, 'rcs', archive, *objects], 'archive')
    members = run([ar, 't', archive], 'archive.members').splitlines()
    if members != [p.name for p in objects]:
        raise RuntimeError('archive 成员不符合预期')
    symbols = run([nm, '-g', '--defined-only', archive], 'archive.symbols')
    verify_symbols(symbols, functions)
    run([readelf, '-A', '-sW', archive], 'archive.attributes')
    verify_snapshot(source, snapshot, original)
    if sha(builder) != builder_sha or sha(out / 'build-script.py') != builder_sha:
        raise RuntimeError('构建脚本或快照在构建期间发生变化')
    for name, item in identities.items():
        if sha(item['resolved']) != item['sha256']:
            raise RuntimeError('构建工具在运行期间发生变化: ' + name)
    with (out / 'dependencies.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('object', 'dependency_path', 'sha256'))
        writer.writerows(dependency_rows)
    with (out / 'source-sha256.txt').open('w') as stream:
        for name, digest in original.items():
            stream.write(f'{digest}  {name}\n')
    flags_legacy = BASE + (['-flto'] if a.lto else ['-fno-lto'])
    if functions == ['log'] and a.variant:
        flags_legacy += VARIANT_FLAGS[a.variant]
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
                'variant': a.variant if a.variant else 'selected',
                'variants': variants,
                'selection': a.select, 'focus': a.focus,
                'functions': functions, 'units': units, 'compiler': str(cc.resolve()),
                'compiler_sha256': identities['gcc']['sha256'], 'compiler_version': version,
                'tool_identities': identities, 'environment': {k: os.environ.get(k) for k in ENV_NAMES},
                'builder': {'source': str(builder), 'snapshot': str(out / 'build-script.py'),
                            'sha256': builder_sha},
                'cwd': str(cwd), 'logs': str(logs), 'command_log': str(logs / 'commands.txt'),
                'archive': str(archive),
                'sha256': sha(archive), 'compile_flags': (
                    flags_legacy if functions == ['log'] and a.variant else None),
                'ffp_contract': 'fast', 'lto': a.lto, 'sources': original,
                'objects': {p.name: sha(p) for p in objects}, 'object_details': object_records,
                'dependency_manifest': str(out / 'dependencies.csv')}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('ARCHIVE=' + str(archive))
    print('SHA256=' + manifest['sha256'])
    print('FUNCTIONS=' + ','.join(functions))
    print('VARIANTS=' + ','.join(f'{f}={variants[f]}' for f in functions))
    print('LOGS=' + str(logs))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
