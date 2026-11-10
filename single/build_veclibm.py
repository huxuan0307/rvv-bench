#!/usr/bin/env python3
"""Build the 25 AM performance images with explicit, recorded size parameters."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FUNCS = 'exp exp2 expm1 log log10 log2 log1p sqrt cbrt sin cos tan asin acos atan sinh cosh tanh asinh acosh atanh erf erfc tgamma lgamma'.split()
DEFAULT_GCC = '/nfs/share/riscv-toolchain-gcc16.1.0-260715-nightly/bin/riscv64-unknown-linux-gnu-gcc'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, log):
    p = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    log.write_text(p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'{log}: exit {p.returncode}\n{p.stderr[-3000:]}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gcc', default=DEFAULT_GCC)
    p.add_argument('--output', type=Path, default=HERE / 'build/veclibm-quick')
    p.add_argument('--config', type=Path, help='JSON mapping function names to {n,repeats}')
    p.add_argument('--n', type=int, default=4096)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--case', choices=FUNCS)
    p.add_argument('--jobs', type=int, default=8)
    a = p.parse_args()
    if a.n < 1 or a.repeats < 1 or a.jobs < 1:
        p.error('n, repeats and jobs must be positive')
    config = json.loads(a.config.read_text()) if a.config else {}
    if set(config) - set(FUNCS):
        p.error(f'unknown functions in config: {set(config) - set(FUNCS)}')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    prefix = a.gcc.removesuffix('gcc')
    # The shared archive is built with exactly the compiler recorded here.
    run(['make', '-B', '-C', HERE, '-j', str(a.jobs), 'build/veclibm.a', 'build/veclibm-runtime.o',
         f'CC={a.gcc}', f'TOOL_CHAIN_PREFIX={prefix}'], out / 'library-build.log')
    flags = ['-O2', '-g', '-march=rv64gcv', '-mabi=lp64d', '-mcmodel=medany',
             '-ffreestanding', '-fno-builtin', '-fno-pie', '-fno-stack-protector']
    link = ['-nostdlib', '-static', '-no-pie', '-Wl,--wrap=_trm_init', '-Wl,--build-id=none',
            '-T', ROOT / 'autovec/tls.ld', '-T', ROOT / 'thirdparty/link.ld',
            HERE / 'build/veclibm.a', HERE / 'build/veclibm-runtime.o',
            ROOT / 'thirdparty/am-riscv64-xs.a', '-Wl,--start-group',
            ROOT / 'thirdparty/klib-riscv64-xs.a', '-lm', '-lc', '-lgcc', '-Wl,--end-group']

    def build(name):
        params = config.get(name, {})
        n, reps = int(params.get('n', a.n)), int(params.get('repeats', a.repeats))
        if n < 1 or reps < 1:
            raise ValueError(f'{name}: n and repeats must be positive')
        dest = out / name
        dest.mkdir(exist_ok=True)
        elf = dest / f'veclibm-{name}-n{n}-r{reps}.elf'
        cmd = [a.gcc, *flags, f'-DTEST_FUNC={name}', f'-DVECLIBM_N={n}',
               f'-DVECLIBM_REPEATS={reps}', HERE / 'veclibm.c', *link, '-o', elf]
        result = dict(name=name, suite='performance', n=n, repeats=reps,
                      elf=str(elf), bin=str(elf.with_suffix('.bin')),
                      require_marker='VECLIBM_COMPLETE functions=1', command=[str(x) for x in cmd], status='failed')
        try:
            run(cmd, dest / 'build.log')
            run([prefix + 'objcopy', '-S', '--set-section-flags', '.bss=alloc,contents',
                 '-O', 'binary', elf, elf.with_suffix('.bin')], dest / 'objcopy.log')
            run([prefix + 'objdump', '-d', '-C', elf], dest / 'disassembly.txt')
            result.update(status='built', sha256=sha(elf.with_suffix('.bin')))
        except Exception as exc:
            result['error'] = str(exc)
        (dest / 'build.json').write_text(json.dumps(result, indent=2) + '\n')
        print(f"{result['status'].upper()} {name} n={n} repeats={reps}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        cases = list(pool.map(build, [a.case] if a.case else FUNCS))
    metadata = dict(schema_version=1, suite='performance', cases=cases, flags=flags,
                    compiler=subprocess.run([a.gcc, '--version'], check=True, text=True, capture_output=True).stdout.splitlines()[0],
                    sources={str(x.relative_to(ROOT)): sha(x) for x in [HERE / 'veclibm.c', HERE / 'veclibm-runtime.c', ROOT / 'autovec/av_runtime.c']},
                    archive_sha256=sha(HERE / 'build/veclibm.a'),
                    config=str(a.config.resolve()) if a.config else None,
                    note='Smoke/throughput only; real static libm baseline. Use veclibm-accuracy for numeric assertions.')
    (out / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return int(any(c['status'] != 'built' for c in cases))


if __name__ == '__main__':
    sys.exit(main())
