"""配对 wire、运行回执和计时门槛的主机回归；不运行目标。"""
from copy import deepcopy
import csv
from datetime import datetime
from fractions import Fraction
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'script' / 'latency'
sys.path.insert(0, str(SCRIPT))
import build_latency as builder
import paired_parse as wire
import run_latency
import run_paired as runner


CASE = 'atan|m1|fixed|unmasked'
PROBE = 'm1_fixed_unmasked'
GOOD = 'Core 0: \x1b[32mHIT GOOD TRAP at pc = 0x80000122\x1b[0m\n'
SUCCESS = dict(returncode=0, timed_out=False, launch_error=None, aborted=False,
               elapsed_seconds=1)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get('TEST_TMPDIR', tempfile.gettempdir())).resolve()
        self.assertFalse(any(part.lower() == 'build' or part.lower().startswith('build-')
                             for part in base.parts))
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.tmp = tempfile.TemporaryDirectory(prefix=f'{stamp}-paired-host-', dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.header = self.root / 'atan.h'
        words = '0x3ff0000000000000ULL, 0x4000000000000000ULL'
        self.header.write_text(f'#define DATA_COUNT 2\n#define ORACLE_LIMIT 2\n'
                               f'static const uint64_t input_bits[DATA_COUNT] = {{{words}}};\n'
                               f'static const uint64_t reference_bits[DATA_COUNT] = {{{words}}};\n')
        self.image = self.root / 'paired-demo.bin'
        self.image.write_bytes(b'host fixture, not an executable image')
        archive = {'path': '/example/libvec.a', 'sha256': 'a' * 64, 'members': ['atan.o']}
        self.manifest = {
            'schema_version': 2, 'suite': 'libmvec-paired', 'protocol': 2,
            'wire_format': 'pair-compact-r', 'build_status': 'built', 'tag': 'demo',
            'candidate': {'id': 'guest', 'lto': 'off', 'cross_lmul': 'none'},
            'functions': ['atan'],
            'cases': [{'case': CASE, 'function': 'atan', 'lanes': 2, 'probe': PROBE,
                       'modes': ['latency', 'tput']}],
            'knobs': dict(only_cases=PROBE, lengths='2', batches='4', paired_k='0,1',
                          streams='1,2', modes='both', rounds=8, serial=1, trace=False,
                          timing_repeat='auto', vlen=128, vlenb=16, calib=False,
                          fixed_lanes={'m1': 2, 'm2': 4}, scalable_lanes={'m1': 2, 'm2': 4}),
            'data': {'frozen': True, 'headers': {'atan.h': dict(
                path=str(self.header), snapshot=str(self.header),
                sha256=wire.common.sha256(self.header), DATA_COUNT=2, ORACLE_LIMIT=2)}},
            'vector_archive': archive, 'math_archive': None,
            'audit': {'status': 'ok', 'findings': []},
            'artifacts': {self.image.name: dict(path=str(self.image),
                sha256=wire.common.sha256(self.image), bytes=self.image.stat().st_size)},
        }
        self.plan = wire.load_plan(self.manifest)
        # 独立固定值：C 的 FNV 按 uint64 word 处理，不是逐字节哈希。
        self.log = [
            'PAIR_HEADER,protocol=2,vlenb=16,vlen=128,candidate=guest,lto=off,modes=both,'
            'lengths=2,batches=4,streams=1,2,k=0,1,rounds=8,serial=1,trace=0,'
            'seed_order=scalar_prefix,latency_batch=1,timing_repeat=auto,window_min=20000,'
            'window_target=40000,max_repeat=64,min_steady=5,only_cases=m1_fixed_unmasked\n',
            f'PAIR_CASE,0,{CASE},atan,2\n',
            'PAIR_SEED,function=atan,N=2,count=2,unary_fnv=a1191300c548b8fb,pair_fnv=a1191300c548b8fb\n',
            f'PAIR_CHECK,case={CASE},mode=latency,N=2,checks=36,failed=0\n',
            f'PAIR_CHECK,case={CASE},mode=tput,N=2,checks=36,failed=0\n',
            f'PAIR_SKIP,case={CASE},mode=tput,kind=ncall_not_divisible_by_streams,N=2,L=2,B=4,K=0,streams=2\n',
            f'PAIR_SKIP,case={CASE},mode=tput,kind=ncall_not_divisible_by_streams,N=2,L=2,B=4,K=1,streams=2\n',
            f'PAIR_WARN,case={CASE},mode=latency,N=2,B=1,K=1,S=1,kind=window_too_short,'
            'window_min=20000,window_cycles=19840,timing_repeat=64\n',
        ]
        for mode, batch in [('l', 1), ('t', 4)]:
            for k in (0, 1):
                rep = 64 if mode == 'l' else 3
                for r in range(8):
                    self.log.append(f'P,0,{mode},2,{batch},{k},1,{r},{k * batch * rep},'
                                    f'{32000 if k else 97},{rep}\n')
        self.log.append('PAIR_END,status=ok,checks=72,failed=0,records=32\n')

    def test_complete_wire_and_buffered_checks(self):
        points, warns, count = wire.parse_wire(self.log, self.plan)
        self.assertEqual((len(points), sum(len(p.cycles) for p in points.values()), count), (4, 32, 72))
        self.assertEqual(warns, {(0, 'latency', 2, 1, 1, 1): 19840})
        self.assertEqual(points[(0, 'tput', 2, 4, 1, 1)].repeat, 3)

    def test_reject_invalid_wire(self):
        variants = {
            'case mapping missing': self.log[:1] + self.log[2:],
            'case mapping duplicate': self.log[:2] + self.log[1:],
            'seed missing': self.log[:2] + self.log[3:],
            'seed hash wrong': self.log[:2] + [self.log[2].replace('a1191300c548b8fb', '0')] + self.log[3:],
            'check missing': self.log[:3] + self.log[4:],
            'skip missing': self.log[:5] + self.log[6:],
            'warn duplicate': self.log[:8] + self.log[7:],
            'duplicate window': self.log[:9] + self.log[8:],
            'forged footer': self.log[:8] + self.log[9:-1] + [self.log[-1].replace('32', '31')],
            'late row': self.log + [self.log[8]],
            'duplicate footer': self.log + [self.log[-1]],
            'truncated footer': self.log[:-1] + [self.log[-1].rstrip('\n')],
            'partial': self.log[:-1],
        }
        for replace in [('0,l,', '0,z,'), (',64\n', ',65\n'), (',64\n', '\n'),
                        ('P,', 'PAIR,trace=1,'), (',97,', ',-97,'), (',0,97,', ',1,97,')]:
            variants[replace] = self.log[:8] + [self.log[8].replace(*replace)] + self.log[9:]
        for label, rows in variants.items():
            with self.subTest(label=label), self.assertRaises(wire.ProtocolError):
                wire.parse_wire(rows, self.plan)

    def test_reject_bad_manifest(self):
        for key, name, value in [
            ('knobs', 'trace', True), ('data', 'frozen', False), ('audit', 'status', 'fail'),
            ('knobs', 'timing_repeat', True), ('knobs', 'rounds', 65536),
            ('knobs', 'only_cases', 'm2_fixed_unmasked'), ('knobs', 'vlenb', 32),
        ]:
            bad = deepcopy(self.manifest)
            bad[key][name] = value
            with self.subTest(name=name), self.assertRaises(wire.ProtocolError):
                wire.load_plan(bad)
        self.header.write_text('changed after freeze\n')
        with self.assertRaisesRegex(wire.ProtocolError, 'SHA256'):
            wire.load_plan(self.manifest)

    def unary_header(self, fn, inputs, references):
        path = self.root / f'{fn}.h'
        words = lambda values: ', '.join(f'0x{value:016x}ULL' for value in values)
        path.write_text(f'#define DATA_COUNT {len(inputs)}\n#define ORACLE_LIMIT 2\n'
                        f'static const uint64_t input_bits[DATA_COUNT] = {{{words(inputs)}}};\n'
                        f'static const uint64_t reference_bits[DATA_COUNT] = {{{words(references)}}};\n')
        return dict(path=str(path), snapshot=str(path), sha256=wire.common.sha256(path),
                    DATA_COUNT=len(inputs), ORACLE_LIMIT=2)

    def test_sin_exp_shared_manifest_and_complete_wire(self):
        manifest = deepcopy(self.manifest)
        manifest['functions'] = ['exp', 'sin']  # Guest registration order is independent of CLI order.
        manifest['cases'] = [dict(self.manifest['cases'][0], function=fn,
                                 case=CASE.replace('atan', fn)) for fn in ('sin', 'exp')]
        manifest['data']['headers'] = {
            fn + '.h': self.unary_header(fn, [0x3ff0000000000000, 0x4000000000000000],
                                        [0x3ff0000000000000, 0x3ff0000000000000])
            for fn in ('sin', 'exp')}
        plan = wire.load_plan(manifest)
        self.assertEqual(tuple(case['function'] for case in plan.cases), ('sin', 'exp'))
        rows = [self.log[0], self.log[1].replace('atan', 'sin'),
                self.log[1].replace('atan', 'exp').replace('PAIR_CASE,0,', 'PAIR_CASE,1,')]
        for i, fn in enumerate(('sin', 'exp')):
            rows += [row.replace('atan', fn).replace('P,0,', f'P,{i},')
                     for row in self.log[2:-1]]
        rows += ['PAIR_END,status=ok,checks=144,failed=0,records=64\n']
        points, _, checks = wire.parse_wire(rows, plan)
        self.assertEqual((len(points), checks), (8, 144))
        for i, old, new in [(1, 'sin', 'sinf'), (2, 'unmasked', 'masked'),
                            (3, 'a1191300c548b8fb', '0')]:
            with self.subTest(old=old), self.assertRaises(wire.ProtocolError):
                wire.parse_wire(rows[:i] + [rows[i].replace(old, new)] + rows[i + 1:], plan)
        with self.assertRaises(wire.ProtocolError):
            wire.parse_wire(rows[:-1], plan)

    def test_exp_positive_cap_and_finite_bridge_contract(self):
        def bits(value):
            return int.from_bytes(struct.pack('>d', value), 'big')

        for fn, modes, word, ref, error in [
            ('exp', 'latency', bits(709.0), bits(1.0), None),
            ('exp', 'latency', bits(709.0) + 1, bits(1.0), 'limit 709'),
            ('exp', 'both', bits(687.0), bits(1.0), None),
            ('exp', 'tput', bits(687.0) + 1, bits(1.0), 'limit 687'),
            ('exp', 'both', bits(-800.0), bits(0.0), None),
            ('sin', 'both', bits(1e200), bits(0.5), None),
            ('sin', 'both', bits(-0.0), bits(0.0), 'finite normal seed'),
            ('sin', 'both', 1, bits(0.0), 'finite normal seed'),
            ('sin', 'both', bits(1.0), bits(float('inf')), 'finite reference'),
        ]:
            with self.subTest(fn=fn, modes=modes, word=word, ref=ref):
                info = self.unary_header(fn, [word], [ref])
                snapshot = Path(info['snapshot'])
                if error:
                    with self.assertRaisesRegex(wire.ProtocolError, error):
                        wire.frozen_seeds(snapshot, info, (1,), fn, modes)
                else:
                    count, prefix = wire.frozen_seeds(snapshot, info, (1,), fn, modes)
                    self.assertEqual(count, 1)
                    self.assertEqual(prefix[1]['unary_fnv'], prefix[1]['pair_fnv'])

    @unittest.skipUnless(shutil.which('cc'), 'host C preprocessor unavailable')
    def test_shared_unmasked_case_slice_excludes_masked_callee_uses(self):
        selected = ['m1_fixed_unmasked', 'm1_scalable_unmasked',
                    'm2_fixed_unmasked', 'm2_scalable_unmasked']
        args = SimpleNamespace(only_case=','.join(selected), modes='both', vlen=256, calib=False)
        cases, only_cases = builder.pair_cases(('sin', 'exp'), args)
        self.assertEqual(only_cases, ';'.join(selected))
        self.assertEqual([(case['function'], case['lanes']) for case in cases],
                         [(fn, lanes) for fn in ('sin', 'exp') for lanes in (2, 4, 4, 8)])
        (self.root / 'riscv_vector.h').write_text('/* Host preprocessing only. */\n')
        mask = builder.pair_probe_mask(selected)
        self.assertEqual(mask, 0x33)
        for fn in ('sin', 'exp'):
            self.unary_header(fn, [0x3ff0000000000000], [0x3ff0000000000000])
            cpp = subprocess.run(['cc', '-E', '-P', '-D__riscv_vector=1',
                f'-DPAIRED_PROBE_MASK={mask}', '-I', str(self.root), '-I',
                str(builder.REPO / 'autovec'), str(builder.LATENCY / f'paired_{fn}.c')],
                cwd=self.root, check=True, capture_output=True, text=True).stdout
            chosen = builder.pair_audit_specs(fn, selected)
            self.assertEqual(len(chosen), 8)
            self.assertEqual({spec['callee'] for spec in chosen.values()},
                             {f'_ZGV{shape}v_{fn}' for shape in ('1N2', '1Nx', '2N4', '2Nx')})
            for probe, spec in builder.pair_audit_specs(fn).items():
                # Unselected callee names occur only in their harmless extern declaration.
                if probe in chosen:
                    self.assertIn(probe, cpp)
                    self.assertGreater(cpp.count(spec['callee']), 1)
                else:
                    self.assertNotIn(probe, cpp)
                    self.assertEqual(cpp.count(spec['callee']), 1)

    def test_fixed_repeat_cannot_change(self):
        plan = deepcopy(self.manifest)
        plan['knobs']['timing_repeat'] = 64
        log = [line.replace('timing_repeat=auto', 'timing_repeat=64').replace('checks=36', 'checks=32')
               for line in self.log if not line.startswith('PAIR_WARN')]
        with self.assertRaisesRegex(wire.ProtocolError, 'R 与固定'):
            wire.parse_wire(log, wire.load_plan(plan))

    def test_diagnostics_are_not_cross_line_or_reference_traps(self):
        log = ''.join(self.log)
        runner.audit_diagnostics(log + GOOD, '', 'PAIR_END,')
        for bad_stdout, bad_stderr in [
            ('Core 0:\nNEMU: HIT GOOD TRAP\n', ''), ('NEMU: ' + GOOD, ''),
            (GOOD.replace('Core 0', 'Core 1'), ''), (GOOD * 2, ''), ('', GOOD),
            (GOOD.rstrip('\n'), ''), (GOOD, 'DiffTest is disabled\n'),
            (GOOD, 'DIFFTEST: mismatch at an instruction\n'),
            (GOOD, 'HIT BAD TRAP\n'), (GOOD, 'timeout\n'),
        ]:
            with self.subTest(stdout=bad_stdout, stderr=bad_stderr), self.assertRaises(wire.ProtocolError):
                runner.audit_diagnostics(log + bad_stdout, bad_stderr, 'PAIR_END,')

    def test_audit_runs_require_exact_process_and_frozen_identity(self):
        doc = self.root / 'manifest.json'
        doc.write_text(json.dumps(self.manifest))
        emu = self.root / 'host-emulator-fixture'
        ref = self.root / 'host-reference-fixture'
        ini = self.root / 'dram-config'
        for p in (emu, ref, ini):
            p.write_text(p.name + '\n')
        args = SimpleNamespace(seed=123456789, timeout='1s', kind='gsim', emu=emu, reference=ref,
                               dramsim3_ini=ini, output=self.root / (self.root.name + '-run'))

        def launch(command, cwd, stdout, stderr, timeout, registry):
            self.assertEqual(command[1:3], ['--diff', str(args.output / 'reference.so')])
            self.assertEqual(command[-2:], ['-i', str(args.output / 'image.bin')])
            self.assertEqual((args.output / 'corpus/atan.h').read_bytes(), self.header.read_bytes())
            stdout.write_text(''.join(self.log) + GOOD)
            stderr.write_text('')
            return SUCCESS.copy()

        with patch.object(wire.common, 'tool_identity', side_effect=lambda p, **kw: wire.common.identity(p)), \
             patch.object(wire.common, 'dynamic_executable_info'), \
             patch.object(wire.common, 'shared_object_info'), \
             patch.object(wire.common, 'validate_reference') as validate, \
             patch.object(wire.common, 'launch_process', side_effect=launch):
            run = runner.launch_rtl(args, self.image, self.plan.bin_info,
                                    build=(doc, wire.common.sha256(doc), self.manifest['data']['headers']))
            parsed = runner.audit_run(run)
            self.assertEqual(parsed[3], 72)
            validate.assert_called_with(run / 'reference.so')
            original = parsed[-1]
            for field, value in [('returncode', 1), ('timed_out', True), ('aborted', True),
                                 ('launch_error', 'failed'), ('returncode', False)]:
                bad = deepcopy(original)
                bad['result'][field] = value
                wire.common.write_json(run / 'execution.json', bad)
                with self.subTest(field=field, value=value), self.assertRaises(wire.ProtocolError):
                    runner.audit_run(run)
            bad = deepcopy(original)
            bad['command'].insert(1, '--no-diff')
            wire.common.write_json(run / 'execution.json', bad)
            with self.assertRaisesRegex(wire.ProtocolError, '命令'):
                runner.audit_run(run)
            wire.common.write_json(run / 'execution.json', original)
            with patch.object(wire.common, 'validate_reference',
                              side_effect=wire.ProtocolError('missing exports')):
                with self.assertRaisesRegex(wire.ProtocolError, 'exports'):
                    runner.audit_run(run)
            out = self.root / 'analysis'
            runner.report(out, *parsed)
            with (out / 'cells.csv').open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row['quality'] for row in rows], ['control', 'eligible', 'control', 'eligible'])
            self.assertEqual(float(rows[-1]['median_cycles_per_call']), 32000 / 12)
            (run / 'reference.so').write_text('different reference')
            with self.assertRaisesRegex(wire.ProtocolError, 'SHA256'):
                runner.audit_run(run)

    def test_cv_boundary_and_fit(self):
        self.assertEqual(runner.quality([20000] * 4, 1)[0], 'insufficient-steady')
        self.assertEqual(runner.quality([19999] + [20000] * 4, 1)[0], 'short-window')
        self.assertEqual(runner.quality([29700, 29700, 30000, 30300, 30300], 1), ('eligible', 0.01))
        self.assertEqual(runner.quality([29699, 29700, 30000, 30300, 30301], 1)[0], 'unstable')
        self.assertEqual(runner.quality([], 0), ('control', None))
        self.assertEqual(tuple(runner.fit([(4, Fraction(20064, 8)), (8, Fraction(40064, 8))])),
                         (625.0, 8.0, 0.0))

    def test_historical_lat_has_separate_coverage(self):
        text = ('LAT_HEADER,protocol=1,vlenb=16,vlen=128,rounds=1,prelude=0,serial=1,'
                'nop_block=8,nop_block64=64,k=0,groups=calib,entries=nop_addi\n'
                'LAT_CHECK,entry=nop_addi,checks=0,failed=0\n'
                'LAT,entry=nop_addi,K=0,phase=first,calls=0,begin=0,cycles=97,checksum=0\n'
                'LAT_CHAIN,entry=nop_addi,K=0,chain_checksum=0,stable=yes\n'
                'LAT_END,status=ok,checks=0,failed=0\n' + GOOD)
        self.assertEqual(run_latency.audit_lat(text, '')['lat_windows'], 1)
        with self.assertRaises(wire.ProtocolError):
            wire.parse_wire(io.StringIO(text), self.plan)
        with self.assertRaises(ValueError):
            run_latency.audit_lat(text.replace('LAT,entry', 'removed,entry'), '')
