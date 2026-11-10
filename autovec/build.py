#!/usr/bin/env python3
"""Build pinned upstream suites for the existing rvv-bench AM platform."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys
from source_paths import default_sources

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_GCC = '/nfs/share/riscv-toolchain-gcc16.1.0-260715-nightly/bin/riscv64-unknown-linux-gnu-gcc'


def run(command, log=None):
    p = subprocess.run([str(x) for x in command], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if log:
        Path(log).write_text(p.stdout)
    if p.returncode:
        raise RuntimeError(f'{command}\n{p.stdout[-6000:]}')
    return p.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, default=default_sources())
    parser.add_argument('--gcc', default=DEFAULT_GCC)
    parser.add_argument('--output-root', '--build-root', dest='output_root', type=Path,
                        help='isolated output directory (defaults to legacy autovec/build/<opt>)')
    parser.add_argument('--generated-root', type=Path,
                        help='isolated generated source directory (defaults to legacy autovec/build/generated)')
    parser.add_argument('--extra-cflag', action='append', default=[],
                        help='additional compiler flag; repeat for multiple flags')
    parser.add_argument('--march', default='rv64gcv', choices=['rv64gcv', 'rva23u64'])
    parser.add_argument('--mabi', default='lp64d', choices=['lp64d'])
    parser.add_argument('--opt', choices=['O2', 'O3'], default='O3')
    parser.add_argument('--suite', choices=['all', 'tsvc', 'polybench', 'llvm'], default='all')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--case', help='Build one named test (requires --suite); keep its manifest separate')
    parser.add_argument('--scalar', action='store_true', help='validation reference; disable loop and SLP vectorization')
    args = parser.parse_args()
    if args.output_root is not None:
        build = args.output_root.resolve()
    else:
        build = HERE / 'build' / (args.opt + ('-scalar' if args.scalar else ''))
    if args.case:
        if args.suite == 'all' or not re.fullmatch(r'[A-Za-z0-9_-]+', args.case):
            parser.error('--case requires an explicit --suite and a valid test name')
        if args.output_root is None:
            build = build / 'selected' / args.suite / args.case
    generated = (args.generated_root.resolve() if args.generated_root else HERE / 'build' / 'generated')
    build.mkdir(parents=True, exist_ok=True)
    import importlib
    specs = []
    for suite, path in [('tsvc', 'TSVC_2'), ('polybench', 'PolyBenchC'), ('llvm', 'llvm-test-suite/SingleSource/UnitTests/Vectorizer')]:
        if args.suite in ('all', suite):
            prepare = importlib.import_module('prepare_' + suite).prepare
            specs += prepare((args.sources / path).resolve(), generated / suite)
    if args.case:
        specs = [spec for spec in specs if spec['name'] == args.case]
        if not specs:
            parser.error(f'Unknown {args.suite} test: {args.case}')
    prefix = args.gcc.removesuffix('gcc')
    flags = [f'-{args.opt}', f'-march={args.march}', f'-mabi={args.mabi}', '-mcmodel=medany', '-ffreestanding', '-fno-builtin', '-fno-pie', '-fno-stack-protector', '-g', '-I', str(HERE)]
    if args.extra_cflag:
        flags += args.extra_cflag
    if args.scalar:
        flags += ['-fno-tree-loop-vectorize', '-fno-tree-slp-vectorize']
    runtime = build / 'av_runtime.o'
    runtime_command = [str(x) for x in [args.gcc, *flags, '-fno-tree-vectorize', '-c', HERE / 'av_runtime.c', '-o', runtime]]
    run(runtime_command, build / 'runtime.log')
    revisions = {}
    for name in ('TSVC_2', 'llvm-test-suite', 'PolyBenchC'):
        revisions[name] = {'commit': run(['git', '-C', args.sources / name, 'rev-parse', 'HEAD']).strip(),
                           'origin': run(['git', '-C', args.sources / name, 'remote', 'get-url', 'origin']).strip(),
                           'dirty': bool(run(['git', '-C', args.sources / name, 'status', '--porcelain']).strip())}
    metadata = {'sources': revisions, 'compiler': run([args.gcc, '--version']).splitlines()[0], 'gcc': args.gcc, 'flags': flags,
                 'generated_root': str(generated), 'build_root': str(build),
                 'runtime_command': runtime_command,
                 'runtime_sources': [str(HERE / name) for name in ('av_runtime.c', 'av_runtime.h', 'tls.ld')],
                 'link_inputs': [str(ROOT / 'thirdparty' / name) for name in ('link.ld', 'am-riscv64-xs.a', 'klib-riscv64-xs.a')],
                'optimizer': run([args.gcc, *flags, '-Q', '--help=optimizers']),
                'target': run([args.gcc, *flags, '-Q', '--help=target']), 'cases': []}
    (build / 'optimizer.txt').write_text(metadata['optimizer'])
    (build / 'target.txt').write_text(metadata['target'])

    def compile_case(spec):
        dest = build / spec['suite'] / spec['name']
        dest.mkdir(parents=True, exist_ok=True)
        result = dict(spec, directory=str(dest), status='failed', commands=[])
        try:
            objects = []
            for i, src in enumerate(spec['sources']):
                src = Path(src)
                cc = prefix + ('g++' if src.suffix in ('.cpp', '.cc', '.cxx') else 'gcc')
                obj = dest / f'{src.stem}-{i}.o'
                extra = ['-I' + str(p) for p in spec.get('include_dirs', [])] + ['-D' + d for d in spec.get('defines', [])] + spec.get('cflags', [])
                if cc.endswith('g++'):
                    extra += ['-std=c++17', '-nostdinc++', '-fno-exceptions', '-fno-rtti', '-fno-threadsafe-statics']
                cmd = [cc, *flags, *extra, '-fopt-info-vec-all=' + str(obj.with_suffix('.vect')), '-fdump-tree-vect-details', '-c', str(src), '-o', str(obj)]
                result['commands'].append(cmd)
                obj.with_suffix('.vect').write_text('')
                run(cmd, obj.with_suffix('.log'))
                objects.append(obj)
            elf = dest / (spec['name'] + '.elf')
            cmd = [args.gcc, *flags, '-nostdlib', '-static', '-no-pie', '-Wl,--wrap=_trm_init', '-Wl,--build-id=none', '-T', HERE / 'tls.ld', '-T', ROOT / 'thirdparty/link.ld', *objects, runtime,
                   ROOT / 'thirdparty/am-riscv64-xs.a', '-Wl,--start-group', ROOT / 'thirdparty/klib-riscv64-xs.a', '-lm', '-lgcc', '-Wl,--end-group', '-o', elf]
            result['commands'].append([str(x) for x in cmd])
            run(cmd, dest / 'link.log')
            run([prefix + 'objcopy', '-S', '--set-section-flags', '.bss=alloc,contents', '-O', 'binary', elf, elf.with_suffix('.bin')])
            (dest / 'disassembly.txt').write_text(run([prefix + 'objdump', '-d', '-C', elf]))
            result['status'] = 'built'
        except Exception as e:
            result['error'] = str(e)
            print(f"FAIL {spec['suite']}/{spec['name']}: {str(e)[-500:]}", flush=True)
        else:
            print(f"BUILT {spec['suite']}/{spec['name']}", flush=True)
        (dest / 'commands.json').write_text(json.dumps(result['commands'], indent=2))
        return result
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        metadata['cases'] = list(pool.map(compile_case, specs))
    (build / 'manifest.json').write_text(json.dumps(metadata, indent=2))
    return int(any(s['status'] != 'built' for s in metadata['cases']))

if __name__ == '__main__':
    sys.exit(main())
