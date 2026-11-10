import copy
import json
from pathlib import Path
import tempfile
import unittest
from analyze import analyze, stats

def sample(mode,n,k,phase,r,cycles):
    return dict(mode=mode,n=n,k=k,phase=phase,round=r,cycles=cycles,cycle_begin=2**60+r,
        cycle_end=2**60+r+cycles,checksum=2**63+5,order=0)

def document():
    modes=['scalar_strict','scalar_fast','auto_m1','auto_m2','direct_m1','direct_m2']
    rows=[];cal=[]
    for k in (1,4,16):
        for phase,count in [('first',1),('warmup',2),('steady',7)]:
            for mode in modes:
                for r in range(count): rows.append(sample(mode,16,k,phase,r,160*k if mode.startswith('scalar') else 80*k))
            for mode in ('rdcycle','empty','copy'):
                for r in range(count): cal.append(sample(mode,16,k,phase,r,1 if mode=='rdcycle' else 2*k))
    return dict(mode='gsim',state='passed',all_passed=True,run='/test/run1',manifest={'snapshot_sha256':'a'*64},
        tools={k:{'sha256':k} for k in ('gsim','reference','dram_config')},cases=[
        dict(name='perf-log',suite='performance',status='passed',measurement_valid=True,vlen_bits=128,
            staged_sha256='a'*64,sample_records=rows,calibration_records=cal)])

class AnalyzeTests(unittest.TestCase):
    def call(self,doc):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text(json.dumps(doc));return analyze([p])
    def test_normalization_keeps_k_and_phase_separate(self):
        got=self.call(document())
        groups=[g for g in got['groups'] if g['kind']=='math' and g['mode']=='auto_m1' and g['phase']=='steady']
        self.assertEqual(len(groups),3)
        for g in groups:
            self.assertEqual(g['count'],7);self.assertEqual(g['cycles_per_element'],5)
            self.assertEqual(g['cycles_per_call'],80);self.assertEqual(g['speedup_vs_scalar_strict'],2)
            self.assertEqual(g['normalized_change_vs_k1'],0)
        self.assertFalse(got['full_acceptance'])
    def test_exact_cycle_values_preserved(self):
        rows=[sample('auto_m1',1,1,'steady',r,2**60+r) for r in range(7)]
        result=stats(rows)
        self.assertEqual(result['raw_cycles'],[2**60+r for r in range(7)])
        self.assertEqual(result['median_cycles'],2**60+3)
        self.assertEqual(result['raw_begin'][0],2**60)
    def test_reject_nemu_or_partial(self):
        for field,value in [('mode','nemu'),('state','in_progress'),('all_passed',False)]:
            doc=document();doc[field]=value
            with self.assertRaises(ValueError):self.call(doc)
    def test_reject_mixed_build_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            a=document();b=document();b['run']='/test/run2';b['manifest']['snapshot_sha256']='b'*64
            paths=[]
            for i,doc in enumerate((a,b)):
                p=Path(d)/f'{i}.json';p.write_text(json.dumps(doc));paths.append(p)
            with self.assertRaisesRegex(ValueError,'different build manifests'):analyze(paths)

    def test_reject_missing_round(self):
        doc=document();doc['cases'][0]['sample_records'].pop()
        with self.assertRaises(ValueError):self.call(doc)
    def test_no_division_of_rdcycle_by_k(self):
        got=self.call(document())
        rows=[g for g in got['groups'] if g['mode']=='rdcycle']
        self.assertTrue(all(g['cycles_per_call'] is None and g['cycles_per_element'] is None for g in rows))
    def test_fixture_sensitivity(self):
        doc=document()
        for r in doc['cases'][0]['sample_records']:
            if r['k']==4 and r['mode']=='auto_m1':
                r['cycles']*=2;r['cycle_end']=r['cycle_begin']+r['cycles']
        got=self.call(doc)
        row=next(g for g in got['groups'] if g['mode']=='auto_m1' and g['k']==4 and g['phase']=='steady')
        self.assertTrue(row['fixture_sensitive']);self.assertEqual(row['normalized_change_vs_k1'],1)
if __name__=='__main__':unittest.main()
