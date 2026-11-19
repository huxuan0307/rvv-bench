"""短波形运行器纯主机测试；不执行模拟器。"""
from pathlib import Path
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "script"
sys.path.insert(0, str(SCRIPT))

import run_trace_verilator as trace


class TraceRunnerTests(unittest.TestCase):
    def test_strict_good_result_and_separate_streams(self):
        row='TRACE_RESULT n=16 k=1 begin=10 end=17 cycles=7 failed=0\n'
        good,values,issues=trace.analyze_logs('Core 0: HIT GOOD TRAP\n',row,16,1)
        self.assertTrue(good); self.assertEqual(values,(16,1,10,17,7,0)); self.assertEqual(issues,[])
        cases=[('',row), ('Core 0: HIT GOOD TRAP\n',row.replace('failed=0','failed=1')),
               ('Core 0: HIT GOOD TRAP\n',row.replace('cycles=7','cycles=8')),
               ('Core 0: HIT GOOD TRAP\n',row+row),
               ('Core 0: HIT GOOD TRAP\nTRACE_RESULT n=16 ', 'k=1 begin=10 end=17 cycles=7 failed=0\n'),
               ('Core 0: HIT GOOD TRAP\nDIFFTEST FAIL\n',row)]
        for so,se in cases:
            with self.subTest(stdout=so,stderr=se):
                self.assertTrue(trace.analyze_logs(so,se,16,1)[2])

    def test_command_and_output_guard(self):
        case=Path('/tmp/runs/20261114-170000-x/a')
        cmd=trace.case_command('/emu','/ref','/dram.ini',case,case/'x.bin',case/'trace.fst',123)
        self.assertEqual(cmd[-6:],['-b','0','-e','30000','-C','100000'])
        self.assertIn('--diff=/ref',cmd); self.assertIn('--seed=123',cmd)
        self.assertIn('--wave-path='+str(case/'trace.fst'),cmd)
        for bad in ('/tmp/build/20261114-170000-x','/tmp/runs/no-date'):
            with self.subTest(path=bad),self.assertRaises(ValueError): trace.prepare_output(bad)
        self.assertEqual(trace.parse_label('base=/tmp/m.json')[0],'base')
        for bad in ('../x=/tmp/m.json','base=','nodivider'):
            with self.subTest(label=bad),self.assertRaises(Exception): trace.parse_label(bad)


if __name__=='__main__': unittest.main()
