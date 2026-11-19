#!/usr/bin/env python3
"""五舍入直接观察解析器的自包含正反例；不运行目标仿真。"""
import argparse
import csv
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import importlib.util
import sys
from unittest.mock import patch as _module_patch


def _load_script(name, relative):
    path = Path(__file__).resolve().parents[2] / 'script' / relative
    spec = importlib.util.spec_from_file_location('_libmvec_host_' + name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 检查需要临时模块登记，不修改 sys.path。
    with _module_patch.dict(sys.modules, {spec.name: module}):
        spec.loader.exec_module(module)
    return module

o = _load_script('frm_observe', 'accuracy/frm_observe.py')


GOOD = '[src/cpu] nemu: HIT GOOD TRAP at pc = 0x80000122\n'
COMMANDS = [dict(label='nemu', status='0')]


def sample(function='log', *, config=True, vlenb=16, wrong_length=False, wrong_inputs=False):
    rows = [f'FRM_CONFIG fn={function} vlenb={vlenb}'] if config else []
    inputs = o.INPUT32 if function in ('expf', 'logf') else o.INPUT64
    width = 4 if function in ('expf', 'logf') else 8
    count = 0
    for iface in range(2):
        for m in (1, 2):
            full = m*vlenb//width
            for rm in range(5):
                for seed in range(2):
                    for pattern in range(16):
                        for shape in range(5 if iface else 1):
                            n = (1,full-1,full,full+1,2*full+1)[shape] if iface else full
                            if wrong_length and iface == 1 and m == 2 and shape == 4:
                                n = 1
                            k = f'fn={function} iface={iface} m={m} rm={rm} seed={seed} pattern={pattern} shape={shape}'
                            rows.append(f'FRM_CASE {k} n={n} after={rm} flags={24 if seed else 0} errno=0')
                            for lane in range(n):
                                x = inputs[(pattern+lane)%16]
                                z = inputs[(pattern+3*lane+1)%16]
                                if wrong_inputs and lane == 0:
                                    x += 1
                                rows.append(f'FRM_VALUE {k} lane={lane} x={x} z={z} y=30')
                                count += 1
    rows.append(f'FRM_COMPLETE fn={function} cases=1920 values={count} failed=0 status=pass')
    return GOOD, '\n'.join(rows)+'\n', COMMANDS


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs = sample()

    def parse(self, obs=None, function='log', **kwargs):
        return o.parse_observation(*(obs or self.obs), function, **kwargs)

    def test_complete_identical_and_counts(self):
        rows=o.compare(self.obs,self.obs,'log')
        self.assertEqual(20,len(rows))
        self.assertEqual((1920,6400,0,0), (sum(r['cases'] for r in rows),sum(r['value_count'] for r in rows),
                                         sum(r['state_diff'] for r in rows),sum(r['value_diff'] for r in rows)))

    def test_single_precision_patterns(self):
        cases,values,vlenb=self.parse(sample('logf'), 'logf')
        self.assertEqual((1920,12160,16),(len(cases),len(values),vlenb))

    def test_changed_output_recorded(self):
        changed=(self.obs[0],self.obs[1].replace('y=30','y=31',1),COMMANDS)
        rows=o.compare(self.obs,changed,'log')
        self.assertEqual(1,sum(r['value_diff'] for r in rows))

    def test_changed_flags_recorded(self):
        changed=(self.obs[0],self.obs[1].replace('after=0 flags=0 errno=0','after=0 flags=1 errno=0',1),COMMANDS)
        rows=o.compare(self.obs,changed,'log')
        self.assertEqual(1,sum(r['state_diff'] for r in rows))

    def test_missing_completion_rejected(self):
        with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1].replace('FRM_COMPLETE','NO_COMPLETE'),COMMANDS))

    def test_missing_trap_rejected(self):
        with self.assertRaises(ValueError): self.parse(('none',self.obs[1],COMMANDS))

    def test_trap_in_uart_or_fake_trap_rejected(self):
        with self.assertRaises(ValueError): self.parse(('',GOOD+self.obs[1],COMMANDS))
        with self.assertRaises(ValueError): self.parse(('HIT GOOD TRAP\n',self.obs[1],COMMANDS))

    def test_bad_markers_in_either_stream_rejected(self):
        for bad in o.BAD:
            for side in (0,1):
                streams=[GOOD,self.obs[1]]; streams[side]+=bad+'\n'
                with self.subTest(bad=bad,side=side), self.assertRaises(ValueError):
                    self.parse((streams[0],streams[1],COMMANDS))

    def test_nonzero_missing_or_duplicate_command_rejected(self):
        for records in ([],[dict(label='nemu',status='1')],COMMANDS*2):
            with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1],records))

    def test_missing_case_rejected(self):
        rows=self.obs[1].splitlines(); rows.pop(1)
        with self.assertRaises(ValueError): self.parse((GOOD,'\n'.join(rows),COMMANDS))

    def test_duplicate_record_rejected(self):
        duplicate=self.obs[1].splitlines()[1]
        with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1]+duplicate+'\n',COMMANDS))

    def test_bad_frm_rejected(self):
        with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1].replace('after=0 flags=0','after=2 flags=0',1),COMMANDS))

    def test_sticky_lost_rejected(self):
        with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1].replace('flags=24','flags=0',1),COMMANDS))

    def test_wrong_x_even_on_both_sides_rejected(self):
        wrong=sample(wrong_inputs=True)
        with self.assertRaises(ValueError): o.compare(wrong,wrong,'log')

    def test_wrong_z_rejected(self):
        changed=(GOOD,self.obs[1].replace(f'z={o.INPUT64[1]}',f'z={o.INPUT64[1]+1}',1),COMMANDS)
        with self.assertRaises(ValueError): self.parse(changed)

    def test_missing_lane_rejected(self):
        rows=self.obs[1].splitlines(); rows.pop(2)
        with self.assertRaises(ValueError): self.parse((GOOD,'\n'.join(rows),COMMANDS))

    def test_wrong_lengths_even_on_both_sides_rejected(self):
        wrong=sample(wrong_length=True)
        with self.assertRaises(ValueError): o.compare(wrong,wrong,'log')

    def test_legacy_requires_explicit_vlenb_and_matches(self):
        old=sample(config=False)
        with self.assertRaises(ValueError): self.parse(old)
        self.assertEqual(16,self.parse(old,legacy_vlenb=16)[2])
        with self.assertRaises(ValueError): self.parse(old,legacy_vlenb=32)
        with self.assertRaises(ValueError): self.parse(self.obs,legacy_vlenb=32)

    def test_duplicate_or_invalid_vlenb_rejected(self):
        with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1]+'FRM_CONFIG fn=log vlenb=16\n',COMMANDS))
        with self.assertRaises(ValueError): self.parse((GOOD,self.obs[1].replace('vlenb=16','vlenb=7',1),COMMANDS))

    def test_command_file_matches_stream_paths(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t); out=p/'out'; err=p/'err'; out.touch(); err.touch()
            commands=p/'commands.csv'
            o.write_csv(commands, ('label','status','stdout','stderr'), [dict(label='nemu',status='0',stdout=str(out),stderr=str(err))])
            self.assertEqual(1,len(o.read_commands(commands,out,err)))
            with self.assertRaises(ValueError): o.read_commands(commands,err,out)

    def test_uart_records_in_control_stdout_rejected(self):
        with self.assertRaises(ValueError): self.parse((GOOD+'FRM_CASE fn=log\n',self.obs[1],COMMANDS))

    def test_commands_and_identities_survive_failed_validation(self):
        # Stub subprocess: create link outputs and return rc=0, but no valid NEMU trap.
        with tempfile.TemporaryDirectory() as t:
            p=Path(t); gcc=p/'gcc'; lib=p/'math.a'; archive=p/'new.a'; nemu=p/'nemu'
            objcopy=p/'objcopy'; libc=p/'libc.a'; libgcc=p/'libgcc.a'
            for f in (gcc,lib,archive,nemu,objcopy,libc,libgcc): f.write_bytes(b'test')
            args=argparse.Namespace(build_dir=p/'artifacts',run_dir=p/'20261114-180000-test',gcc=gcc,archive=archive,
                                    scalar_libm=lib,nemu=nemu,build_only=False,function='log',max_instr=100,timeout=5)
            def fake(argv,*,cwd,env,stdout,stderr,timeout):
                self.assertTrue(Path(env['TMPDIR']).is_dir())
                if '-print-prog-name=objcopy' in argv: stdout.write(str(objcopy))
                elif '-print-file-name=libc.a' in argv: stdout.write(str(libc))
                elif '-print-file-name=libgcc.a' in argv: stdout.write(str(libgcc))
                elif '-o' in argv: Path(argv[argv.index('-o')+1]).write_bytes(b'fake-elf-or-object')
                elif '--set-section-flags' in argv: Path(argv[-1]).write_bytes(b'fake-bin')
                return types.SimpleNamespace(returncode=0)
            with patch.object(o.subprocess,'run',side_effect=fake), self.assertRaises(ValueError): o.build_run(args)
            with (args.run_dir/'commands.csv').open(newline='') as f: commands=list(csv.DictReader(f))
            with (args.run_dir/'identities.csv').open(newline='') as f: identities=list(csv.DictReader(f))
            self.assertEqual('0',next(row['status'] for row in commands if row['label']=='nemu'))
            self.assertTrue({'elf','binary','staged-bin','runtime','runtime-impl','runtime-header','linker','observer'} <= {row['role'] for row in identities})
            self.assertEqual(10,len(commands))


if __name__ == '__main__': unittest.main()
