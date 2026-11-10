#!/usr/bin/env python3
"""Host-side self-checks for the libmvec public-ABI program.

These tests never execute RISC-V code.  They statically verify that

  * abi-data.h is byte-identical to what abi-probe.py regenerates,
  * the data rows are well formed, keep the released corpus thresholds and
    domains, and include the documented special values,
  * abi.c declares exactly the 36 public ABI symbols (cross-checked against
    the installed libmvec.a when a toolchain prefix is available), with one
    descriptor per symbol, the right vector type / mask type / fixed lane
    count and the right ULP budget,
  * abi.c emits the protocol records in the required order and shape:
    one CONFIG with seed=123456789 rounds=0, one LIBMVEC_ABI per symbol,
    one LIBMVEC_CHECK, a unique LIBMVEC_COMPLETE only on success, and
    LIBMVEC_ERROR followed by a non-zero return on failure.

Run: python3 single/libmvec/test_abi.py
     python3 -m unittest single/libmvec/test_abi.py
"""
import hashlib
import os
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ABI_C = HERE / 'abi.c'
ABI_H = HERE / 'abi-data.h'
PROBE = HERE / 'abi-probe.py'

SEED = 123456789
CORPUS_COUNT = 1024
FINITE = 64
ULP = {'log': 1, 'exp': 1, 'sin': 1, 'cos': 1, 'pow': 1, 'logf': 3, 'expf': 2}
MASKED_FAMILIES = {'log', 'exp'}
DEFAULT_TOOLCHAIN = Path(
    '/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec')

# ---------------------------------------------------------------------------
# Expected 36-symbol ABI table (derived from glibc libmvec.abilist /
# mathvec-variants.mk: log/exp m1+m2 fixed+Nx masked+unmasked, sin/cos/pow
# m1+m2 fixed+Nx unmasked, logf/expf m1+m2 fixed+Nx unmasked).

def expected_symbols():
    table = {}
    for fam, lanes in (('log', 2), ('exp', 2), ('sin', 2), ('cos', 2)):
        for lmul, fixed in ((1, lanes), (2, 2 * lanes)):
            table[f'_ZGV{lmul}N{fixed}v_{fam}'] = dict(
                fam=fam, single=0, masked=0, binary=0, lmul=lmul,
                fixed=1, lanes=fixed)
            table[f'_ZGV{lmul}Nxv_{fam}'] = dict(
                fam=fam, single=0, masked=0, binary=0, lmul=lmul,
                fixed=0, lanes=0)
    for lmul, fixed in ((1, 2), (2, 4)):
        table[f'_ZGV{lmul}N{fixed}vv_pow'] = dict(
            fam='pow', single=0, masked=0, binary=1, lmul=lmul,
            fixed=1, lanes=fixed)
        table[f'_ZGV{lmul}Nxvv_pow'] = dict(
            fam='pow', single=0, masked=0, binary=1, lmul=lmul,
            fixed=0, lanes=0)
    for fam, lanes in (('logf', 4), ('expf', 4)):
        for lmul, fixed in ((1, lanes), (2, 2 * lanes)):
            table[f'_ZGV{lmul}N{fixed}v_{fam}'] = dict(
                fam=fam, single=1, masked=0, binary=0, lmul=lmul,
                fixed=1, lanes=fixed)
            table[f'_ZGV{lmul}Nxv_{fam}'] = dict(
                fam=fam, single=1, masked=0, binary=0, lmul=lmul,
                fixed=0, lanes=0)
    for fam in ('log', 'exp'):
        for lmul, fixed in ((1, 2), (2, 4)):
            table[f'_ZGV{lmul}M{fixed}v_{fam}'] = dict(
                fam=fam, single=0, masked=1, binary=0, lmul=lmul,
                fixed=1, lanes=fixed)
            table[f'_ZGV{lmul}Mxv_{fam}'] = dict(
                fam=fam, single=0, masked=1, binary=0, lmul=lmul,
                fixed=0, lanes=0)
    return table


EXPECTED = expected_symbols()
assert len(EXPECTED) == 36


def _read(path):
    return path.read_text()


def _mask_bits(single, lmul):
    """vboolN_t element size for an RVV float register group."""
    return (32 if single else 64) // lmul


