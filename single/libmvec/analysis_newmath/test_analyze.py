"""合成fixtures只验证工具，绝非gsim实测。"""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
import analyze as a


def fixture_rows(functions, factor=1):
    rows=[]
    for fn,mode,n,k,r in a.matrix(functions):
        ph,ro=a.phase(r)
        cycles=(100+a.MODES.index(mode)*10+r)*factor
        rows.append(dict(record_type='sample' if mode in a.MODES[:6] else 'calibration',case='perf-'+fn,suite='performance',function=fn,mode=mode,n=str(n),k=str(k),phase=ph,round=str(ro),order=str((a.MODES.index(mode)-r)%9),r=str(r),cycle_begin='1000',cycle_end=str(1000+cycles),cycles=str(cycles),checksum='123',wire_format='fixture-not-gsim',raw='SYNTHETIC',run_status='passed',case_status='passed',measurement_valid='True'))
    return rows


def campaign(root):
    h='a'*64
    gates=[]
    instructions=[]
    for family,fn in __import__('itertools').product(a.FAMILIES,a.FUNCTIONS):
        gates.append(dict(family=family,function=fn,elf_sha256=h,bin_sha256='b'*64,baseline_validation={'passed':True,'evidence_sha256':h},nemu_passed=True,abi_passed=True,oracle_passed=True,rva23_passed=True,implementation='shared-register-core',stack_bridge=False))
        instructions.append(dict(family=family,function=fn,bin_sha256='b'*64,nemu_total_instructions=123,scope='whole-program'))
    (root/'gates.json').write_text(json.dumps(dict(campaign_id=a.CAMPAIGN,complete=True,gates=gates)))
    a.write_csv(root/'instructions.csv',instructions)
    bound=lambda p:dict(path=p,sha256=a.sha(root/p))
    runs=[]
    for family,role in __import__('itertools').product(a.FAMILIES,('main','replay')):
        funcs=a.FUNCTIONS if role=='main' else ('log','exp')
        name=family+'-'+role
        identity=dict(fixture_sha256=h,scalar_objects={fn:{m:h for m in a.MODES[:2]} for fn in a.FUNCTIONS},auto_objects={fn:{m:h for m in a.MODES[2:4]} for fn in a.FUNCTIONS},direct_objects={fn:{m:h for m in a.MODES[4:6]} for fn in a.FUNCTIONS},gsim_sha256=h,reference_sha256=h,dram_config_sha256=h,abi='Nx',elf_sha256={fn:h for fn in funcs},bin_sha256={fn:'b'*64 for fn in funcs})
        a.write_csv(root/(name+'.csv'),fixture_rows(funcs,2 if family!='new' else 1))
        result=dict(mode='gsim',complete=True,all_passed=True,state='passed',audit_issues=[],final_bin_sha256={fn:'b'*64 for fn in funcs},analysis_identity=identity)
        (root/(name+'.json')).write_text(json.dumps(result))
        runs.append(dict(family=family,role=role,samples=bound(name+'.csv'),results=bound(name+'.json'),identity=identity))
    m=dict(schema_version=1,campaign_id=a.CAMPAIGN,profile='main-nx',runs=runs,gates=bound('gates.json'),instructions=bound('instructions.csv'))
    (root/'manifest.json').write_text(json.dumps(m))
    return m


