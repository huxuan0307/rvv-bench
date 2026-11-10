#!/usr/bin/env python3
"""Build the actual 141 upstream veclibm TESTs as independent AM binaries.

Only interval/grid sample counts and one documented upstream indexing defect
are adapted in generated test_infra.cpp. Function tests, special-value tables,
explicit argument tables, reference expressions and thresholds stay intact.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UPSTREAM = ROOT / 'thirdparty/veclibm'
AUTOVEC = ROOT / 'autovec'
DEFAULT_GCC = '/nfs/share/riscv-toolchain-gcc16.1.0-260715-nightly/bin/riscv64-unknown-linux-gnu-gcc'
TEST_RE = re.compile(r'\bTEST\(\s*(\w+)\s*,\s*(\w+)\s*\)')
# These upstream tests contain no EXPECT: eight show_special-only tests and
# two strided two-output helpers which compute/report errors without asserting.
DIAGNOSTIC_ONLY = {
    'cdfnorminv.special', 'cosh.special', 'erfc.special', 'erfcinv.special',
    'erfinv.special', 'lgamma.special', 'sinh.special', 'tgamma.special',
    'sincosI.test', 'sincospiI.test',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(cmd, log):
    cmd = [str(x) for x in cmd]
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    Path(log).write_text(p.stdout)
    if p.returncode:
        raise RuntimeError(f'command exited {p.returncode}: {cmd}\n{p.stdout[-7000:]}')
    return p.stdout


def catalog():
    cases = []
    sources = sorted((UPSTREAM / 'test/src').glob('test_*.cpp'))
    for src in sources:
        if src.name == 'test_infra.cpp':
            continue
        for match in TEST_RE.finditer(src.read_text()):
            suite, name = match.groups()
            cases.append({'id': f'{suite}.{name}', 'name': f'{suite}.{name}',
                          'suite': suite, 'test': name, 'source': str(src),
                          'source_line': src.read_text()[:match.start()].count('\n') + 1,
                          'source_sha256': digest(src),
                          'validation': 'diagnostic-only' if f'{suite}.{name}' in DIAGNOSTIC_ONLY
                                        else 'upstream-assertions'})
    if len(cases) != 141 or len({c['id'] for c in cases}) != 141:
        raise RuntimeError('Upstream catalog changed: expected 141 unique TEST declarations')
    if len({c['source'] for c in cases}) != 81:
        raise RuntimeError('Upstream catalog changed: expected 81 functional sources')
    return cases


def adapted_infra(points, grid_points):
    original = UPSTREAM / 'test/src/test_infra.cpp'
    text = original.read_text()
    # Only definitions with interval point-count arguments are clamped. Explicit
    # argument arrays, NB_TV and all N_SPECIALS loops are deliberately untouched.
    function = re.compile(r'(void report_\w+\([^;]*?\)\s*\{)', re.S)
    edits = []
    def clamp(match):
        signature = match.group(1)
        extra = ''
        if re.search(r'\bint nb_pts\b', signature):
            extra += f'\n  nb_pts = nb_pts > {points} ? {points} : nb_pts;'
            edits.append('interval_points')
        for axis in ('x', 'y'):
            if re.search(r'\bint nb_pts_' + axis + r'\b', signature):
                extra += f'\n  nb_pts_{axis} = nb_pts_{axis} > {grid_points} ? {grid_points} : nb_pts_{axis};'
                edits.append('grid_' + axis)
        return signature + extra
    text = function.sub(clamp, text)
    old = 'x[cnt++] = x_val;\n      y[cnt] = y_val;'
    new = 'x[cnt] = x_val;\n      y[cnt] = y_val;\n      ++cnt;'
    if text.count(old) != 1:
        raise RuntimeError('Expected upstream unit-stride grid indexing defect changed')
    text = text.replace(old, new)
    if edits.count('interval_points') != 10 or edits.count('grid_x') != 2 or edits.count('grid_y') != 2:
        raise RuntimeError(f'Unexpected interval adaptation sites: {edits}')
    return f'// Generated sampling adapter; upstream SHA256 {digest(original)}\n#line 1 "{original}"\n' + text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gcc', default=DEFAULT_GCC)
    parser.add_argument('--build', type=Path, default=HERE / 'build')
    parser.add_argument('--points', type=int, default=16)
    parser.add_argument('--grid-points', type=int, default=4)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--case', help='one exact suite.test id; the full catalog is still emitted')
    parser.add_argument('--config', type=Path, help='JSON id -> {points, grid_points, repeats} overrides')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.points < 2 or args.grid_points < 2 or args.repeats < 1 or args.jobs < 1:
        parser.error('points/grid-points must be >=2; repeats/jobs must be positive')
    cases = catalog()
    if args.list:
        print('\n'.join(c['id'] for c in cases))
        return 0
    build = args.build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    (build / 'catalog.json').write_text(json.dumps(cases, indent=2) + '\n')
    if args.case:
        cases = [c for c in cases if c['id'] == args.case]
        if not cases:
            parser.error(f'Unknown case {args.case}')
    config = json.loads(args.config.read_text()) if args.config else {}
    known = {c['id'] for c in catalog()}
    if set(config) - known:
        parser.error(f'Unknown config case ids: {set(config) - known}')
    for c in cases:
        overrides = config.get(c['id'], {})
        if set(overrides) - {'points', 'grid_points', 'repeats'}:
            parser.error(f'Unsupported override for {c["id"]}: {overrides}')
        c.update(points=int(overrides.get('points', args.points)),
                 grid_points=int(overrides.get('grid_points', args.grid_points)),
                 repeats=int(overrides.get('repeats', args.repeats)))
        if c['points'] < 2 or c['grid_points'] < 2 or c['repeats'] < 1:
            parser.error(f'Invalid scale for {c["id"]}')
    gcc = args.gcc
    prefix = gcc.removesuffix('gcc')
    gxx = prefix + 'g++'
    flags = ['-O2', '-g', '-march=rv64gcv', '-mabi=lp64d', '-mcmodel=medany',
             '-ffreestanding', '-fno-builtin', '-fno-pie', '-fno-stack-protector',
             '-ffunction-sections', '-fdata-sections']
    includes = ['-I' + str(HERE / 'include'), '-I' + str(AUTOVEC),
                '-I' + str(UPSTREAM / 'include'), '-I' + str(UPSTREAM / 'test/include')]
    cxxflags = ['-std=gnu++17', '-nostdinc++', '-fno-exceptions', '-fno-rtti', '-fno-threadsafe-statics']
    commands = []
    # Timestamp-independent command+content keys include all library headers.
    headers = sorted((UPSTREAM / 'include').glob('*.h'))
    header_hash = hashlib.sha256(''.join(digest(p) for p in headers).encode()).hexdigest()
    local_hash = hashlib.sha256((digest(HERE / 'include/gtest/gtest.h') + digest(HERE / 'include/math.h') +
                               digest(AUTOVEC / 'av_runtime.h') +
                               ''.join(digest(p) for p in sorted((UPSTREAM / 'test/include').glob('*.h')))).encode()).hexdigest()

    def compile_obj(src, obj, cxx=False, extra=(), dependency_hash=''):
        obj.parent.mkdir(parents=True, exist_ok=True)
        cmd = [gxx if cxx else gcc, *flags, *includes, *(cxxflags if cxx else []),
               *extra, '-c', src, '-o', obj]
        fingerprint = hashlib.sha256((json.dumps([str(x) for x in cmd]) + digest(src) +
                                     dependency_hash).encode()).hexdigest()
        key = obj.with_suffix('.sha256')
        if not obj.exists() or not key.exists() or key.read_text().strip() != fingerprint:
            run(cmd, obj.with_suffix('.log'))
            key.write_text(fingerprint + '\n')
        commands.append([str(x) for x in cmd])
        return obj

    library_sources = sorted((UPSTREAM / 'src').glob('*.c'))
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        objects = list(pool.map(lambda src: compile_obj(src, build / 'lib' / (src.stem + '.o'),
                                                        dependency_hash=header_hash), library_sources))
    archive = build / 'libveclibm.a'
    run([prefix + 'ar', 'rcs', archive, *objects], build / 'archive.log')
    runtime = compile_obj(HERE / 'am_runtime.c', build / 'runtime/av_runtime.o',
                          extra=['-fno-tree-vectorize'],
                          dependency_hash=local_hash + digest(AUTOVEC / 'av_runtime.c'))
    adapter = compile_obj(HERE / 'runtime.cpp', build / 'runtime/accuracy_runtime.o', True,
                          extra=['-fno-tree-vectorize'], dependency_hash=local_hash)
    source_objects = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for src, obj in pool.map(lambda src: (src, compile_obj(Path(src),
            build / 'tests' / (Path(src).stem + '.o'), True, ['-fno-tree-vectorize'],
            header_hash + local_hash)), sorted({c['source'] for c in cases})):
            source_objects[src] = obj
    # Runtime cap variables are defined by each independent driver. Compile the
    # infrastructure once, not once per distinct calibration profile.
    src = build / 'generated/test_infra.cpp'
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text('extern int accuracy_points, accuracy_grid_points;\n' +
                   adapted_infra('accuracy_points', 'accuracy_grid_points'))
    infra_obj = compile_obj(src, src.with_suffix('.o'), True,
                           ['-fno-tree-vectorize'], local_hash)


    def build_case(c):
        c = dict(c)
        dest = build / 'cases' / c['id']
        dest.mkdir(parents=True, exist_ok=True)
        driver = dest / 'main.cpp'
        function = f'accuracy_test_{c["suite"]}_{c["test"]}'
        driver.write_text(f'''// Generated driver for exactly one original upstream TEST.
#include "av_runtime.h"
extern void {function}(void);
extern "C" unsigned long accuracy_checks, accuracy_failures;
int accuracy_points = {c['points']};
int accuracy_grid_points = {c['grid_points']};
static_assert(sizeof(long double) == 16, "binary128 long double required");
static_assert(__LDBL_MANT_DIG__ == 113, "113-bit reference required");
int main(void) {{
  printf_("VECLIBM_ACCURACY_START case={c['id']} points={c['points']} grid_points={c['grid_points']} repeats={c['repeats']} ldbl_bits=113\\n");
  unsigned long begin, end;
  __asm__ volatile("rdinstret %0" : "=r"(begin) :: "memory");
  for (unsigned long i = 0; i < {c['repeats']}; ++i) {function}();
  __asm__ volatile("rdinstret %0" : "=r"(end) :: "memory");
  printf_("VECLIBM_ACCURACY_COMPLETE case={c['id']} checks=%lu failed=%lu body_instructions=%lu\\n",
      accuracy_checks, accuracy_failures, end - begin);
  return accuracy_failures != 0;
}}
''')
        c.update(directory=str(dest), bin=str(dest / 'test.bin'), elf=str(dest / 'test.elf'),
                 require_marker=f"VECLIBM_ACCURACY_COMPLETE case={c['id']}", status='failed')
        try:
            main_obj = compile_obj(driver, dest / 'main.o', True,
                                   ['-fno-tree-vectorize'], local_hash)
            cmd = [gcc, *flags, '-nostdlib', '-static', '-no-pie',
                   '-Wl,--wrap=_trm_init', '-Wl,--build-id=none', '-Wl,--gc-sections',
                   '-T', AUTOVEC / 'tls.ld', '-T', ROOT / 'thirdparty/link.ld',
                   main_obj, source_objects[c['source']], infra_obj,
                   adapter, runtime, archive, ROOT / 'thirdparty/am-riscv64-xs.a',
                   '-Wl,--start-group', ROOT / 'thirdparty/klib-riscv64-xs.a', '-lm', '-lc', '-lgcc',
                   '-Wl,--end-group', '-o', c['elf']]
            run(cmd, dest / 'link.log')
            (dest / 'link-command.json').write_text(json.dumps([str(x) for x in cmd], indent=2) + '\n')
            run([prefix + 'objcopy', '-S', '--set-section-flags', '.bss=alloc,contents',
                 '-O', 'binary', c['elf'], c['bin']], dest / 'objcopy.log')
            c['status'] = 'built'
            c['bin_sha256'] = c['sha256'] = digest(c['bin'])
            print('BUILT ' + c['id'], flush=True)
        except Exception as e:
            c['error'] = str(e)
            print('FAIL ' + c['id'] + ': ' + str(e)[-1000:], flush=True)
        return c

    metadata = {'schema': 1, 'suite': 'veclibm-accuracy', 'catalog_count': 141,
        'bin_paths': 'absolute', 'gcc': gcc, 'flags': flags, 'points': args.points,
        'grid_points': args.grid_points, 'repeats': args.repeats,
        'upstream_infra_sha256': digest(UPSTREAM / 'test/src/test_infra.cpp'),
        'adapter': 'restricted TEST/EXPECT_LT/EXPECT_EQ; not full GoogleTest',
        'oracle': 'unmodified upstream long-double expressions + toolchain static libm/libgcc; binary128',
        'adaptations': ['cap interval point counts; include both interval endpoints',
            'cap each grid axis; keep axis endpoints and all outer interval/target/stride loops',
            'retain all explicit argument tables and special-value vectors',
            'fix unit-stride report_err2_fp64 x[cnt++]/y[cnt] off-by-one in generated copy',
            'suppress unsupported upstream printf diagnostics only; assertions retain full comparison precision'],
        'cases': []}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        metadata['cases'] = list(pool.map(build_case, cases))
    (build / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (build / 'compile-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    return int(any(c['status'] != 'built' for c in metadata['cases']))


if __name__ == '__main__':
    sys.exit(main())