def _is_nan_bits(bits, single):
    if single:
        return (bits & 0x7f800000) == 0x7f800000 and (bits & 0x007fffff) != 0
    return ((bits & 0x7ff0000000000000) == 0x7ff0000000000000
            and (bits & 0x000fffffffffffff) != 0)


def _row_re(single):
    width = 8 if single else 16
    return re.compile(
        r'\{\s*0x([0-9a-f]{%d})(?:ULL|U),\s*0x([0-9a-f]{%d})(?:ULL|U),'
        r'\s*0x([0-9a-f]{%d})(?:ULL|U),\s*0x([0-9a-f]{2}),'
        r'\s*0x([0-9a-f]{2}),\s*(\d)\s*\}' % (width, width, width))


def parse_data_header(text=None):
    text = _read(ABI_H) if text is None else text
    arrays = {}
    for fam in ULP:
        single = fam.endswith('f')
        m = re.search(r'static const abi_case(64|32) abi_cases_%s\[[^\]]*\] = \{(.*?)\};'
                      % re.escape(fam), text, re.S)
        if not m:
            raise AssertionError(f'abidata: missing array for {fam}')
        rows = []
        for row in _row_re(single).finditer(m.group(2)):
            x, y, ref, req, forbid, kind = row.groups()
            rows.append(dict(x=int(x, 16), y=int(y, 16), ref=int(ref, 16),
                             req=int(req, 16), forbid=int(forbid, 16),
                             kind=int(kind)))
        arrays[fam] = dict(single=single, width=8 if single else 16, rows=rows)
    return arrays


def parse_abi_c(text=None):
    text = _read(ABI_C) if text is None else text
    externs = re.findall(r'^extern\s+v(?:float64|float32)m[12]_t\s+(_ZGV\w+)\s*\(',
                         text, re.M)
    desc = []
    for kind, name, lmul, fixed, lanes, cname, fam in re.findall(
            r'SYM_(U64|U32|B64|M64)\s*\(\s*"(_ZGV\w+)"\s*,\s*(\d)\s*,\s*(\d)\s*,'
            r'\s*(\d+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)', text):
        desc.append(dict(macro=kind, name=name, lmul=int(lmul), fixed=int(fixed),
                         lanes=int(lanes), cname=cname, fam=fam))
    return text, externs, desc


def parse_header_macros(text=None):
    text = _read(ABI_H) if text is None else text
    out = {}
    for key in ('ABI_DATA_SEED', 'ABI_DATA_CORPUS_COUNT',
                'ABI_DATA_FINITE_PER_FUNC'):
        m = re.search(r'#define\s+%s\s+(\d+)' % key, text)
        out[key] = int(m.group(1)) if m else None
    m = re.search(r'#define\s+ABI_DATA_GENERATOR_SHA256\s+"([0-9a-f]{64})"', text)
    out['sha'] = m.group(1) if m else None
    m = re.search(r'#define\s+ABI_DATA_MPFR_VERSION\s+"([^"]+)"', text)
    out['mpfr'] = m.group(1) if m else None
    out['ulp'] = {fam.upper(): int(re.search(
        r'#define\s+ABI_ULP_%s\s+(\d+)' % fam.upper(), text).group(1))
        for fam in ULP}
    out['counts'] = {}
    for fam in ULP:
        m = re.search(r'#define\s+ABI_CASES_%s\s+(\d+)' % fam.upper(), text)
        out['counts'][fam] = int(m.group(1)) if m else None
    return out


class DataHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(ABI_H)
        cls.macros = parse_header_macros(cls.text)
        cls.arrays = parse_data_header(cls.text)

    def test_generator_is_current(self):
        proc = subprocess.run([sys.executable, str(PROBE), '--check'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         f'abi-data.h is stale: {proc.stdout}{proc.stderr}')

    def test_provenance(self):
        self.assertEqual(self.macros['ABI_DATA_SEED'], SEED)
        self.assertEqual(self.macros['ABI_DATA_CORPUS_COUNT'], CORPUS_COUNT)
        self.assertEqual(self.macros['ABI_DATA_FINITE_PER_FUNC'], FINITE)
        self.assertIsNotNone(self.macros['mpfr'])
        self.assertRegex(self.macros['mpfr'], r'^\d+\.\d+')
        sha = hashlib.sha256(PROBE.read_bytes()).hexdigest()
        self.assertEqual(self.macros['sha'], sha,
                         'header provenance names a different generator')

    def test_provenance_wording(self):
        # The corpus is this suite's own LCG stream, not an older released
        # data set; the special-value flags are a measured regression
        # contract, not claimed as an independent ISA proof.
        docstring = PROBE.read_text()
        self.assertIn('LCG', docstring)
        self.assertNotIn('SplitMix', docstring)
        self.assertIn('regression contract', docstring)
        self.assertIn('LCG', self.text)
        self.assertNotIn('SplitMix', self.text)
        self.assertIn('not an\n *                  older released data set', self.text)
        self.assertIn('not an independent IEEE/ISA proof', self.text)

    def test_thresholds_not_relaxed(self):
        self.assertEqual({k.lower(): v for k, v in self.macros['ulp'].items()},
                         ULP)

    def test_row_shape_and_counts(self):
        self.assertEqual(sorted(self.arrays), sorted(ULP))
        for fam, data in self.arrays.items():
            rows = data['rows']
            self.assertEqual(self.macros['counts'][fam], len(rows),
                             f'{fam}: header count macro disagrees with rows')
            self.assertGreaterEqual(len(rows), FINITE, fam)
            self.assertEqual(sum(r['kind'] == 0 for r in rows[:FINITE]), FINITE,
                             f'{fam}: first {FINITE} rows must be finite ULP rows')
            for i, row in enumerate(rows):
                self.assertIn(row['kind'], (0, 1, 2), f'{fam}[{i}]: bad kind')
                self.assertLessEqual(row['req'] | row['forbid'], 0x1f, f'{fam}[{i}]: flags')
                self.assertEqual(row['req'] & row['forbid'], 0,
                                 f'{fam}[{i}]: required/forbidden flags overlap')
                if row['kind'] == 1:
                    self.assertTrue(_is_nan_bits(row['ref'], data['single']),
                                    f'{fam}[{i}]: NaN row with non-NaN reference')
                else:
                    self.assertFalse(_is_nan_bits(row['ref'], data['single']),
                                     f'{fam}[{i}]: non-NaN row with NaN reference')
                if row['kind'] == 0 and i < FINITE:
                    # Corpus rows carry the relaxed per-case flag budget; the
                    # mixed-lane sweep separately forbids NV/DZ, and special
                    # rows (kind 0 with req/forbid) keep their own budget.
                    self.assertEqual(row['req'], 0,
                                     f'{fam}[{i}]: finite row must not require flags')
                    self.assertEqual(row['forbid'], 0,
                                     f'{fam}[{i}]: finite row flag budget')

    def test_corpus_domains(self):
        def dbl(bits):
            return struct.unpack('<d', struct.pack('<Q', bits))[0]

        def flt(bits):
            return struct.unpack('<f', struct.pack('<I', bits))[0]

        for fam, data in self.arrays.items():
            conv = flt if data['single'] else dbl
            base = fam.removesuffix('f')
            for i, row in enumerate(data['rows'][:FINITE]):
                x = conv(row['x'])
                if base in ('log', 'pow'):
                    self.assertGreaterEqual(x, 0.125, f'{fam}[{i}] x low')
                    self.assertLess(x, 8.0, f'{fam}[{i}] x high')
                elif base == 'exp':
                    self.assertGreaterEqual(x, -8.0, f'{fam}[{i}] x low')
                    self.assertLess(x, 8.0, f'{fam}[{i}] x high')
                else:
                    self.assertGreaterEqual(x, -32.0, f'{fam}[{i}] x low')
                    self.assertLess(x, 32.0, f'{fam}[{i}] x high')
                if base == 'pow':
                    y = conv(row['y'])
                    self.assertGreaterEqual(y, -2.0, f'{fam}[{i}] y low')
                    self.assertLessEqual(y, 2.0, f'{fam}[{i}] y high')

    def test_shared_corpus_across_precisions(self):
        # log/logf and exp/expf must draw from the same corpus stream, and
        # sin/cos must use the same argument stream.
        def as_float_bits(bits):
            value = struct.unpack('<d', struct.pack('<Q', bits))[0]
            return struct.unpack('<I', struct.pack('<f', value))[0]

        d = self.arrays
        for a, b in (('log', 'logf'), ('exp', 'expf')):
            for i in range(FINITE):
                self.assertEqual(as_float_bits(d[a]['rows'][i]['x']),
                                 d[b]['rows'][i]['x'], f'{a}/{b}[{i}] stream')
        for i in range(FINITE):
            self.assertEqual(d['sin']['rows'][i]['x'], d['cos']['rows'][i]['x'],
                             f'sin/cos[{i}] stream')

    def test_specials_present(self):
        labels = self.text
        required = ['log(+0)', 'log(-0)', 'log(-1)', 'log(+inf)', 'log(-inf)',
                    'log(qNaN)', 'log(sNaN)', 'exp(+inf)', 'exp(-inf)',
                    'exp(qNaN)', 'exp(sNaN)', 'sin(+0)', 'sin(-0)',
                    'sin(+inf)', 'cos(+0)', 'cos(+inf)', 'pow(0,0)',
                    'pow(0,-1)', 'pow(-1,0.5)', 'pow(2,1024)', 'pow(2,-1075)',
                    'pow(qNaN,0)', 'pow(1,qNaN)', 'pow(sNaN,2)',
                    'logf(+0)', 'logf(-1)', 'logf(sNaN)', 'expf(+inf)',
                    'expf(-inf)', 'expf(sNaN)', 'expf(-1000)']
        for label in required:
            self.assertIn(f'/* {label} */', labels, f'missing special {label}')
        # overflow/underflow flag budgets used by the special rows
        self.assertRegex(self.text, r'0x05, 0x1a, 2 \}, /\* exp\(1000\) \*/')
        self.assertRegex(self.text, r'0x03, 0x1c, 2 \}, /\* exp\(-1000\) \*/')
        self.assertRegex(self.text, r'0x03, 0x1c, 0 \}, /\* expf\(-100\) \*/')


class SymbolCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text, cls.externs, cls.desc = parse_abi_c()
        cls.info = EXPECTED

    def test_exactly_36_unique_externs(self):
        self.assertEqual(len(self.externs), 36)
        self.assertEqual(len(set(self.externs)), 36)
        self.assertEqual(set(self.externs), set(EXPECTED))

    def test_exactly_one_descriptor_per_symbol(self):
        self.assertEqual(len(self.desc), 36)
        names = [d['name'] for d in self.desc]
        self.assertEqual(len(set(names)), 36, 'duplicate descriptor')
        self.assertEqual(set(names), set(EXPECTED))
        self.assertEqual(len(re.findall(
            r'^\s+SYM_(?:U64|U32|B64|M64)\s*\(', self.text, re.M)), 36)

    def test_descriptor_attributes(self):
        for d in self.desc:
            want = self.info[d['name']]
            self.assertEqual(d['fam'], want['fam'], d['name'])
            self.assertEqual(d['lmul'], want['lmul'], d['name'])
            self.assertEqual(d['fixed'], want['fixed'], d['name'])
            self.assertEqual(d['lanes'], want['lanes'], d['name'])
            if want['masked']:
                self.assertEqual(d['macro'], 'M64', d['name'])
            elif want['binary']:
                self.assertEqual(d['macro'], 'B64', d['name'])
            elif want['single']:
                self.assertEqual(d['macro'], 'U32', d['name'])
            else:
                self.assertEqual(d['macro'], 'U64', d['name'])
            self.assertEqual(d['cname'], d['name'], 'wrapper mismatch')
            if want['masked']:
                bits = _mask_bits(False, want['lmul'])
                self.assertIn(f'DEF_M64 (m{want["lmul"]}, {bits}, {d["name"]})',
                              self.text, f'{d["name"]}: mask type wrong')
                self.assertIn(f'vbool{bits}_t', self.text)

    def test_fixed_lane_constants(self):
        # Fixed entries must use exactly 2/4 (f64 m1/m2) or 4/8 (f32 m1/m2).
        self.assertEqual(self.info['_ZGV1N2v_log']['lanes'], 2)
        self.assertEqual(self.info['_ZGV2N4v_log']['lanes'], 4)
        self.assertEqual(self.info['_ZGV1N4v_logf']['lanes'], 4)
        self.assertEqual(self.info['_ZGV2N8v_logf']['lanes'], 8)
        # Masked entries are only log/exp; fixed entries use the ABI lane
        # count (2/4 for f64 m1/m2, 4/8 for f32 m1/m2), Nx entries use VLMAX.
        masked = [d for d in self.desc if d['macro'] == 'M64']
        self.assertEqual(len(masked), 8)
        self.assertTrue(all(d['fam'] in MASKED_FAMILIES for d in masked))
        for d in self.desc:
            if d['fixed']:
                base = 4 if d['macro'] == 'U32' else 2
                self.assertEqual(d['lanes'], base * d['lmul'], d['name'])
            else:
                self.assertEqual(d['lanes'], 0, d['name'])

    def test_ulp_budget_per_family(self):
        for d in self.desc:
            self.assertEqual(d['name'], d['cname'])
        # The ulp is selected by the descriptor family macro alias; verify the
        # alias table maps to the released thresholds.
        aliases = dict(re.findall(
            r'#define\s+ABI_ULP_(\w+)\s+ABI_ULP_(\w+)', self.text))
        self.assertEqual(set(aliases), set(ULP))
        header = parse_header_macros(_read(ABI_H))
        for fam, target in aliases.items():
            self.assertEqual(header['ulp'][target], ULP[fam], fam)

    def test_installed_libmvec_matches(self):
        prefix = Path(os.environ.get('LIBMVEC_TOOLCHAIN', DEFAULT_TOOLCHAIN))
        nm = prefix / 'bin' / 'riscv64-unknown-linux-gnu-nm'
        gcc = prefix / 'bin' / 'riscv64-unknown-linux-gnu-gcc'
        if not (nm.is_file() and gcc.is_file()):
            self.skipTest(f'installed toolchain not present at {prefix}')
        proc = subprocess.run([str(gcc), '-march=rv64gcv', '-mabi=lp64d',
                               '-print-file-name=libmvec.a'],
                              capture_output=True, text=True)
        lib = Path(proc.stdout.strip())
        if not lib.is_file():
            self.skipTest('libmvec.a not found via installed gcc')
        out = subprocess.run([str(nm), '-g', '--defined-only', str(lib)],
                             capture_output=True, text=True, check=True).stdout
        syms = {m.group(1) for m in re.finditer(r'\b(_ZGV\w+)\s*$', out, re.M)}
        syms = {s for s in syms if not s.endswith('_len')}
        self.assertEqual(syms, set(EXPECTED),
                         'installed libmvec exports a different public set')


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(ABI_C)

    def test_config_record(self):
        m = re.search(
            r'printf_\s*\("LIBMVEC_CONFIG case=abi-public function=abi '
            r'vlen_bits=%llu "\s*\n?\s*"seed=123456789 rounds=0\\n"', self.text)
        self.assertIsNotNone(m, 'CONFIG format/order changed')
        first_use = self.text.index('LIBMVEC_CONFIG')
        loop = self.text.index('for (unsigned i = 0; i < NSYMS; ++i)')
        self.assertLess(first_use, loop, 'CONFIG must be printed before symbols')

    def test_abi_record_unique_site(self):
        self.assertEqual(self.text.count('LIBMVEC_ABI'),
                         1, 'LIBNVEC_ABI must be printed from one site')
        self.assertIn('LIBMVEC_ABI symbol=%s checks=%llu failed=%llu\\n', self.text)

    def test_check_and_complete(self):
        self.assertEqual(self.text.count('LIBMVEC_CHECK'), 1)
        self.assertEqual(self.text.count('LIBMVEC_COMPLETE'), 1)
        self.assertIn('LIBMVEC_CHECK case=abi-public checks=%llu failed=%llu',
                      self.text)
        self.assertIn('LIBMVEC_COMPLETE case=abi-public status=pass', self.text)

    def test_error_path(self):
        self.assertGreaterEqual(self.text.count('LIBMVEC_ERROR'), 2)
        self.assertIn('return 1;', self.text)
        complete = self.text.index('LIBMVEC_COMPLETE')
        failure = self.text.index('if (abi_failed != 0)')
        self.assertLess(failure, complete,
                        'COMPLETE must be unreachable when checks failed')
        self.assertIn('if (abi_vlen_bits < 128)', self.text,
                      'program must reject VLEN < 128 explicitly')
        self.assertIn('__riscv_vlenb () * 8', self.text,
                      'VLEN must be measured at runtime, never assumed 128')

    def test_vl_perturb_is_volatile_asm(self):
        # An unused __riscv_vsetvl_* builtin is dead code; the clobber must be
        # a volatile vsetivli with a memory barrier so loads stay before it
        # and the call stays after it in the emitted code.
        self.assertIn('vsetivli zero, 1, e', self.text)
        self.assertIn('::: "memory"', self.text)
        self.assertNotIn('(void) __riscv_vsetvl_e', self.text)

    def test_vl_clobber_in_disassembly(self):
        prefix = Path(os.environ.get('LIBMVEC_TOOLCHAIN', DEFAULT_TOOLCHAIN))
        objdump = prefix / 'bin' / 'riscv64-unknown-linux-gnu-objdump'
        builds = sorted((HERE / 'build').glob('abi-development-*/abi.elf'),
                        key=lambda p: p.stat().st_mtime)
        if not builds or not objdump.is_file():
            self.skipTest('no built ELF/toolchain available for disassembly')
        text = subprocess.run([str(objdump), '-d', str(builds[-1])],
                              capture_output=True, text=True, check=True).stdout
        funcs, cur = {}, None
        for line in text.splitlines():
            m = re.match(r'^[0-9a-f]+ <([^>]+)>:$', line)
            if m:
                cur = m.group(1)
                funcs[cur] = []
            elif cur is not None:
                funcs[cur].append(line)
        wrappers = {n: body for n, body in funcs.items()
                    if re.fullmatch(r'[umb]__ZGV\w+', n)}
        self.assertEqual(len(wrappers), 36)
        setre = re.compile(r'\bvsetivli\s+zero,1,e(64|32),m(1|2),ta,ma\b')
        loadre = re.compile(r'\bvle(64|32)\.v\b')
        jalre = re.compile(r'\bjal\s+[0-9a-f]+ <(_ZGV\w+)>')
        calls = set()
        for name, body in wrappers.items():
            symbol = name[2:]
            asm = [i for i, l in enumerate(body) if setre.search(l)]
            self.assertEqual(len(asm), 1, f'{name}: lane-1 vsetivli')
            loads = [i for i, l in enumerate(body) if loadre.search(l)]
            self.assertTrue(loads and min(loads) < asm[0],
                            f'{name}: argument load before vl clobber')
            jal = [(i, m.group(1)) for i, l in enumerate(body)
                   for m in [jalre.search(l)] if m]
            after = [t for i, t in jal if i > asm[0]]
            self.assertTrue(after, f'{name}: call after vl clobber')
            self.assertEqual(after[0], symbol, f'{name}: call target')
            self.assertFalse([t for i, t in jal if i < asm[0]],
                             f'{name}: call before vl clobber')
            calls.add(symbol)
        self.assertEqual(len(calls), 36)
        self.assertEqual(len(re.findall(
            r'\bvsetivli\s+zero,1,e(?:64|32),m(?:1|2),ta,ma\b', text)), 36)

    def test_all_vlen_paths(self):
        # Nx entries take the runtime VLMAX, fixed entries the ABI constant;
        # abi.c must not contain a hardcoded 128-lane assumption.
        self.assertNotIn('== 128', self.text)
        self.assertIn('__riscv_vsetvlmax_e64m2 ()', self.text)
        self.assertIn('__riscv_vsetvlmax_e32m2 ()', self.text)
        self.assertIn('ABI_MAX_LANES', self.text)
        self.assertIn('adapt-tail', self.text, 'scalar tail handling missing')
        self.assertIn('adapt-inactive', self.text, 'inactive-lane handling missing')
        self.assertIn('snan-nv', self.text, 'active sNaN check missing')
        self.assertIn('sticky-flags', self.text, 'sticky flag check missing')
        self.assertIn('vl-independent', self.text, 'vl independence check missing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