class TestAnalysis(unittest.TestCase):
    def test_full_matrix(self):
        rows=a.validate_samples(fixture_rows(a.FUNCTIONS),a.FUNCTIONS)
        self.assertEqual(len(rows),4320)
        self.assertEqual(sum(r['phase']=='steady' for r in rows),3024)

    def test_partial_duplicate_rotation_invalid(self):
        rows=fixture_rows(('log',))
        for mutated in (rows[:-1],rows+[rows[0]]):
            with self.assertRaises(a.Rejected):a.validate_samples(mutated,('log',))
        for field,value in [('order','7'),('measurement_valid','False'),('phase','steady'),('cycle_end','0'),('mode','direct_array'),('cycle_begin',str(1<<64)),('cycles',str(1<<64))]:
            bad=copy.deepcopy(rows);bad[0][field]=value
            with self.assertRaises(a.Rejected):a.validate_samples(bad,('log',))

    def test_u64_wrap(self):
        rows=fixture_rows(('sin',))
        rows[0]['cycle_begin']=str((1<<64)-10)
        rows[0]['cycle_end']=str(int(rows[0]['cycles'])-10)
        a.validate_samples(rows,('sin',))

    def test_aggregate_ratios(self):
        samples=[]
        for family,role in __import__('itertools').product(a.FAMILIES,('main','replay')):
            rows=a.validate_samples(fixture_rows(('log',),2 if family!='new' else 1),('log',))
            for row in rows: row.update(family=family,role=role)
            samples.extend(rows)
        groups,pairs,speedups,replay=a.aggregate(samples)
        self.assertTrue(all(p['ratio_of_medians']==(2 if p['comparison']=='new' else 1) for p in pairs))
        self.assertTrue(all(p['median_of_paired_ratios']==(2 if p['comparison']=='new' else 1) for p in pairs))
        self.assertTrue(all(p['replay_over_main']==1 for p in replay))
        self.assertTrue(all(g['count']==7 for g in groups if g['phase']=='steady'))
        svg=a.svg_plot(groups,'log')
        self.assertIn('zero baseline',svg)
        self.assertNotIn('logarith',svg)

    def test_campaign_end_to_end_and_rejections(self):
        with tempfile.TemporaryDirectory(prefix='analysis-fixture-') as tmp:
            root=Path(tmp)
            m=campaign(root)
            samples,instructions,provenance=a.load_campaign(root/'manifest.json',root)
            self.assertEqual(len(samples),17820)
            a.render(root/'synthetic-output',samples,instructions,provenance)
            self.assertTrue((root/'synthetic-output'/'sin.svg').is_file())
            with self.assertRaises(a.Rejected):a.render(root/'synthetic-output',samples,instructions,provenance)
            # 错profile、身份漂移、缺复跑、旧root均必须拒绝。
            for mutate in ('profile','identity','missing','bin-elf-swap'):
                bad=copy.deepcopy(m)
                if mutate=='profile':bad['profile']='historical-gcv'
                if mutate=='identity':bad['runs'][0]['identity']['gsim_sha256']='b'*64
                if mutate=='missing':bad['runs'].pop()
                if mutate=='bin-elf-swap':bad['runs'][0]['identity']['bin_sha256']=bad['runs'][0]['identity']['elf_sha256']
                (root/'bad.json').write_text(json.dumps(bad))
                with self.assertRaises(a.Rejected):a.load_campaign(root/'bad.json',root)
            with self.assertRaises(a.Rejected):a.load_campaign(root/'manifest.json',root/'unrelated')
            # 采用独立receipt，原results去掉附加身份字段仍可通过；raw snapshot tamper拒绝。
            run=m['runs'][0]
            rp=root/run['results']['path'];result=json.loads(rp.read_text())
            identity=result.pop('analysis_identity');bins=result.pop('final_bin_sha256')
            rp.write_text(json.dumps(result));run['results']['sha256']=a.sha(rp)
            receipt=dict(campaign_id=a.CAMPAIGN,family=run['family'],role=run['role'],samples_sha256=run['samples']['sha256'],results_snapshot_sha256=a.sha(rp),analysis_identity=identity,final_bin_sha256=bins)
            (root/'receipt.json').write_text(json.dumps(receipt))
            run['receipt']=dict(path='receipt.json',sha256=a.sha(root/'receipt.json'))
            (root/'manifest.json').write_text(json.dumps(m))
            a.load_campaign(root/'manifest.json',root)
            (root/'new-main.csv').write_text('tampered')
            with self.assertRaises(a.Rejected):a.load_campaign(root/'manifest.json',root)

    def test_relocated_default_root_and_explicit_main_only(self):
        with tempfile.TemporaryDirectory(prefix='analysis-relocated-') as tmp:
            root=Path(tmp)
            m=campaign(root)
            samples,_,_=a.load_campaign(root/'manifest.json')
            self.assertEqual(len(samples),17820)
            m['runs']=[r for r in m['runs'] if r['role']=='main']
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaises(a.Rejected):
                a.load_campaign(root/'manifest.json',main_only_authorized=True)
            m.update(acceptance_scope='main-only-user-authorized',replay_status='cancelled-by-user')
            (root/'manifest.json').write_text(json.dumps(m))
            samples,_,_=a.load_campaign(root/'manifest.json',main_only_authorized=True)
            self.assertEqual(len(samples),12960)
            with self.assertRaises(a.Rejected):
                a.load_campaign(root/'manifest.json')

    def test_gate_missing_or_bridge(self):
        with tempfile.TemporaryDirectory(prefix='analysis-gate-fixture-') as tmp:
            root=Path(tmp);campaign(root)
            gate=json.loads((root/'gates.json').read_text())
            bad=copy.deepcopy(gate);bad['gates'].pop()
            with self.assertRaises(a.Rejected):a.validate_gates(bad)
            bad=copy.deepcopy(gate)
            next(r for r in bad['gates'] if r['family']=='new')['stack_bridge']=True
            with self.assertRaises(a.Rejected):a.validate_gates(bad)

if __name__=='__main__':unittest.main()
